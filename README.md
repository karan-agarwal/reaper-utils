# REAPER Offline Transcript Track

Create a transcript track inside a REAPER project from one or more selected WAV media items, using only local/offline tools.

The repo also includes a second offline automation for syncing phone/camera video files to the selected mixer-recorded WAV item.

The workflow is intentionally simple and reliable:

- Select one or more WAV media items in REAPER.
- Run the blocking ReaScript action, or use the background launcher plus importer.
- The worker runs `whisper.cpp` offline.
- REAPER creates a fresh `Transcript` track below the source track.
- Each transcript segment becomes an empty item with text notes.

Example timeline:

```text
Main Event Audio: |---------------- recording ----------------|

Transcript:       | 🦊 Welcome everyone. | 🐼 Thanks for joining. | 🦊 Let's begin. |
```

## Current scope

Implemented:

- **Multi-item support** — select any number of WAV items; each is transcribed in sequence.
- Selection-based WAV input with trimmed-item support.
- Fresh transcript track creation.
- Timeline-aligned empty items.
- Emoji speaker prefixes.
- Stable speaker-to-emoji mapping.
- Offline `whisper.cpp` integration (English and multilingual models).
- **Progress window** — a titled console window opens per item showing chunk-by-chunk progress, elapsed time, and ETA while REAPER's UI is frozen.
- **Error recovery** — if a chunk fails, an error-marker item is inserted in the transcript at the correct time and processing continues with the next chunk.
- **Resume support** — re-running the action skips any chunks that were already successfully transcribed in a previous run.
- **Background workflow** — `create_transcript_track_background.lua` launches transcription jobs without freezing REAPER, and `import_transcript_track.lua` builds the transcript track later from the completed results.
- TSV output for robust ReaScript parsing.
- JSON output for metadata and future tooling.

Planned:

- Offline speaker diarization.
- Speaker-based item colors.
- Optional SRT/VTT export.

## Repository layout

```text
reaper-offline-transcript/
├── config.example.json
├── reaper/
│   ├── create_transcript_track.lua
│   ├── create_transcript_track_background.lua
│   ├── import_transcript_track.lua
│   └── sync_videos_to_audio.lua
├── worker/
│   ├── offline_transcribe.py
│   └── sync_videos.py
├── tests/
│   └── test_offline_transcribe.py
└── docs/
    ├── ARCHITECTURE.md
    ├── INSTALL.md
    └── VIDEO_SYNC.md
```

## Requirements

- REAPER with ReaScript enabled.
- Python 3.10 or newer.
- Local FFmpeg and FFprobe binaries.
- A local `whisper.cpp` command-line binary.
- A local Whisper model file — use a multilingual model (e.g. `ggml-small.bin`) for non-English or mixed-language audio; use `ggml-base.en.bin` for English-only.

No internet connection is required at runtime once tools and models are installed.

## Quick start

1. Copy `config.example.json` to `config.json`.
2. Edit `config.json` with your local Python path, worker path, FFmpeg path, `whisper.cpp` binary path, and model path.
3. In REAPER, install `reaper/create_transcript_track.lua` as a ReaScript action.
4. Select one or more WAV media items.
5. Run the action.
6. If you use the blocking script, a progress window opens for each item and REAPER's UI will be unresponsive until all items finish.
7. If you use the background launcher, REAPER stays responsive and you later run the importer to build the transcript track from the finished TSV files.

See [docs/INSTALL.md](docs/INSTALL.md) for detailed setup.

For video alignment, see [docs/VIDEO_SYNC.md](docs/VIDEO_SYNC.md).

## How transcript items are created

The ReaScript creates normal REAPER empty items. It stores transcript text in the item notes and item name, so the transcript remains editable inside the project.

Text is intentionally split into short items rather than one huge paragraph. This keeps the timeline readable and makes manual corrections easier.

Items containing the whole word `next` receive an extra highlight emoji:

```text
🦊 ➡️ Next we will discuss budget.
```

This is configurable with `highlight_word` and `highlight_emoji` in `config.json`.

## Speaker labels

The MVP uses a single default speaker unless diarization data is added later. Speaker IDs are rendered with compact emoji labels:

```text
speaker_1 -> 🦊
speaker_2 -> 🐼
speaker_3 -> 🐸
```

Emoji assignment is deterministic, so rerunning the same transcript does not randomly change labels.

## Offline runtime guarantee

The runtime path uses only local files and local executables:

- REAPER runs the Lua action locally.
- Python uses only the standard library.
- FFmpeg converts bounded chunks of the selected WAV item region to 16 kHz mono WAV locally.
- `whisper.cpp` runs against a local model file one chunk at a time.
- Transcript JSON/TSV files are written to the local REAPER resource directory.

The scripts do not make network requests.

Large uncompressed WAV files are processed in configurable chunks (`max_audio_chunk_seconds`, default `600`) to avoid one massive temporary WAV or one huge Whisper invocation.

If a chunk fails (e.g. a whisper.cpp encoding error), an error-marker item is inserted in the transcript at the correct position and the remaining chunks continue. On re-run, any chunk whose `whisper_NNNN.json` file is still on disk is skipped and reused automatically.

## License

No license has been chosen yet. Add a license before publishing if you want others to use or contribute to the project.
