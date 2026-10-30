# Video sync automation

`reaper/outlaw_sync_videos_to_audio.lua` inserts phone/camera video files in sync with a selected reference audio track.

The workflow is fully offline:

1. Select the reference audio track in REAPER (click the track header). All contiguous WAV items on the track are used as the reference — items must be joined end-to-end with no gaps.
2. Run the `outlaw_sync_videos_to_audio.lua` ReaScript action.
3. **Pick video files:**
   - **With [js_ReaScriptAPI](https://forum.cockos.com/showthread.php?t=212174) installed:** A multi-file picker dialog opens — select all video files at once using Ctrl+click or Shift+click.
   - **Without js_ReaScriptAPI:** A text prompt appears. Enter video paths separated by `|`, or enter `@C:\path\videos.txt` to use a manifest file with one video path per line. Leave blank and press OK to pick files one at a time.
4. The Python worker extracts low-rate mono audio using local FFmpeg.
5. It matches each video audio stream against the reference using energy/onset cross-correlation.
6. REAPER creates two fresh tracks:
   - `Synced Videos`
   - `Manual Sync Videos`
7. Every input video is inserted exactly once.

## Multi-directory or large batches (without js_ReaScriptAPI)

Use the `@manifest.txt` form for many files or videos spread across multiple directories. REAPER's text prompt has limited space and can truncate a long pipe-separated list. Create a plain text file with one video path per line and enter `@C:\path\to\manifest.txt`.

## Reference track requirements

- All items on the track must be WAV files with take playrate 1.0.
- Items must not be muted.
- Items must be contiguous — each item must start within 10 ms of the previous item's end. A clear error is shown if gaps or overlaps are detected.

## Track behavior

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
