# Installation

## 1. Install offline dependencies

Install or build `whisper.cpp` on the machine that will run REAPER. Download the Whisper model you want to use and keep it on local disk.

- For **English-only** audio, `ggml-base.en.bin` (~141 MB) is fast and accurate.
- For **Hindi, Urdu, or mixed-language** audio, use a multilingual model: `ggml-small.bin` (~465 MB) or larger. Set `"language": "auto"` in `config.json` so whisper detects the language per chunk.

Install FFmpeg locally as well. It is used only to prepare the selected WAV region as 16 kHz mono audio for `whisper.cpp`.

At runtime the tool does not need internet access.

## 2. Configure paths

Copy the example config:

```powershell
Copy-Item config.example.json config.json
```

Edit `config.json`. Use **absolute Windows paths** with escaped backslashes:

```json
{
  "python_executable": "C:\\Python314\\python.exe",
  "worker_script": "C:\\workplace\\reaper-utils\\worker\\offline_transcribe.py",
  "video_sync_worker_script": "C:\\workplace\\reaper-utils\\worker\\sync_videos.py",
  "ffmpeg_binary": "C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe",
  "ffprobe_binary": "C:\\Tools\\ffmpeg\\bin\\ffprobe.exe",
  "whisper_binary": "C:\\Tools\\whisper.cpp\\Release\\whisper-cli.exe",
  "whisper_model": "C:\\Models\\whisper\\ggml-small.bin",
  "language": "auto",
  "max_audio_chunk_seconds": 600,
  "max_chars_per_item": 280,
  "min_item_duration_seconds": 1.0,
  "highlight_word": "next",
  "highlight_emoji": "➡️",
  "speaker_emoji_pool": ["🦊", "🐼", "🐸", "🐧", "🐵", "🐯", "🐨", "🐰", "🦁", "🐙"]
}
```

`ffprobe_binary` is optional when `ffprobe.exe` is next to `ffmpeg.exe`, but setting it explicitly is recommended for large WAV/RF64 files. Newer `whisper.cpp` builds usually produce `whisper-cli.exe`; older builds may use `main.exe`.

> **Important:** Save `config.json` without a UTF-8 BOM. In PowerShell, use `Set-Content -Encoding utf8NoBOM` or edit with an editor that supports BOM-free UTF-8.

## 3. Install the REAPER action

1. Open REAPER.
2. Go to **Actions > Show action list**.
3. Choose **ReaScript: New/load**.
4. Select `reaper/create_transcript_track.lua`.
5. Optionally bind it to a toolbar button or shortcut.

## 4. Run

1. Select one or more WAV media items in the project.
2. Run the `create_transcript_track.lua` action.
3. A titled console window opens for each item — watch it for chunk-by-chunk progress, elapsed time, and ETA. REAPER's UI will be unresponsive while the worker runs; this is expected.
4. Once all items are processed the window(s) close automatically and a new `Transcript` track appears below the source track.

The selected item can be trimmed; only the audible selected item region is sent to transcription. The current version requires the take playrate to be `1.0` so transcript timing stays exact.

## Large WAV handling

Large uncompressed WAV files are processed in bounded chunks. The worker extracts and transcribes one chunk at a time, then deletes the temporary 16 kHz chunk WAV before moving to the next chunk.

The default chunk size is 600 seconds:

```json
{
  "max_audio_chunk_seconds": 600
}
```

Lower this value if the machine has limited memory. Increase it if you prefer fewer `whisper.cpp` invocations and have enough RAM.

## Error recovery and resume

If a chunk fails (for example, due to a `whisper.cpp` encoding error on non-ASCII text), the worker inserts a visible error-marker item in the transcript at the correct time position and continues with the remaining chunks. The job does not abort on a single chunk failure.

On re-run, the worker detects any `whisper_NNNN.json` files left on disk from a previous run and skips those chunks automatically. Only new or previously failed chunks are re-transcribed. This means you can safely re-run the action after a partial failure without paying the cost of re-transcribing what already succeeded.

To force a full re-transcription, delete the `chunks/` subdirectory inside the relevant `item_NNNN/` output folder.

## Language and model selection

| Use case | Model | `language` setting |
|---|---|---|
| English only | `ggml-base.en.bin` | `"en"` |
| Hindi / Urdu / mixed | `ggml-small.bin` or larger | `"auto"` |
| Specific non-English language | `ggml-small.bin` or larger | language code e.g. `"hi"` |

With `"language": "auto"`, whisper detects the language once per 30-second chunk. Code-switching mid-chunk (e.g. a sentence that switches from English to Hindi) may not be captured perfectly; use a larger model for better accuracy.

## Highlighting "next"

Transcript items whose text contains the whole word `next` receive an extra emoji in the item text:

```text
🦊 ➡️ Next we will discuss budget.
```

Configure the rule in `config.json`:

```json
{
  "highlight_word": "next",
  "highlight_emoji": "➡️"
}
```

Matching is case-insensitive and whole-word only, so `Next` and `next.` match, but `annex` does not.

## Troubleshooting

### REAPER shows "not responding"

This is expected. The Lua script blocks REAPER's UI thread while waiting for the Python worker to finish. A progress window is visible in the taskbar — watch it for status. Do not force-quit REAPER.

### "Select at least one WAV media item"

Select one or more source WAV items and run the action again.

### "Config file not found"

The ReaScript computes the config path by looking for `config.json` in the repository root (the directory containing the `reaper/` folder). Make sure `config.json` exists there and that you are running the Lua file from its installed location inside the repo, not from a copy elsewhere.

### Worker exits with an error

On failure the progress window stays open and shows the error message. Press any key to close it. You can also run the worker directly from PowerShell to see the full output:

```powershell
python worker\offline_transcribe.py `
  --config config.json `
  --wav "C:\Path\event.wav" `
  --out-dir "C:\Temp\reaper-transcript-test"
```

### Transcript text is clipped

REAPER timeline items only show text that fits in the item rectangle. Increase the transcript track height or open item notes to edit the full text.

### whisper.cpp produces garbled output for Hindi/Urdu

Older whisper.cpp builds (v1.9.x) sometimes truncate multi-byte Unicode characters at JSON boundaries, producing invalid UTF-8. The worker handles this automatically with `errors="replace"` — affected characters appear as `\ufffd` in the transcript. Use a newer whisper.cpp build or a larger model to reduce frequency of this issue.
