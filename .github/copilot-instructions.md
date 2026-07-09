# Copilot instructions for this repository

This repository contains offline REAPER automations built as ReaScript Lua actions plus local Python workers. The target user workflow is audio/video post-production in REAPER without internet access.

## Hard requirements

- Runtime must be fully offline. Do not add cloud APIs, web requests, model downloads, telemetry, or dependencies that need internet access at runtime.
- Prefer local executables configured in `config.json`, especially FFmpeg, FFprobe, `whisper.cpp`, and local Whisper model files.
- Keep Python workers standard-library-only unless there is a strong reason to add an optional dependency.
- Preserve Windows compatibility. Use Windows paths in docs/examples and be careful with quoting paths that contain spaces.
- Every REAPER action should surface errors with a clear message box or worker error; do not silently skip user-provided media.
- Run the existing Python unit tests after behavior changes:

```powershell
python -m unittest discover -s tests -v
```

## Architecture

The repo has two main automations:

1. `reaper/create_transcript_track.lua` + `worker/offline_transcribe.py`
   - Input: exactly one selected WAV media item in REAPER.
   - Worker: extracts bounded 16 kHz mono chunks with FFmpeg and runs local `whisper.cpp`.
   - Output: `transcript.json` and `transcript.tsv`.
   - REAPER result: a fresh `Transcript` track with empty text items aligned to speech.

2. `reaper/sync_videos_to_audio.lua` + `worker/sync_videos.py`
   - Input: exactly one selected WAV reference item plus video paths or an `@manifest.txt`.
   - Worker: extracts low-rate mono audio with FFmpeg and aligns video audio to the reference using energy/onset correlation.
   - Output: `video_sync_results.json` and `video_sync_results.tsv`.
   - REAPER result: fresh `Synced Videos` and `Manual Sync Videos` tracks. Every input video must be represented exactly once, using a placeholder item if REAPER cannot insert the media file.

Lua reads TSV instead of JSON because REAPER's embedded Lua environment does not include a standard JSON parser. Keep TSV formats stable and update both Lua parsing and Python output together.

## REAPER Lua conventions

- ReaScripts validate that exactly one reference WAV item is selected.
- Take playrate must remain `1.0` unless alignment math is explicitly updated.
- Use `D_STARTOFFS`, `D_LENGTH`, and `D_POSITION` to map selected item audio time to project time.
- Use `reaper.ExecProcess(command, 0)` for blocking worker execution. Its return is a string beginning with the exit code; parse it before checking success.
- Wrap worker calls as `cmd.exe /C "<inner command>"`, with each argument quoted individually.
- When inserting many items, compute all worker results first, then batch REAPER edits inside:

```lua
reaper.Undo_BeginBlock()
reaper.PreventUIRefresh(1)
-- insert/mutate items
reaper.PreventUIRefresh(-1)
reaper.UpdateArrange()
reaper.Undo_EndBlock("...", -1)
```

- REAPER may run peak analysis after media insertion. Do not depend on peaks being ready.
- For video insertion, do not assume the newest item is the last item on a track. Track items are position-ordered. Use GUID snapshots and/or selected-item checks as in `sync_videos_to_audio.lua`.

## Python worker conventions

- Use `argparse` CLIs with explicit paths and numeric timing arguments.
- Raise or return non-zero for configuration/tool failures. For per-media failures in batch jobs, emit a manual result row instead of aborting the whole run.
- Escape TSV with `\`, tab, and newline handling compatible with the Lua `unescape_tsv` functions.
- Keep long-file processing bounded:
  - Transcription chunks are controlled by `max_audio_chunk_seconds`.
  - Video sync extracts compact low-rate audio instead of loading original media.
  - Delete temporary chunk WAV/JSON files when finished.
- Do not capture large normal stdout streams in memory. Suppress or stream subprocess output where appropriate; keep stderr for diagnostics.

## Transcript behavior

- Transcript items are REAPER empty items with item notes/names.
- Keep text segments short and readable; `max_chars_per_item` controls splitting.
- Speaker IDs are rendered as stable emojis from `speaker_emoji_pool`.
- Items containing whole word `highlight_word` (default `next`) get `highlight_emoji` added to display text.
- JSON should preserve original text and include display/highlight metadata when applicable.

## Video sync behavior

- Use the selected WAV item as the reference audio region. Trimmed references are supported via `D_STARTOFFS` and `D_LENGTH`.
- Video sync must support:
  - videos shorter than the reference,
  - videos longer than the reference,
  - videos that start before the reference audio,
  - low-confidence/failure cases.
- Confident results go to `Synced Videos`.
- Low-confidence, before-project-start, no-match, or failed results go to `Manual Sync Videos`.
- Every input video path must produce one result row and one visible REAPER item or placeholder.
- `best_alignment_offset` intentionally uses consistent local Pearson-style scoring for both full and partial overlap candidates. Do not mix incompatible confidence metrics when choosing offsets.

## Config keys

`config.example.json` is the source of truth for supported settings. Keep docs and defaults aligned when adding keys.

Important keys:

- `python_executable`
- `worker_script`
- `video_sync_worker_script`
- `ffmpeg_binary`
- `ffprobe_binary`
- `whisper_binary`
- `whisper_model`
- `max_audio_chunk_seconds`
- `max_chars_per_item`
- `highlight_word`
- `highlight_emoji`
- `video_sync_sample_rate`
- `video_sync_coarse_frame_seconds`
- `video_sync_refine_frame_seconds`
- `video_sync_refine_window_seconds`
- `video_sync_min_overlap_seconds`
- `min_sync_confidence`
- `manual_sync_stage_gap_seconds`
- `speaker_emoji_pool`

## Documentation expectations

Update docs when behavior or configuration changes:

- `README.md` for high-level repo/workflow changes.
- `docs/INSTALL.md` for setup/config/runtime requirements.
- `docs/ARCHITECTURE.md` for transcript architecture.
- `docs/VIDEO_SYNC.md` for video alignment behavior.

Do not add planning notes or temporary design files to the repo. Use docs only for durable user/developer guidance.
