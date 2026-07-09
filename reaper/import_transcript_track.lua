-- Imports completed transcript TSV files from the latest background run.
--
-- Use this after create_transcript_track_background.lua has finished. The
-- script scans the latest run manifest, verifies that all transcript TSV files
-- exist, and then creates the Transcript track from the finished results.

local SCRIPT_DIR = ({reaper.get_action_context()})[2]:match("^(.*[\\/])")
local REPO_DIR = SCRIPT_DIR:match("^(.*[\\/])[^\\/]+[\\/]$") or SCRIPT_DIR
local OUTPUT_DIR = reaper.GetResourcePath() .. package.config:sub(1, 1) .. "reaper-offline-transcript"

local function message(title, text)
  reaper.ShowMessageBox(text, title, 0)
end

local function read_file(path)
  local file = io.open(path, "rb")
  if not file then
    return nil
  end

  local function read_kv_file(path)
    local content = read_file(path)
    if not content then
      return nil
    end

    local data = {}
    for line in content:gmatch("[^\r\n]+") do
      local key, value = line:match("^(.-)=(.*)$")
      if key then
        data[key] = value
      end
    end

    return data
  end

  local content = file:read("*all")
  file:close()
  return content
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

local function parse_manifest(manifest_path)
  local file = io.open(manifest_path, "rb")
  if not file then
    return nil, "Unable to read run manifest: " .. manifest_path
  end

  local rows = {}
  local line_number = 0
  for line in file:lines() do
    line_number = line_number + 1
    if line_number > 1 and line ~= "" then
      local fields = split_tsv_line(line)
      if #fields < 8 then
        file:close()
        return nil, "Malformed manifest TSV at line " .. tostring(line_number) .. "."
      end

      table.insert(rows, {
        item_index = tonumber(fields[1]),
        track_index = tonumber(fields[2]),
        position = tonumber(fields[3]),
        start_offset = tonumber(fields[4]),
        duration = tonumber(fields[5]),
        source_path = unescape_tsv(fields[6]),
        item_dir = unescape_tsv(fields[7]),
        track_guid = unescape_tsv(fields[8]),
      })
    end
  end

  file:close()
  return rows
end

local function read_segments(tsv_path)
  local file = io.open(tsv_path, "rb")
  if not file then
    return nil, "Unable to read transcript TSV: " .. tsv_path
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
        text = unescape_tsv(fields[5]),
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

local function find_track_by_guid(guid)
  if not guid or guid == "" then
    return nil
  end

  local track_count = reaper.CountTracks(0)
  for index = 0, track_count - 1 do
    local track = reaper.GetTrack(0, index)
    if track then
      local _, current_guid = reaper.GetSetMediaTrackInfo_String(track, "GUID", "", false)
      if current_guid == guid then
        return track
      end
    end
  end

  return nil
end

local function find_latest_manifest()
  local sep = package.config:sub(1, 1)
  local latest_run_file = OUTPUT_DIR .. sep .. "latest_run.txt"
  local latest_run = read_file(latest_run_file)
  if latest_run then
    latest_run = latest_run:gsub("%s+$", "")
    if latest_run ~= "" then
      local manifest = latest_run .. sep .. "manifest.tsv"
      if read_file(manifest) then
        return manifest
      end
    end
  end

  local runs_dir = OUTPUT_DIR .. sep .. "runs"
  local p = io.popen('cmd.exe /C dir /b /ad /o-d "' .. runs_dir .. '" 2>nul')
  if not p then
    return nil, "No run manifest found."
  end

  for folder in p:lines() do
    local manifest = runs_dir .. sep .. folder .. sep .. "manifest.tsv"
    if read_file(manifest) then
      p:close()
      return manifest
    end
  end
  p:close()
  return nil, "No completed transcript run found."
end

local function current_project_path()
  local _, path = reaper.EnumProjects(-1, "")
  return path or ""
end

local function main()
  local manifest_path, manifest_error = find_latest_manifest()
  if not manifest_path then
    message("Import Transcript Track", manifest_error)
    return
  end

  local manifest, parse_error = parse_manifest(manifest_path)
  if not manifest then
    message("Import Transcript Track", parse_error)
    return
  end

  if #manifest == 0 then
    message("Import Transcript Track", "The latest run manifest is empty.")
    return
  end

  local manifest_dir = manifest_path:match("^(.*[\\/])[^\\/]+[\\/]manifest%.tsv$")
  local meta = manifest_dir and read_kv_file(manifest_dir .. "run_meta.txt") or nil
  local run_project_path = meta and meta.project_path or ""
  local current_path = current_project_path()
  if run_project_path ~= "" and run_project_path ~= current_path then
    message(
      "Import Transcript Track",
      "The latest transcription run belongs to a different project.\n\nRun project:\n" .. run_project_path .. "\n\nCurrent project:\n" .. current_path
    )
    return
  end

  local missing = {}
  local all_items = {}
  for _, row in ipairs(manifest) do
    local tsv_path = row.item_dir .. package.config:sub(1, 1) .. "transcript.tsv"
    local segments, segment_error = read_segments(tsv_path)
    if not segments then
      table.insert(missing, string.format("Item %d: %s", row.item_index or -1, segment_error))
    else
      for _, segment in ipairs(segments) do
        table.insert(all_items, {
          segment = segment,
          source = row,
        })
      end
    end
  end

  if #missing > 0 then
    message("Import Transcript Track", "Some transcript results are not ready yet:\n\n" .. table.concat(missing, "\n"))
    return
  end

  table.sort(all_items, function(left, right)
    local a = left.source.position + left.segment.start_seconds
    local b = right.source.position + right.segment.start_seconds
    return a < b
  end)

  local source_track = find_track_by_guid(manifest[1].track_guid)
    or reaper.GetTrack(0, (manifest[1].track_index or 1) - 1)
  if not source_track then
    message("Import Transcript Track", "Could not find the source track for the latest run.")
    return
  end

  reaper.Undo_BeginBlock()
  reaper.PreventUIRefresh(1)

  local transcript_track = create_transcript_track(source_track)
  for _, entry in ipairs(all_items) do
    local segment = entry.segment
    local source = entry.source
    local start_pos = source.position + segment.start_seconds
    local length = math.max(0.25, segment.end_seconds - segment.start_seconds)
    local text = segment.emoji .. " " .. segment.text
    create_text_item(transcript_track, start_pos, length, text)
  end

  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock("Import transcript track", -1)
end

main()
