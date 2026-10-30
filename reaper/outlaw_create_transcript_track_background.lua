-- Launches transcript generation in the background so REAPER stays responsive.
--
-- This script writes a run manifest, starts one worker process per selected WAV
-- item, and returns immediately. Use outlaw_import_transcript_track.lua later to build
-- the transcript track from completed TSV outputs.
-- @description outlaw: Transcribe selected WAV media in the background

local SCRIPT_DIR = ({reaper.get_action_context()})[2]:match("^(.*[\\/])")
local REPO_DIR = SCRIPT_DIR:match("^(.*[\\/])[^\\/]+[\\/]$") or SCRIPT_DIR
local CONFIG_PATH = REPO_DIR .. "config.json"
local config_setup = dofile(SCRIPT_DIR .. "config_setup.lua")

local function message(title, text)
  reaper.ShowMessageBox(text, title, 0)
end

local function quote_arg(value)
  return '"' .. tostring(value):gsub('"', '\\"') .. '"'
end

local function read_file(path)
  local file = io.open(path, "rb")
  if not file then
    return nil
  end

  local content = file:read("*all")
  file:close()
  return content
end

local function file_exists(path)
  local file = io.open(path, "rb")
  if file then
    file:close()
    return true
  end

  return false
end

local function escape_tsv(value)
  return tostring(value)
    :gsub("\\", "\\\\")
    :gsub("\t", "\\t")
    :gsub("\n", "\\n")
end

local function selected_wav_items()
  local count = reaper.CountSelectedMediaItems(0)
  if count == 0 then
    return nil, "Select at least one WAV media item before running this action."
  end

  local items = {}
  for i = 0, count - 1 do
    local item = reaper.GetSelectedMediaItem(0, i)
    local take = reaper.GetActiveTake(item)
    if not take or reaper.TakeIsMIDI(take) then
      return nil, string.format("Item %d must contain an audio take.", i + 1)
    end

    local source = reaper.GetMediaItemTake_Source(take)
    local source_path = reaper.GetMediaSourceFileName(source, "")
    if not source_path or source_path == "" then
      return nil, string.format("Item %d source file could not be resolved.", i + 1)
    end

    if not source_path:lower():match("%.wav$") then
      return nil, string.format("Item %d must use a WAV source file.", i + 1)
    end

    local playrate = reaper.GetMediaItemTakeInfo_Value(take, "D_PLAYRATE")
    if math.abs(playrate - 1.0) > 0.0001 then
      return nil, string.format("Item %d take playrate must be 1.0 for transcript alignment.", i + 1)
    end

    table.insert(items, {
      source_path = source_path,
      position = reaper.GetMediaItemInfo_Value(item, "D_POSITION"),
      duration = reaper.GetMediaItemInfo_Value(item, "D_LENGTH"),
      start_offset = reaper.GetMediaItemTakeInfo_Value(take, "D_STARTOFFS"),
      track_index = reaper.GetMediaTrackInfo_Value(reaper.GetMediaItem_Track(item), "IP_TRACKNUMBER"),
      track_guid = select(2, reaper.GetSetMediaTrackInfo_String(reaper.GetMediaItem_Track(item), "GUID", "", false)) or "",
    })
  end

  return items
end

local function ensure_config()
  local config_path, setup_error = config_setup.ensure(CONFIG_PATH, REPO_DIR .. "config.example.json")
  if not config_path then
    return nil, setup_error
  end

  local config = read_file(CONFIG_PATH)
  if not config then
    return nil, "Config file not found: " .. CONFIG_PATH
  end

  local python = config:match('\"python_executable\"%s*:%s*\"([^\"]*)\"') or "python"
  local worker = config:match('\"worker_script\"%s*:%s*\"([^\"]*)\"')
  if not worker or worker == "" then
    worker = SCRIPT_DIR .. "..\\worker\\offline_transcribe.py"
  end

  -- Derive the background launcher path from the worker script path.
  -- worker is e.g. C:\repo\worker\offline_transcribe.py
  local worker_dir = worker:match("^(.*[\\/])[^\\/]+$") or ""
  local launcher = worker_dir .. "background_launcher.py"

  return {
    python = python,
    worker = worker,
    launcher = launcher,
  }
end

local function make_output_dir()
  local resource_path = reaper.GetResourcePath()
  local sep = package.config:sub(1, 1)
  local output_dir = resource_path .. sep .. "reaper-offline-transcript"
  reaper.RecursiveCreateDirectory(output_dir, 0)
  return output_dir
end

local function make_run_dir(output_dir)
  local sep = package.config:sub(1, 1)
  local runs_dir = output_dir .. sep .. "runs"
  reaper.RecursiveCreateDirectory(runs_dir, 0)

  local now_us = math.floor((reaper.time_precise() * 1000000) % 1000000)
  local name = os.date("run_%Y%m%d_%H%M%S") .. string.format("_%06d", now_us)
  local candidate = runs_dir .. sep .. name
  reaper.RecursiveCreateDirectory(candidate, 0)
  return candidate
end

local function write_manifest(run_dir, sources)
  local sep = package.config:sub(1, 1)
  local manifest_path = run_dir .. sep .. "manifest.tsv"
  local file = io.open(manifest_path, "w")
  if not file then
    return nil, "Could not write run manifest: " .. manifest_path
  end

  file:write("item_index\ttrack_index\tposition\tstart_offset\tduration\tsource_path\titem_dir\ttrack_guid\n")
  for index, source in ipairs(sources) do
    local item_dir = run_dir .. sep .. string.format("item_%04d", index)
    reaper.RecursiveCreateDirectory(item_dir, 0)
    file:write(table.concat({
      tostring(index),
      string.format("%.0f", source.track_index),
      string.format("%.6f", source.position),
      string.format("%.6f", source.start_offset),
      string.format("%.6f", source.duration),
      escape_tsv(source.source_path),
      escape_tsv(item_dir),
      escape_tsv(source.track_guid),
    }, "\t"))
    file:write("\n")
  end

  file:close()
  return manifest_path
end

local function write_latest_run(output_dir, run_dir)
  local sep = package.config:sub(1, 1)
  local latest_path = output_dir .. sep .. "latest_run.txt"
  local file = io.open(latest_path, "w")
  if not file then
    return nil, "Could not update latest run file: " .. latest_path
  end

  file:write(run_dir .. "\n")
  file:close()
  return latest_path
end

local function write_run_meta(run_dir)
  local sep = package.config:sub(1, 1)
  local meta_path = run_dir .. sep .. "run_meta.txt"
  local file = io.open(meta_path, "w")
  if not file then
    return nil, "Could not write run metadata: " .. meta_path
  end

  local _, project_path = reaper.EnumProjects(-1, "")
  file:write("project_path=" .. (project_path or "") .. "\n")
  file:close()
  return meta_path
end

local function launch_worker(config, source, item_dir, item_index, total_items)
  local log_path = item_dir .. package.config:sub(1, 1) .. "worker.log"

  -- Call the Python launcher, which spawns the real worker as a fully detached
  -- Windows process and exits in under a second. ExecProcess(cmd, 0) blocks on
  -- the launcher, not on the transcription job, so REAPER unfreezes quickly.
  local command = table.concat({
    quote_arg(config.python),
    quote_arg(config.launcher),
    "--config", quote_arg(CONFIG_PATH),
    "--wav", quote_arg(source.source_path),
    "--out-dir", quote_arg(item_dir),
    "--log-file", quote_arg(log_path),
    "--clip-start", string.format("%.6f", source.start_offset),
    "--clip-duration", string.format("%.6f", source.duration),
    "--item-index", tostring(item_index),
    "--total-items", tostring(total_items),
  }, " ")

  local result = reaper.ExecProcess(command, 0)
  if not result then
    return nil, "Could not start background transcription launcher."
  end

  local exit_code = tonumber(result:match("^(%-?%d+)"))
  if exit_code ~= 0 then
    return nil, string.format(
      "Background launcher failed (exit %d). Check: %s", exit_code, log_path
    )
  end

  return log_path
end

local function main()
  local config, config_error = ensure_config()
  if not config then
    message("Create Transcript Track", config_error)
    return
  end

  local sources, source_error = selected_wav_items()
  if not sources then
    message("Create Transcript Track", source_error)
    return
  end

  local output_dir = make_output_dir()
  local run_dir, run_error = make_run_dir(output_dir)
  if not run_dir then
    message("Create Transcript Track", run_error)
    return
  end

  local manifest_path, manifest_error = write_manifest(run_dir, sources)
  if not manifest_path then
    message("Create Transcript Track", manifest_error)
    return
  end

  local meta_path, meta_error = write_run_meta(run_dir)
  if not meta_path then
    message("Create Transcript Track", meta_error)
    return
  end

  local latest_path, latest_error = write_latest_run(output_dir, run_dir)
  if not latest_path then
    message("Create Transcript Track", latest_error)
    return
  end

  for index, source in ipairs(sources) do
    local item_dir = run_dir .. package.config:sub(1, 1) .. string.format("item_%04d", index)
    local _, worker_error = launch_worker(config, source, item_dir, index, #sources)
    if worker_error then
      message("Create Transcript Track", string.format("Item %d: %s", index, worker_error))
      return
    end
  end

  reaper.ShowConsoleMsg(string.format(
    "Launched %d transcription job(s) in background.\nRun folder: %s\nWhen complete, run outlaw_import_transcript_track.lua to build the transcript track.\n",
    #sources,
    run_dir
  ))
end

main()
