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
  if not file then
    return false
  end

  file:close()
  return true
end

local function write_file(path, content)
  local file, err = io.open(path, "wb")
  if not file then
    return nil, "Could not write config file: " .. tostring(err)
  end

  local ok, write_error = file:write(content)
  if not ok then
    file:close()
    return nil, "Could not write config file: " .. tostring(write_error)
  end

  local close_ok, close_error = file:close()
  if not close_ok then
    return nil, "Could not finish writing config file: " .. tostring(close_error)
  end

  return true
end

local function copy_config(source_path, target_path)
  local content = read_file(source_path)
  if not content then
    return nil, "Could not read the selected config file: " .. source_path
  end

  return write_file(target_path, content)
end

local function escape_json_string(value)
  return '"' .. tostring(value)
    :gsub("\\", "\\\\")
    :gsub('"', '\\"')
    :gsub("\b", "\\b")
    :gsub("\f", "\\f")
    :gsub("\n", "\\n")
    :gsub("\r", "\\r")
    :gsub("\t", "\\t") .. '"'
end

local function replace_json_string(content, key, value)
  local pattern = '("' .. key .. '"%s*:%s*)"[^"]*"'
  local result, replacements = content:gsub(pattern, function(prefix)
    return prefix .. escape_json_string(value)
  end, 1)

  if replacements ~= 1 then
    return nil, "The config template is missing the " .. key .. " setting."
  end

  return result
end

local function ask_for_file(title, extension)
  local ok, path = reaper.GetUserFileNameForRead("", title, extension)
  if not ok or not path or path == "" then
    return nil, "Configuration was cancelled."
  end

  return path
end

local function ask_for_python()
  local ok, values = reaper.GetUserInputs(
    "Set up outlaw actions",
    1,
    "Python executable (python if on PATH):",
    "python"
  )
  if not ok then
    return nil, "Configuration was cancelled."
  end

  local value = values:match("^%s*(.-)%s*$")
  if value == "" then
    return nil, "Python executable cannot be empty."
  end

  return value
end

local function ask_for_language()
  local ok, values = reaper.GetUserInputs(
    "Set up outlaw actions",
    1,
    "Whisper language (e.g. en or auto):",
    "en"
  )
  if not ok then
    return nil, "Configuration was cancelled."
  end

  local value = values:match("^%s*(.-)%s*$")
  if value == "" then
    return nil, "Whisper language cannot be empty."
  end

  return value
end

local function create_config(config_path, template_path)
  local template = read_file(template_path)
  if not template then
    return nil, "Config template not found: " .. template_path
  end

  local python, err = ask_for_python()
  if not python then
    return nil, err
  end

  local ffmpeg
  ffmpeg, err = ask_for_file("Select ffmpeg.exe", "exe")
  if not ffmpeg then
    return nil, err
  end

  local whisper
  whisper, err = ask_for_file("Select whisper.cpp executable", "exe")
  if not whisper then
    return nil, err
  end

  local model
  model, err = ask_for_file("Select local Whisper model", "bin")
  if not model then
    return nil, err
  end

  local language
  language, err = ask_for_language()
  if not language then
    return nil, err
  end

  local settings = {
    python_executable = python,
    ffmpeg_binary = ffmpeg,
    ffprobe_binary = "",
    whisper_binary = whisper,
    whisper_model = model,
    language = language
  }

  for key, value in pairs(settings) do
    template, err = replace_json_string(template, key, value)
    if not template then
      return nil, err
    end
  end

  local ok
  ok, err = write_file(config_path, template)
  if not ok then
    return nil, err
  end

  return config_path
end

local function ensure(config_path, template_path)
  if file_exists(config_path) then
    return config_path
  end

  local choice = reaper.ShowMessageBox(
    "No config.json was found.\n\nYes: choose an existing config file.\nNo: create one by selecting your local tools.\nCancel: stop.",
    "Set up outlaw actions",
    3
  )

  if choice == 6 then
    local selected_path, err = ask_for_file("Select config.json", "json")
    if not selected_path then
      return nil, err
    end

    local ok
    ok, err = copy_config(selected_path, config_path)
    if not ok then
      return nil, err
    end

    return config_path
  elseif choice == 7 then
    return create_config(config_path, template_path)
  end

  return nil, "Configuration was cancelled."
end

return {
  ensure = ensure
}
