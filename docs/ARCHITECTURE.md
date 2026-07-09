# Architecture

## Overview

The tool is split into two processes:

1. **REAPER ReaScripts**
   - `create_transcript_track.lua` validates the selected media item(s), calls the offline Python worker, reads the generated TSV transcript, and creates a fresh `Transcript` track below the source track.
   - `create_transcript_track_background.lua` writes a run manifest, launches `background_launcher.py` synchronously (exits in < 1 s), and returns immediately so REAPER stays responsive. The actual transcription runs fully detached.
   - `import_transcript_track.lua` reads the latest run manifest and completed TSV outputs, then creates the `Transcript` track from those finished results.
   - `sync_videos_to_audio.lua` collects all WAV items from a selected reference track, accepts video files via a file picker, and calls `sync_videos.py` to align each video's audio against the concatenated reference.
   - The blocking transcript script is still useful when you want live progress output in a console window.

2. **Python workers**
   - `offline_transcribe.py` — transcription worker; extracts bounded WAV chunks, runs whisper.cpp, writes TSV/JSON.
   - `background_launcher.py` — thin launcher that starts `offline_transcribe.py` as a detached Windows process (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`) so no pipe handles are inherited back to REAPER. Exits in under a second.
   - `sync_videos.py` — video sync worker; extracts low-rate mono audio from each reference segment and video file, builds energy/onset features, runs Pearson-style correlation, writes TSV/JSON results.

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

## Progress window (blocking script)

`reaper.ExecProcess` blocks REAPER's UI thread until the child process exits. To give the user visibility without requiring a separate background process, the Lua script:

1. Writes a small `run_transcribe.bat` into the item output directory.
2. Calls `cmd.exe /C start /wait "REAPER Transcriber [Item N of M]" "run_transcribe.bat"`.

This opens a **separate titled console window** the user can watch. The outer `cmd.exe` blocks until the window closes, and its exit code propagates back via `start /wait`. On failure the batch file runs `pause` so the user can read the error before the window closes.

The Python worker prints a structured progress display to the console window:

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

The background launcher writes worker output to `worker.log` in each item folder instead.

## Background launcher (non-blocking script)

The background launcher avoids the "stdout pipe stays open" problem that would block REAPER's `ExecProcess` even with `start /B`. Instead:

1. `create_transcript_track_background.lua` calls `background_launcher.py` via `ExecProcess(command, 0)`. The launcher exits in under a second.
2. `background_launcher.py` uses Python's `subprocess.Popen` with `creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`. This creates the transcription worker with a completely new console — no file handles are inherited back to REAPER.
3. Worker stdout/stderr is redirected to `worker.log` in each item folder.
4. `ExecProcess` returns as soon as the launcher exits, and REAPER's UI stays responsive.

## Video sync architecture

### Reference track and segments

`sync_videos_to_audio.lua` selects a whole track instead of a single item. It collects all WAV items in position order, validates them (WAV source, playrate 1.0, not muted, contiguous within 10 ms), and writes a reference segments TSV:

```
source_path<TAB>clip_start<TAB>clip_duration
C:\Audio\part1.wav<TAB>0.000000<TAB>1800.000000
C:\Audio\part2.wav<TAB>0.000000<TAB>900.000000
```

`sync_videos.py` reads this file as `ReferenceSegment` dataclass instances, extracts each clip to a temp WAV at the configured sample rate, then concatenates them into `reference.wav` using Python's stdlib `wave` module (no FFmpeg concat, no path-escaping risk). This produces a single reference audio file spanning the full track timeline.

### Timing math

The correlation offset returned by `estimate_sync_offset` is measured from the start of the concatenated reference audio. Adding it to the first item's project position gives the correct project insert position:

```
insert_position = first_item.D_POSITION + offset_seconds
```

This is only valid when items are exactly contiguous. The Lua contiguity check enforces this before the worker runs.

### Video file picker

When [js_ReaScriptAPI](https://forum.cockos.com/showthread.php?t=212174) is installed, `JS_Dialog_BrowseForOpenFiles` opens a native multi-file picker. The null-byte-separated result is parsed with an `is_absolute_path` guard that handles both Windows format (`dir\0file1\0file2`) and macOS/Linux SWELL format (full paths per entry). Without js_ReaScriptAPI the script falls back to the original text prompt with `@manifest.txt` support.

## Transcript timeline alignment

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
