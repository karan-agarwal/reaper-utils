"""Launches offline_transcribe.py in a visible console window, detached from REAPER.

Called by create_transcript_track_background.lua. Writes a small batch file
that opens a titled console window per item, shows live progress output, and
tees the same output to worker.log. Exits in under a second so REAPER's
ExecProcess returns immediately.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _bat_quote(s: str) -> str:
    """Wrap a string in double quotes for a Windows batch file command line."""
    return '"' + str(s).replace('"', '""') + '"'


def main() -> int:
    parser = argparse.ArgumentParser(description="Visible-console transcription launcher.")
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
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Build the python command line for the bat file.
    # -u forces unbuffered stdout so the tee pipe shows output line-by-line.
    python_parts = [
        _bat_quote(sys.executable), "-u",
        _bat_quote(worker_script),
        "--config",       _bat_quote(args.config),
        "--wav",          _bat_quote(args.wav),
        "--out-dir",      _bat_quote(args.out_dir),
        "--clip-start",   str(args.clip_start),
        "--item-index",   str(args.item_index),
        "--total-items",  str(args.total_items),
    ]
    if args.clip_duration is not None:
        python_parts.extend(["--clip-duration", str(args.clip_duration)])

    python_cmd = " ".join(python_parts)

    # Use single-quoted path inside the PowerShell command so backslashes are
    # treated literally. Windows paths cannot contain single quotes, so this is safe.
    log_path_ps = str(args.log_file).replace("'", "")
    tee_cmd = (
        f"{python_cmd} 2>&1 | "
        f"powershell -NoProfile -Command \"$input | Tee-Object -FilePath '{log_path_ps}'\""
    )

    title = f"REAPER Transcriber [Item {args.item_index} of {args.total_items}]"
    bat_path = args.out_dir / "run_transcribe_background.bat"
    bat_path.write_text(
        "\r\n".join([
            "@echo off",
            f"title {title}",
            f"cd /d {_bat_quote(args.out_dir)}",
            tee_cmd,
            "echo.",
            "echo Transcription finished. Press any key to close.",
            "pause > nul",
        ]) + "\r\n",
        encoding="utf-8",
    )

    # CREATE_NEW_CONSOLE opens a visible titled window for the user to watch.
    # CREATE_NEW_PROCESS_GROUP isolates signal handling from REAPER.
    # No stdout/stderr redirection here — the bat file's tee handles logging.
    CREATE_NEW_CONSOLE = 0x00000010
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    subprocess.Popen(
        ["cmd.exe", "/C", str(bat_path)],
        creationflags=CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

    print(f"launched: {args.wav.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
