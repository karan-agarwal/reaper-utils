# Installation

## 1. Install offline dependencies

Install or build `whisper.cpp` on the machine that will run REAPER. Download the Whisper model you want to use and keep it on local disk.

- For **English-only** audio, `ggml-base.en.bin` (~141 MB) is fast and accurate.
- For **Hindi, Urdu, or mixed-language** audio, use a multilingual model: `ggml-small.bin` (~465 MB) or larger. Set `"language": "auto"` in `config.json` so whisper detects the language per chunk.

Install FFmpeg locally as well. It is used only to prepare the selected WAV region as 16 kHz mono audio for `whisper.cpp`.

At runtime the tool does not need internet access.

## 2. Configure paths

The workers are found automatically relative to the installed Lua actions. On first run, an action can create `config.json` for you. To configure it manually, copy the example config beside the actions: from the repository root for a manual clone, or from REAPER's resource `Scripts` folder for ReaPack.

```powershell
# Run from the repository root for a manual clone.
Copy-Item config.example.json config.json
```

Edit `config.json`. Use absolute Windows paths with escaped backslashes for local tools; `python` can remain as-is if it is on `PATH`:

```json
{
  "python_executable": "python",
  "worker_script": "",
  "video_sync_worker_script": "",
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

## 3. Install the REAPER actions

### ReaPack

1. Open **Extensions > ReaPack > Import repositories** and add:
   `https://github.com/karan-agarwal/reaper-utils/raw/main/index.xml`
2. In **Browse packages**, install **outlaw_create_transcript_track.lua**. The package installs all four `outlaw_` actions, Python workers, and `config.example.json`.
3. Run any `outlaw` action. If `config.json` is missing, choose an existing config to copy or create one by selecting Python, FFmpeg, the `whisper.cpp` executable, and your local model. The config is saved in REAPER's resource `Scripts` folder for future runs.

### Manual

1. Open REAPER.
2. Go to **Actions > Show action list**.
3. Choose **ReaScript: New/load**.
4. Select `reaper/outlaw_create_transcript_track.lua`.
5. Optionally bind it to a toolbar button or shortcut.
6. Also load `reaper/outlaw_create_transcript_track_background.lua` and `reaper/outlaw_import_transcript_track.lua` if you want the non-blocking workflow.
7. Load `reaper/outlaw_sync_videos_to_audio.lua` for the video sync automation.

For the best video-sync experience, install the **[js_ReaScriptAPI](https://forum.cockos.com/showthread.php?t=212174)** REAPER extension. It enables a native multi-file picker dialog so you can select all video files at once. Without it the script falls back to a text prompt.

## 4. Run

1. Select one or more WAV media items in the project.
2. Run `outlaw_create_transcript_track.lua` for the blocking workflow, or run `outlaw_create_transcript_track_background.lua` if you want REAPER to stay responsive.
3. If you launched the background workflow, wait for the jobs to finish and then run `outlaw_import_transcript_track.lua` to build the transcript track from the latest completed run.
4. For the blocking workflow, a titled console window opens for each item — watch it for chunk-by-chunk progress, elapsed time, and ETA. REAPER's UI will be unresponsive while the worker runs; this is expected.
5. Once the importer runs, a new `Transcript` track appears below the source track.

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

## Background workflow

The background launcher (`outlaw_create_transcript_track_background.lua`) uses a small Python helper script (`worker/background_launcher.py`) to start the real transcription worker as a fully detached Windows process. The launcher exits in under a second, so REAPER's `ExecProcess` call returns immediately and the UI stays responsive.

Under the hood:

1. The Lua script writes a manifest TSV and a `run_meta.txt` (storing the project path) into a unique run directory under `reaper-offline-transcript\runs\run_YYYYMMDD_HHMMSS_NNNNNN\`.
2. It calls `background_launcher.py` synchronously (fast — exits in < 1 s).
3. `background_launcher.py` starts `offline_transcribe.py` with Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` flags so no stdout/stderr handles are inherited back to REAPER. Worker output goes to `worker.log` in each item folder.
4. REAPER unfreezes immediately; transcription continues in the background.
5. A `latest_run.txt` pointer is written so the importer finds the right run directory.

This means you can:

1. Start transcription without freezing REAPER.
2. Keep editing or monitoring the session.
3. Come back later and run `outlaw_import_transcript_track.lua` to place the finished transcript items on the timeline.

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

### Video sync: "Reference track items are not contiguous"

The video sync action requires all items on the reference track to be placed end-to-end with no gaps or overlaps (within 10 ms). Use REAPER's item snap/align tools to close any gaps, or select a different track where items are already joined.

### Video sync: no file picker dialog appears

Install the **[js_ReaScriptAPI](https://forum.cockos.com/showthread.php?t=212174)** REAPER extension for the native multi-file picker. Without it the script shows a text prompt where you can type paths separated by `|` or enter `@C:\path\manifest.txt`.

### Background transcription is not appearing after import

Check `worker.log` in the run's item folder (e.g. `reaper-offline-transcript\runs\run_...\item_0001\worker.log`) to see if the worker is still running or hit an error. Only run `outlaw_import_transcript_track.lua` after all workers have finished — the importer verifies that every `transcript.tsv` file exists before inserting anything.
