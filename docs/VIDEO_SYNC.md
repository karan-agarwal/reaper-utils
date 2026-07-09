# Video sync automation

`reaper/sync_videos_to_audio.lua` inserts phone/camera video files in sync with a selected reference WAV item.

The workflow is fully offline:

1. Select exactly one reference WAV item in REAPER.
2. Run the `sync_videos_to_audio.lua` ReaScript action.
3. Enter video paths separated by `|`, or enter `@C:\path\videos.txt` to use a manifest with one video path per line.
4. The Python worker extracts low-rate mono audio using local FFmpeg.
5. It matches each video audio stream against the reference using energy/onset cross-correlation.
6. REAPER creates two fresh tracks:
   - `Synced Videos`
   - `Manual Sync Videos`
7. Every input video is inserted exactly once.

Use the `@manifest.txt` form for many files or long paths. REAPER's text prompt has limited space and can truncate a long pipe-separated list.

## Track behavior

Videos with confidence above `min_sync_confidence` are placed on `Synced Videos` at the detected offset.

Low-confidence or failed matches are placed on `Manual Sync Videos`. If a best-guess offset exists, the item is placed at that best guess. If no usable match exists, the item is staged near the reference start for manual sync.

Each inserted item receives notes with status, confidence, offset, and reason.

## Large files and peak analysis

The worker extracts compact, low-rate audio for matching instead of loading full-quality audio into memory. Video items are inserted only after sync analysis completes, inside a single REAPER undo/UI-refresh block, to reduce UI churn while REAPER starts peak analysis for the inserted media.

REAPER may continue peak analysis after the script finishes. The script does not depend on peak files being ready.

## Configuration

Add these keys to `config.json`:

```json
{
  "video_sync_worker_script": "C:\\Workplace\\reaper-offline-transcript\\worker\\sync_videos.py",
  "video_sync_sample_rate": 4000,
  "video_sync_coarse_frame_seconds": 0.5,
  "video_sync_refine_frame_seconds": 0.05,
  "video_sync_refine_window_seconds": 8.0,
  "video_sync_min_overlap_seconds": 10.0,
  "min_sync_confidence": 0.25,
  "manual_sync_stage_gap_seconds": 5.0
}
```

Lower `min_sync_confidence` to auto-place more videos. Raise it to send more videos to the manual-sync track.

## Accuracy notes

The algorithm uses audio energy and onset patterns rather than speech recognition. It works best when the phone/camera audio and mixer audio share clear common events such as speech starts, applause, or music transients.

Very noisy camera audio, long silence, or repeated similar patterns may reduce confidence. Those videos are still inserted on `Manual Sync Videos` rather than skipped.

The matcher supports negative offsets and partial overlap, so a camera can start before the reference audio or run longer than the reference item.
