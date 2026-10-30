-- Creates a transcript track from one or more selected WAV media items.
--
-- Install this file as a REAPER ReaScript action. The script calls the local
-- Python worker configured in config.json for each selected item, then creates
-- empty transcript items on a single fresh track below the first source track.
-- @description outlaw: Create transcript items from selected WAV media
-- @version 1.0.1
-- @author Karan Agarwal
-- @about Offline transcript and video-sync actions for REAPER.
-- @metapackage
-- @provides
--   [main] .
--   [main] outlaw_create_transcript_track_background.lua
--   [main] outlaw_import_transcript_track.lua
--   [main] outlaw_sync_videos_to_audio.lua
--   [nomain] ../config.example.json
--   [nomain] ../worker/*.py

local SCRIPT_DIR = ({reaper.get_action_context()})[2]:match("^(.*[\\/])")
local REPO_DIR = SCRIPT_DIR:match("^(.*[\\/])[^\\/]+[\\/]$") or SCRIPT_DIR
local CONFIG_PATH = REPO_DIR .. "config.json"

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

local function unescape_json_string(value)
  if not value then
    return nil
  end

  local backslash_placeholder = string.char(31)
  return value
    :gsub("\\\\", backslash_placeholder)
    :gsub('\\"', '"')
    :gsub("\\/", "/")
    :gsub("\\n", "\n")
    :gsub("\\r", "\r")
    :gsub("\\t", "\t")
    :gsub(backslash_placeholder, "\\")
end

local function parse_json_string(content, key)
  local pattern = '"' .. key .. '"%s*:%s*"([^"]*)"'
  return unescape_json_string(content:match(pattern))
end

local function parse_json_number(content, key)
  local pattern = '"' .. key .. '"%s*:%s*([%d%.]+)'
  local value = content:match(pattern)
  return value and tonumber(value) or nil
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
      item = item,
      take = take,
      source_path = source_path,
      position = reaper.GetMediaItemInfo_Value(item, "D_POSITION"),
      duration = reaper.GetMediaItemInfo_Value(item, "D_LENGTH"),
      start_offset = reaper.GetMediaItemTakeInfo_Value(take, "D_STARTOFFS"),
      track = reaper.GetMediaItem_Track(item)
    })
  end

  return items
end

local function ensure_config()
  local config = read_file(CONFIG_PATH)
  if not config then
    return nil, "Config file not found: " .. CONFIG_PATH
  end

  local python = parse_json_string(config, "python_executable") or "python"
  local worker = parse_json_string(config, "worker_script")
  if not worker or worker == "" then
    worker = SCRIPT_DIR .. "..\\worker\\offline_transcribe.py"
  end

  return {
    python = python,
    worker = worker,
    min_item_duration = parse_json_number(config, "min_item_duration_seconds") or 0.25
  }
end

local function make_output_dir()
  local resource_path = reaper.GetResourcePath()
  local sep = package.config:sub(1, 1)
  local output_dir = resource_path .. sep .. "reaper-offline-transcript"
  reaper.RecursiveCreateDirectory(output_dir, 0)
  return output_dir
end

local function run_worker(config, source, output_dir, item_index, total_items)
  local sep = package.config:sub(1, 1)
  local item_dir = output_dir .. sep .. string.format("item_%04d", item_index)
  reaper.RecursiveCreateDirectory(item_dir, 0)

  -- Remove any stale TSV from a previous run so we never silently accept old output.
  local stale_tsv = item_dir .. sep .. "transcript.tsv"
  os.remove(stale_tsv)

  local inner_command = table.concat({
    quote_arg(config.python),
    quote_arg(config.worker),
    "--config", quote_arg(CONFIG_PATH),
    "--wav", quote_arg(source.source_path),
    "--out-dir", quote_arg(item_dir),
    "--clip-start", string.format("%.6f", source.start_offset),
    "--clip-duration", string.format("%.6f", source.duration),
    "--item-index", tostring(item_index),
    "--total-items", tostring(total_items)
  }, " ")

  -- Write a small batch file so ExecProcess can open a visible progress window
  -- while REAPER is blocked. On failure the window stays open for inspection.
  local bat_path = item_dir .. sep .. "run_transcribe.bat"
  local bat_f = io.open(bat_path, "w")
  if not bat_f then
    return nil, "Could not write batch file: " .. bat_path
  end
  bat_f:write("@echo off\r\n")
  bat_f:write(inner_command .. "\r\n")
  bat_f:write("set EXIT_CODE=%ERRORLEVEL%\r\n")
  bat_f:write("if %EXIT_CODE% NEQ 0 (\r\n")
  bat_f:write("    echo.\r\n")
  bat_f:write("    echo Transcription failed. Review the output above.\r\n")
  bat_f:write("    echo Press any key to close this window.\r\n")
  bat_f:write("    pause > nul\r\n")
  bat_f:write(")\r\n")
  bat_f:write("exit /b %EXIT_CODE%\r\n")
  bat_f:close()

  local title = string.format("REAPER Transcriber  [Item %d of %d]", item_index, total_items)
  local command = 'cmd.exe /C start /wait "' .. title .. '" "' .. bat_path .. '"'
  local result = reaper.ExecProcess(command, 0)
  if not result then
    return nil, "Could not start transcription worker."
  end

  local exit_code = tonumber(result:match("^(%-?%d+)"))
  if exit_code ~= 0 then
    return nil, string.format(
      "Transcription worker failed with exit code %d.\nSee the console window for details.",
      exit_code
    )
  end

  local tsv_path = item_dir .. sep .. "transcript.tsv"
  if not read_file(tsv_path) then
    return nil, "Worker did not create transcript TSV: " .. tsv_path
  end

  return tsv_path
end

local function split_tsv_line(line)
  local fields = {}
  local start = 1

  while true do
    local tab_start, tab_end = line:find("\t", start, true)
    if not tab_start then
      table.insert(fields, line:sub(start))
      break
    end

    table.insert(fields, line:sub(start, tab_start - 1))
    start = tab_end + 1
  end

  return fields
end

local function unescape_tsv(value)
  local backslash_placeholder = string.char(31)
  return value
    :gsub("\\\\", backslash_placeholder)
    :gsub("\\t", "\t")
    :gsub("\\n", "\n")
    :gsub(backslash_placeholder, "\\")
end

local function read_segments(tsv_path)
  local file = io.open(tsv_path, "rb")
  if not file then
    return nil, "Unable to read transcript TSV."
  end

  local segments = {}
  local line_number = 0

  for line in file:lines() do
    line_number = line_number + 1
    if line_number > 1 and line ~= "" then
      local fields = split_tsv_line(line)
      if #fields < 5 then
        file:close()
        return nil, "Malformed transcript TSV at line " .. tostring(line_number) .. "."
      end

      local start_sec = tonumber(fields[1])
      local end_sec = tonumber(fields[2])
      if not start_sec or not end_sec or end_sec <= start_sec then
        file:close()
        return nil, "Invalid timestamps in transcript TSV at line " .. tostring(line_number) .. "."
      end

      table.insert(segments, {
        start_seconds = start_sec,
        end_seconds = end_sec,
        speaker = fields[3],
        emoji = fields[4],
        text = unescape_tsv(fields[5])
      })
    end
  end

  file:close()
  return segments
end

local function create_transcript_track(source_track)
  local source_index = reaper.GetMediaTrackInfo_Value(source_track, "IP_TRACKNUMBER")
  reaper.InsertTrackAtIndex(source_index, true)

  local transcript_track = reaper.GetTrack(0, source_index)
  reaper.GetSetMediaTrackInfo_String(transcript_track, "P_NAME", "Transcript", true)
  return transcript_track
end

local function create_text_item(track, position, length, text)
  local item = reaper.AddMediaItemToTrack(track)
  reaper.SetMediaItemInfo_Value(item, "D_POSITION", position)
  reaper.SetMediaItemInfo_Value(item, "D_LENGTH", length)
  reaper.GetSetMediaItemInfo_String(item, "P_NOTES", text, true)
  reaper.GetSetMediaItemInfo_String(item, "P_NAME", text, true)
  return item
end

local function main()
  local sources, source_error = selected_wav_items()
  if not sources then
    message("Create Transcript Track", source_error)
    return
  end

  local config, config_error = ensure_config()
  if not config then
    message("Create Transcript Track", config_error)
    return
  end

  local output_dir = make_output_dir()

  local all_items = {}
  for index, source in ipairs(sources) do
    local tsv_path, worker_error = run_worker(config, source, output_dir, index, #sources)
    if not tsv_path then
      message("Create Transcript Track", string.format("Item %d: %s", index, worker_error))
      return
    end

    local segments, segment_error = read_segments(tsv_path)
    if not segments then
      message("Create Transcript Track", string.format("Item %d: %s", index, segment_error))
      return
    end

    for _, segment in ipairs(segments) do
      table.insert(all_items, { segment = segment, source = source })
    end
  end

  if #all_items == 0 then
    message("Create Transcript Track", "No transcript segments were produced.")
    return
  end

  reaper.Undo_BeginBlock()
  reaper.PreventUIRefresh(1)

  local transcript_track = create_transcript_track(sources[1].track)
  for _, entry in ipairs(all_items) do
    local segment = entry.segment
    local source = entry.source
    local start_pos = source.position + segment.start_seconds
    local length = math.max(config.min_item_duration, segment.end_seconds - segment.start_seconds)
    local text = segment.emoji .. " " .. segment.text
    create_text_item(transcript_track, start_pos, length, text)
  end

  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock("Create transcript track", -1)
end

main()