# Architecture

## Overview

The tool is split into two processes:

1. **REAPER ReaScripts**
   - `create_transcript_track.lua` validates the selected media item(s), calls the offline Python worker, reads the generated TSV transcript, and creates a fresh `Transcript` track below the source track.
   - `create_transcript_track_background.lua` writes a run manifest, launches the worker(s) in the background, and returns immediately so REAPER stays responsive.
   - `import_transcript_track.lua` reads the latest run manifest and completed TSV outputs, then creates the `Transcript` track from those finished results.
   - The blocking script is still useful when you want live progress output in a console window.

2. **Python worker (`offline_transcribe.py`)**
   - Loads configuration.
   - Prints a progress header and per-chunk status to the console window.
   - Uses local FFmpeg to extract bounded chunks of the selected item region as 16 kHz mono WAV.
   - Executes `whisper.cpp` locally one chunk at a time.
   - On chunk failure, inserts an error-marker segment and continues with remaining chunks.
   - On re-run, detects existing `whisper_NNNN.json` files and reuses them (resume).
   - Normalizes Whisper JSON into transcript segments.
   - Applies stable emoji speaker labels.
   - Writes both JSON and TSV transcript files.

## Why TSV for ReaScript consumption?

The worker writes JSON for humans, tests, and future integrations. The ReaScript reads TSV because REAPER's embedded Lua environment does not include a standard JSON parser. TSV keeps the ReaScript dependency-free and avoids bundling a custom JSON parser.

Newlines and tabs in transcript text are escaped before writing TSV.

## Data flow

```text
Selected WAV item(s)
  ↓
create_transcript_track.lua
  ↓  visible console window, blocking
offline_transcribe.py
  ↓
normalized transcript JSON + TSV
  ↓
Transcript track created immediately

Selected WAV item(s)
  ↓
create_transcript_track_background.lua
  ↓  writes manifest + launches background workers
offline_transcribe.py
  ↓
completed item_NNNN/transcript.tsv files
  ↓
import_transcript_track.lua
  ↓
Transcript track created later from finished results
```

## Progress window

`reaper.ExecProcess` blocks REAPER's UI thread until the child process exits. To give the user visibility without requiring a separate background process, the Lua script:

1. Writes a small `run_transcribe.bat` into the item output directory.
2. Calls `cmd.exe /C start /wait "REAPER Transcriber [Item N of M]" "run_transcribe.bat"`.

This opens a **separate titled console window** the user can watch. The outer `cmd.exe` blocks until the window closes, and its exit code propagates back via `start /wait`. On failure the batch file runs `pause` so the user can read the error before the window closes.

The Python worker prints a structured progress display to that window:

```text
==============================================================
  REAPER Transcript Worker  |  Item 1 of 2
  Audio : recording.wav
  Range : 0:00:00 - 0:47:23  (47m 23s)
  Plan  : 5 chunk(s), up to 600s each
==============================================================

[--------------------] Chunk 1/5  (0:00:00 - 0:10:00)
         [1/3] Extracting audio...
         [2/3] Transcribing with whisper.cpp...  (may take several minutes)
         [3/3] Loading transcript segments...
         Done in 1m 24s  |  ETA: ~5m 36s for 4 more chunk(s)
```

The non-blocking launcher instead starts the worker in the background and writes stdout/stderr to `worker.log` in each item folder.

## Timeline alignment

Whisper timestamps are relative to each prepared chunk. The worker offsets each chunk result back into selected-item time, and the ReaScript adds the source item's project start position to each segment start/end.

Example:

```text
Selected WAV item starts at project time: 83.000 seconds
Transcript segment starts in clip at:      12.500 seconds
Final transcript item starts at:           95.500 seconds
```

## Multi-item support

When multiple WAV items are selected, the Lua script loops over them in order. Each item gets its own `item_NNNN/` subdirectory in the output folder (e.g. `item_0001/`, `item_0002/`). The TSVs are read after all workers finish, and all segments are inserted into a single `Transcript` track in one undo block. Items from different source WAVs appear interleaved by project timeline position.

The background launcher keeps the same `item_NNNN/` layout, but it places runs under `reaper-offline-transcript\runs\run_YYYYMMDD_HHMMSS\` and records the latest run in `latest_run.txt` so the importer knows which outputs to use.

## Speaker handling

The MVP assigns all segments to `speaker_1`. The data model already supports speaker IDs so offline diarization can be added without changing the ReaScript contract.

Future diarization should output segments shaped like:

```json
{
  "start": 12.5,
  "end": 18.2,
  "speaker": "speaker_1",
  "text": "Welcome everyone."
}
```

The worker maps `speaker_1`, `speaker_2`, and so on to deterministic emoji labels.

## Error handling

- The ReaScript shows REAPER message boxes for invalid selection, missing config, worker failure, or malformed transcript output.
- The Python worker exits non-zero with a concise message on configuration or tool failures.
- Per-chunk failures insert an error-marker segment (`[Chunk N transcription error: ...]`) at the correct timeline position instead of aborting the job.
- Failed chunk JSON files are kept on disk so the next run can attempt to resume from them. Successfully processed chunk JSONs are deleted immediately after loading.
- Invalid UTF-8 bytes in whisper.cpp JSON output (e.g. truncated multi-byte Unicode characters in older whisper.cpp builds) are handled with `errors="replace"` rather than crashing.

## Config path resolution

`io.open` in REAPER's embedded Lua does not resolve `..` path components on Windows. Both Lua scripts compute the repository root explicitly:

```lua
local SCRIPT_DIR = ({reaper.get_action_context()})[2]:match("^(.*[\\/])")
local REPO_DIR   = SCRIPT_DIR:match("^(.*[\\/])[^\\/]+[\\/]$") or SCRIPT_DIR
local CONFIG_PATH = REPO_DIR .. "config.json"
```

## Offline boundary

All runtime work is local. The Python worker uses the standard library, local FFmpeg, a local `whisper.cpp` binary, and a local model file. No network APIs are called.

## Large-file strategy

The worker never converts a long uncompressed WAV into one huge intermediate file. It uses `max_audio_chunk_seconds` from `config.json` to prepare and transcribe one bounded chunk at a time. This trades latency for stability and keeps temporary disk and memory usage predictable.
