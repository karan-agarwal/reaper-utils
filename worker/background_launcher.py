"""Launches offline_transcribe.py as a truly detached background process.

Called by create_transcript_track_background.lua. This script starts the
transcription worker with Windows DETACHED_PROCESS so no handles are
inherited back to REAPER. It exits in under a second, letting REAPER's
ExecProcess return immediately.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Detached transcription launcher.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument("--clip-start", type=float, default=0.0)
    parser.add_argument("--clip-duration", type=float)
    parser.add_argument("--item-index", type=int, default=1)
    parser.add_argument("--total-items", type=int, default=1)
    args = parser.parse_args()

    worker_script = Path(__file__).parent / "offline_transcribe.py"

    cmd = [
        sys.executable,
        str(worker_script),
        "--config", str(args.config),
        "--wav", str(args.wav),
        "--out-dir", str(args.out_dir),
        "--clip-start", str(args.clip_start),
        "--item-index", str(args.item_index),
        "--total-items", str(args.total_items),
    ]
    if args.clip_duration is not None:
        cmd.extend(["--clip-duration", str(args.clip_duration)])

    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = args.log_file.open("w", encoding="utf-8")

    # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP ensures the child gets a
    # completely new console with no inherited handles back to REAPER.
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=log_handle,
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )

    print(f"launched: {args.wav.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
