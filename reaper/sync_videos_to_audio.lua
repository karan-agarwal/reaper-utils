-- Syncs one or more video files to one selected reference WAV item.
--
-- Video paths are entered as a pipe-separated list, or as @C:\path\videos.txt
-- where the manifest contains one video path per line.

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

local function write_file(path, content)
  local file = io.open(path, "wb")
  if not file then
    return nil
  end

  file:write(content)
  file:close()
  return true
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

local function selected_reference_item()
  if reaper.CountSelectedMediaItems(0) ~= 1 then
    return nil, "Select exactly one reference WAV media item before running this action."
  end

  local item = reaper.GetSelectedMediaItem(0, 0)
  local take = reaper.GetActiveTake(item)
  if not take or reaper.TakeIsMIDI(take) then
    return nil, "The selected item must contain an audio take."
  end

  local source = reaper.GetMediaItemTake_Source(take)
  local source_path = reaper.GetMediaSourceFileName(source, "")
  if not source_path or source_path == "" then
    return nil, "The selected item's source file could not be resolved."
  end

  if not source_path:lower():match("%.wav$") then
    return nil, "The selected reference item must use a WAV source file."
  end

  local playrate = reaper.GetMediaItemTakeInfo_Value(take, "D_PLAYRATE")
  if math.abs(playrate - 1.0) > 0.0001 then
    return nil, "The selected item's take playrate must be 1.0 for sync alignment."
  end

  return {
    item = item,
    take = take,
    source_path = source_path,
    position = reaper.GetMediaItemInfo_Value(item, "D_POSITION"),
    duration = reaper.GetMediaItemInfo_Value(item, "D_LENGTH"),
    start_offset = reaper.GetMediaItemTakeInfo_Value(take, "D_STARTOFFS"),
    track = reaper.GetMediaItem_Track(item)
  }
end

local function ensure_config()
  local config = read_file(CONFIG_PATH)
  if not config then
    return nil, "Config file not found: " .. CONFIG_PATH
  end

  local python = parse_json_string(config, "python_executable") or "python"
  local worker = parse_json_string(config, "video_sync_worker_script")
  if not worker or worker == "" then
    worker = SCRIPT_DIR .. "..\\worker\\sync_videos.py"
  end

  return {
    python = python,
    worker = worker
  }
end

local function make_output_dir()
  local resource_path = reaper.GetResourcePath()
  local sep = package.config:sub(1, 1)
  local output_dir = resource_path .. sep .. "reaper-offline-transcript"
  reaper.RecursiveCreateDirectory(output_dir, 0)
  return output_dir
end

local function trim(value)
  return tostring(value):match("^%s*(.-)%s*$")
end

local function strip_quotes(value)
  return trim(value):gsub('^"', ""):gsub('"$', "")
end

local function split_video_input(value)
  local videos = {}
  for path in tostring(value):gmatch("[^|]+") do
    local cleaned = strip_quotes(path)
    if cleaned ~= "" then
      table.insert(videos, cleaned)
    end
  end

  return videos
end

local function collect_videos_manifest(output_dir)
  local ok, input = reaper.GetUserInputs(
    "Sync Videos to Audio",
    1,
    "Video paths separated by | or @manifest.txt",
    ""
  )
  if not ok then
    return nil, "Video selection was cancelled."
  end

  input = trim(input)
  if input == "" then
    return nil, "No video files were provided."
  end

  if input:sub(1, 1) == "@" then
    local manifest_path = strip_quotes(input:sub(2))
    if not read_file(manifest_path) then
      return nil, "Video manifest could not be read: " .. manifest_path
    end
    return manifest_path
  end

  local videos = split_video_input(input)
  if #videos == 0 then
    return nil, "No video files were provided."
  end

  local content = table.concat(videos, "\n") .. "\n"
  local manifest_path = output_dir .. package.config:sub(1, 1) .. "video_sync_inputs.txt"
  if not write_file(manifest_path, content) then
    return nil, "Could not write video manifest: " .. manifest_path
  end

  return manifest_path
end

local function run_worker(config, source, videos_manifest, output_dir)
  local inner_command = table.concat({
    quote_arg(config.python),
    quote_arg(config.worker),
    "--config", quote_arg(CONFIG_PATH),
    "--reference", quote_arg(source.source_path),
    "--videos-file", quote_arg(videos_manifest),
    "--out-dir", quote_arg(output_dir),
    "--reference-start", string.format("%.6f", source.start_offset),
    "--reference-duration", string.format("%.6f", source.duration),
    "--reference-project-position", string.format("%.6f", source.position)
  }, " ")

  local command = 'cmd.exe /C "' .. inner_command .. '"'
  local result = reaper.ExecProcess(command, 0)
  if not result then
    return nil, "Could not start video sync worker."
  end

  local exit_code = tonumber(result:match("^(%-?%d+)"))
  if exit_code ~= 0 then
    return nil, "Video sync worker failed with exit code " .. tostring(exit_code) .. ":\n" .. result
  end

  local tsv_path = output_dir .. package.config:sub(1, 1) .. "video_sync_results.tsv"
  if not read_file(tsv_path) then
    return nil, "Worker did not create sync results TSV: " .. tsv_path
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

local function read_results(tsv_path)
  local file = io.open(tsv_path, "rb")
  if not file then
    return nil, "Unable to read sync results TSV."
  end

  local results = {}
  local line_number = 0
  for line in file:lines() do
    line_number = line_number + 1
    if line_number > 1 and line ~= "" then
      local fields = split_tsv_line(line)
      if #fields < 6 then
        file:close()
        return nil, "Malformed sync results TSV at line " .. tostring(line_number) .. "."
      end

      table.insert(results, {
        video_path = unescape_tsv(fields[1]),
        status = fields[2],
        insert_position = tonumber(fields[3]),
        offset = fields[4],
        confidence = tonumber(fields[5]) or 0,
        reason = unescape_tsv(fields[6])
      })
    end
  end

  file:close()
  return results
end

local function create_track_below(source_track, offset, name)
  local source_index = reaper.GetMediaTrackInfo_Value(source_track, "IP_TRACKNUMBER")
  local insert_index = source_index + offset
  reaper.InsertTrackAtIndex(insert_index, true)

  local track = reaper.GetTrack(0, insert_index)
  reaper.GetSetMediaTrackInfo_String(track, "P_NAME", name, true)
  return track
end

local function snapshot_track_items(track)
  local items = {}
  local count = reaper.CountTrackMediaItems(track)
  for index = 0, count - 1 do
    local item = reaper.GetTrackMediaItem(track, index)
    local ok, guid = reaper.GetSetMediaItemInfo_String(item, "GUID", "", false)
    if ok and guid then
      items[guid] = true
    end
  end
  return items
end

local function find_new_track_item(track, before_items)
  local count = reaper.CountTrackMediaItems(track)
  for index = 0, count - 1 do
    local item = reaper.GetTrackMediaItem(track, index)
    local ok, guid = reaper.GetSetMediaItemInfo_String(item, "GUID", "", false)
    if ok and guid and not before_items[guid] then
      return item
    end
  end
  return nil
end

local function insert_video_item(track, result)
  reaper.SetOnlyTrackSelected(track)
  reaper.SelectAllMediaItems(0, false)
  reaper.SetEditCurPos(result.insert_position, false, false)

  local before_items = snapshot_track_items(track)
  reaper.InsertMedia(result.video_path, 0)
  local item = find_new_track_item(track, before_items)
  local selected_item = reaper.GetSelectedMediaItem(0, 0)
  local placeholder = false

  if selected_item then
    local selected_track = reaper.GetMediaItem_Track(selected_item)
    if selected_track == track and not item then
      item = selected_item
    elseif selected_track ~= track then
      reaper.DeleteTrackMediaItem(selected_track, selected_item)
    end
  end

  if not item then
    item = reaper.AddMediaItemToTrack(track)
    reaper.SetMediaItemInfo_Value(item, "D_POSITION", result.insert_position)
    reaper.SetMediaItemInfo_Value(item, "D_LENGTH", 5.0)
    placeholder = true
  end

  reaper.SetMediaItemInfo_Value(item, "D_POSITION", result.insert_position)
  local note = string.format(
    "Auto-sync status: %s\nConfidence: %.3f\nOffset: %s\nReason: %s\nVideo: %s%s",
    result.status,
    result.confidence,
    result.offset ~= "" and result.offset or "none",
    result.reason ~= "" and result.reason or "none",
    result.video_path,
    placeholder and "\nPlaceholder: REAPER could not insert this media file automatically." or ""
  )
  reaper.GetSetMediaItemInfo_String(item, "P_NOTES", note, true)
  if placeholder then
    reaper.GetSetMediaItemInfo_String(item, "P_NAME", "Manual placeholder: " .. result.video_path, true)
  end

  return item, placeholder
end

local function main()
  local source, source_error = selected_reference_item()
  if not source then
    message("Sync Videos to Audio", source_error)
    return
  end

  local config, config_error = ensure_config()
  if not config then
    message("Sync Videos to Audio", config_error)
    return
  end

  local output_dir = make_output_dir()
  local videos_manifest, manifest_error = collect_videos_manifest(output_dir)
  if not videos_manifest then
    message("Sync Videos to Audio", manifest_error)
    return
  end

  local tsv_path, worker_error = run_worker(config, source, videos_manifest, output_dir)
  if not tsv_path then
    message("Sync Videos to Audio", worker_error)
    return
  end

  local results, results_error = read_results(tsv_path)
  if not results then
    message("Sync Videos to Audio", results_error)
    return
  end

  if #results == 0 then
    message("Sync Videos to Audio", "No video sync results were produced.")
    return
  end

  reaper.Undo_BeginBlock()
  reaper.PreventUIRefresh(1)

  local synced_track = create_track_below(source.track, 0, "Synced Videos")
  local manual_track = create_track_below(source.track, 1, "Manual Sync Videos")

  local synced_count = 0
  local manual_count = 0
  local placeholder_count = 0
  for _, result in ipairs(results) do
    if result.status == "synced" then
      local item, placeholder = insert_video_item(synced_track, result)
      if item then
        synced_count = synced_count + 1
        if placeholder then
          placeholder_count = placeholder_count + 1
        end
      end
    else
      local item, placeholder = insert_video_item(manual_track, result)
      if item then
        manual_count = manual_count + 1
        if placeholder then
          placeholder_count = placeholder_count + 1
        end
      end
    end
  end

  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock("Sync videos to audio", -1)

  message(
    "Sync Videos to Audio",
    string.format(
      "Inserted %d synced video(s) and %d manual-sync video(s). Created %d placeholder item(s). REAPER may continue peak analysis in the background.",
      synced_count,
      manual_count,
      placeholder_count
    )
  )
end

main()
