"""Offline transcription worker for the REAPER transcript-track action.

The worker intentionally depends only on Python's standard library. It shells
out to a locally installed whisper.cpp binary, normalizes the result, and writes
both JSON and TSV outputs. The Lua ReaScript consumes TSV to avoid a bundled JSON
parser in REAPER's scripting environment.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_EMOJI_POOL = ["🦊", "🐼", "🐸", "🐧", "🐵", "🐯", "🐨", "🐰", "🦁", "🐙"]
DEFAULT_HIGHLIGHT_WORD = "next"
DEFAULT_HIGHLIGHT_EMOJI = "➡️"

_BAR_WIDTH = 20


def _fmt_duration(seconds: float) -> str:
    """Format a duration as h:mm:ss or m:ss."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {sec:02d}s"
    return f"{m}m {sec:02d}s"


def _fmt_hms(seconds: float) -> str:
    """Format a timestamp as H:MM:SS."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def _print_progress_header(
    wav_path: Path,
    clip_start: float,
    effective_duration: float,
    n_chunks: int,
    chunk_duration: float,
    item_index: int,
    total_items: int,
) -> None:
    sep = "=" * 62
    print(sep, flush=True)
    print(f"  REAPER Transcript Worker  |  Item {item_index} of {total_items}", flush=True)
    print(f"  Audio : {wav_path.name}", flush=True)
    clip_end = clip_start + effective_duration
    print(
        f"  Range : {_fmt_hms(clip_start)} - {_fmt_hms(clip_end)}"
        f"  ({_fmt_duration(effective_duration)})",
        flush=True,
    )
    print(
        f"  Plan  : {n_chunks} chunk(s), up to {int(chunk_duration)}s each",
        flush=True,
    )
    print(sep, flush=True)


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    speaker: str
    text: str

    def validate(self) -> None:
        if self.start < 0:
            raise ValueError(f"segment start must be non-negative: {self.start}")
        if self.end <= self.start:
            raise ValueError(f"segment end must be after start: {self.start} -> {self.end}")
        if not self.speaker:
            raise ValueError("segment speaker must not be empty")


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    required_keys = ["ffmpeg_binary", "whisper_binary", "whisper_model"]
    missing = [key for key in required_keys if not config.get(key)]
    if missing:
        raise ValueError(f"config is missing required keys: {', '.join(missing)}")

    return config


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def prepare_audio(
    config: dict[str, Any],
    wav_path: Path,
    out_dir: Path,
    clip_start: float,
    clip_duration: float | None,
    output_path: Path | None = None,
) -> Path:
    ffmpeg_binary = Path(config["ffmpeg_binary"])

    if clip_start < 0:
        raise ValueError(f"clip start must be non-negative: {clip_start}")
    if clip_duration is not None and clip_duration <= 0:
        raise ValueError(f"clip duration must be positive: {clip_duration}")
    if not ffmpeg_binary.exists():
        raise FileNotFoundError(f"ffmpeg binary not found: {ffmpeg_binary}")
    if not wav_path.exists():
        raise FileNotFoundError(f"WAV file not found: {wav_path}")

    prepared_wav = output_path or out_dir / "prepared-16khz-mono.wav"
    command = [
        str(ffmpeg_binary),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{clip_start:.6f}",
        "-i",
        str(wav_path),
    ]

    if clip_duration is not None:
        command.extend(["-t", f"{clip_duration:.6f}"])

    command.extend(["-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(prepared_wav)])

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"ffmpeg failed with exit code {result.returncode}: {stderr}")

    if not prepared_wav.exists():
        raise FileNotFoundError(f"ffmpeg did not produce prepared WAV: {prepared_wav}")

    return prepared_wav


def run_whisper_cpp(
    config: dict[str, Any],
    wav_path: Path,
    out_dir: Path,
    output_stem: str = "whisper",
) -> Path:
    whisper_binary = Path(config["whisper_binary"])
    whisper_model = Path(config["whisper_model"])

    if not whisper_binary.exists():
        raise FileNotFoundError(f"whisper binary not found: {whisper_binary}")
    if not whisper_model.exists():
        raise FileNotFoundError(f"whisper model not found: {whisper_model}")
    output_base = out_dir / output_stem
    command = [
        str(whisper_binary),
        "-m",
        str(whisper_model),
        "-f",
        str(wav_path),
        "-oj",
        "-of",
        str(output_base),
    ]

    language = config.get("language")
    if language:
        command.extend(["-l", str(language)])

    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"whisper.cpp failed with exit code {result.returncode}: {stderr}")

    json_path = output_base.with_suffix(".json")
    if not json_path.exists():
        raise FileNotFoundError(f"whisper.cpp did not produce JSON output: {json_path}")

    return json_path


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        frame_rate = wav_file.getframerate()

    if frame_rate <= 0:
        raise ValueError(f"WAV file has invalid frame rate: {path}")

    return frame_count / frame_rate


def resolve_ffprobe_binary(config: dict[str, Any]) -> Path:
    configured = config.get("ffprobe_binary")
    if configured:
        return Path(str(configured))

    ffmpeg_binary = Path(config["ffmpeg_binary"])
    suffix = ".exe" if ffmpeg_binary.suffix.lower() == ".exe" else ""
    return ffmpeg_binary.with_name(f"ffprobe{suffix}")


def media_duration_seconds(config: dict[str, Any], path: Path) -> float:
    ffprobe_binary = resolve_ffprobe_binary(config)
    if ffprobe_binary.exists():
        command = [
            str(ffprobe_binary),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if result.returncode == 0:
            try:
                duration = float(result.stdout.strip())
                if duration > 0:
                    return duration
            except ValueError:
                warn(f"ffprobe returned an invalid duration for {path}: {result.stdout.strip()!r}")

        stderr = result.stderr.strip()
        warn(f"ffprobe could not read duration for {path}: {stderr}")

    return wav_duration_seconds(path)


def chunk_ranges(total_duration: float, chunk_duration: float) -> list[tuple[float, float]]:
    if chunk_duration <= 0:
        raise ValueError(f"max_audio_chunk_seconds must be positive: {chunk_duration}")

    if total_duration <= 0:
        raise ValueError(f"clip duration must be positive: {total_duration}")

    chunks: list[tuple[float, float]] = []
    cursor = 0.0
    epsilon = 0.001
    while cursor < total_duration - epsilon:
        duration = min(chunk_duration, total_duration - cursor)
        chunks.append((cursor, duration))
        cursor += duration

    return chunks


def offset_segments(segments: list[Segment], offset_seconds: float) -> list[Segment]:
    if offset_seconds == 0:
        return segments

    return [
        Segment(
            start=segment.start + offset_seconds,
            end=segment.end + offset_seconds,
            speaker=segment.speaker,
            text=segment.text,
        )
        for segment in segments
    ]


def parse_timestamp(value: Any, *, centiseconds: bool = False) -> float:
    """Parse whisper.cpp timestamps from seconds, centiseconds, or hh:mm:ss strings."""

    if isinstance(value, (int, float)):
        number = float(value)
        return number / 100.0 if centiseconds else number

    if not isinstance(value, str):
        raise ValueError(f"unsupported timestamp value: {value!r}")

    stripped = value.strip()
    if not stripped:
        raise ValueError("empty timestamp")

    if stripped.replace(".", "", 1).isdigit():
        number = float(stripped)
        return number / 100.0 if centiseconds else number

    pieces = stripped.split(":")
    if len(pieces) != 3:
        raise ValueError(f"unsupported timestamp format: {value!r}")

    hours = int(pieces[0])
    minutes = int(pieces[1])
    seconds = float(pieces[2].replace(",", "."))
    return hours * 3600 + minutes * 60 + seconds


def extract_segment_times(raw_segment: dict[str, Any]) -> tuple[float, float]:
    if "start" in raw_segment and "end" in raw_segment:
        return parse_timestamp(raw_segment["start"]), parse_timestamp(raw_segment["end"])

    if "t0" in raw_segment and "t1" in raw_segment:
        return parse_timestamp(raw_segment["t0"], centiseconds=True), parse_timestamp(
            raw_segment["t1"], centiseconds=True
        )

    timestamps = raw_segment.get("timestamps")
    if isinstance(timestamps, dict) and {"from", "to"} <= timestamps.keys():
        return parse_timestamp(timestamps["from"]), parse_timestamp(timestamps["to"])

    raise ValueError(f"segment does not contain timestamps: {raw_segment}")


def load_whisper_segments(path: Path, max_chars: int = 280) -> list[Segment]:
    # whisper.cpp can emit truncated multi-byte sequences (e.g. Devanagari) at
    # JSON string boundaries. Replace invalid bytes rather than crashing.
    with path.open("r", encoding="utf-8", errors="replace") as file:
        payload = json.load(file)

    raw_segments = payload.get("transcription") or payload.get("segments")
    if not isinstance(raw_segments, list):
        raise ValueError("whisper JSON does not contain a transcription or segments list")

    segments: list[Segment] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue

        try:
            start, end = extract_segment_times(raw)
            text = str(raw.get("text", "")).strip()
            if not text:
                continue

            segment = Segment(start=start, end=end, speaker="speaker_1", text=text)
            segment.validate()
            segments.append(segment)
        except ValueError as error:
            warn(f"skipping invalid whisper segment: {error}")

    return split_long_segments(segments, max_chars=max_chars)


def transcribe_audio_chunks(
    config: dict[str, Any],
    wav_path: Path,
    out_dir: Path,
    clip_start: float,
    clip_duration: float | None,
    max_chars: int,
    item_index: int = 1,
    total_items: int = 1,
) -> list[Segment]:
    chunk_duration = float(config.get("max_audio_chunk_seconds", 600))
    effective_duration = (
        clip_duration if clip_duration is not None
        else media_duration_seconds(config, wav_path) - clip_start
    )
    chunk_dir = out_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    all_segments: list[Segment] = []
    ranges = chunk_ranges(effective_duration, chunk_duration)
    total_chunks = len(ranges)
    completed: set[int] = set()

    _print_progress_header(
        wav_path=wav_path,
        clip_start=clip_start,
        effective_duration=effective_duration,
        n_chunks=total_chunks,
        chunk_duration=chunk_duration,
        item_index=item_index,
        total_items=total_items,
    )

    job_t0 = time.monotonic()

    for index, (relative_start, duration) in enumerate(ranges, start=1):
        prepared_wav = chunk_dir / f"chunk_{index:04d}.wav"
        whisper_stem = f"whisper_{index:04d}"
        whisper_json = chunk_dir / f"{whisper_stem}.json"

        # Progress bar: filled portion reflects chunks completed so far.
        filled = int(_BAR_WIDTH * (index - 1) / total_chunks)
        bar = "#" * filled + "-" * (_BAR_WIDTH - filled)
        chunk_label = (
            f"\n[{bar}] Chunk {index}/{total_chunks}"
            f"  ({_fmt_hms(relative_start)} - {_fmt_hms(relative_start + duration)})"
        )
        print(chunk_label, flush=True)

        chunk_t0 = time.monotonic()

        try:
            if whisper_json.exists():
                # Resume: a previous run already transcribed this chunk.
                print("         Resuming: reusing cached whisper output...", flush=True)
            else:
                print("         [1/3] Extracting audio...", flush=True)
                prepare_audio(
                    config=config,
                    wav_path=wav_path,
                    out_dir=chunk_dir,
                    clip_start=clip_start + relative_start,
                    clip_duration=duration,
                    output_path=prepared_wav,
                )
                print(
                    "         [2/3] Transcribing with whisper.cpp..."
                    "  (may take several minutes)",
                    flush=True,
                )
                run_whisper_cpp(config, prepared_wav, chunk_dir, whisper_stem)
                try:
                    prepared_wav.unlink()
                except FileNotFoundError:
                    pass

            print("         [3/3] Loading transcript segments...", flush=True)
            chunk_segments = load_whisper_segments(whisper_json, max_chars=max_chars)
            all_segments.extend(offset_segments(chunk_segments, relative_start))
            completed.add(index)

            chunk_elapsed = time.monotonic() - chunk_t0
            job_elapsed = time.monotonic() - job_t0
            n_done = len(completed)
            if n_done < total_chunks:
                avg = job_elapsed / n_done
                eta = avg * (total_chunks - n_done)
                print(
                    f"         Done in {_fmt_duration(chunk_elapsed)}"
                    f"  |  ETA: ~{_fmt_duration(eta)} for {total_chunks - n_done} more chunk(s)",
                    flush=True,
                )
            else:
                print(f"         Done in {_fmt_duration(chunk_elapsed)}", flush=True)

        except Exception as error:
            chunk_elapsed = time.monotonic() - chunk_t0
            warn(f"chunk {index} failed: {error}")
            print(
                f"         ERROR after {_fmt_duration(chunk_elapsed)}: {error}",
                flush=True,
            )
            # Insert a visible error marker in the transcript so the user can
            # spot the gap and re-run just that portion manually if needed.
            error_end = min(relative_start + duration, relative_start + 5.0)
            if error_end > relative_start:
                error_segment = Segment(
                    start=relative_start,
                    end=error_end,
                    speaker="speaker_1",
                    text=f"[Chunk {index} transcription error: {error}]",
                )
                try:
                    error_segment.validate()
                    all_segments.append(error_segment)
                except ValueError:
                    pass
            # Leave the chunk JSON on disk (if it exists) so the next run can
            # attempt to reuse it rather than re-running whisper.

    # Remove JSON files only for chunks that completed successfully.
    # Failed chunks keep their JSON so the next run can resume from them.
    for index in completed:
        try:
            (chunk_dir / f"whisper_{index:04d}.json").unlink()
        except FileNotFoundError:
            pass

    total_elapsed = time.monotonic() - job_t0
    sep = "=" * 62
    print(f"\n{sep}", flush=True)
    n_failed = total_chunks - len(completed)
    if n_failed == 0:
        print(
            f"  All {total_chunks} chunk(s) complete."
            f"  Total time: {_fmt_duration(total_elapsed)}",
            flush=True,
        )
    else:
        print(
            f"  {len(completed)}/{total_chunks} chunk(s) complete"
            f"  ({n_failed} failed)."
            f"  Total time: {_fmt_duration(total_elapsed)}",
            flush=True,
        )
    print(sep, flush=True)

    return all_segments


def split_text_by_length(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    sentences: list[str] = []
    current = ""

    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            sentences.append(current)
        current = word

    if current:
        sentences.append(current)

    return sentences


def split_long_segments(segments: list[Segment], max_chars: int = 280) -> list[Segment]:
    result: list[Segment] = []

    for segment in segments:
        parts = split_text_by_length(segment.text, max_chars)
        if len(parts) == 1:
            result.append(segment)
            continue

        duration = segment.end - segment.start
        offset = 0.0
        total_chars = sum(len(part) for part in parts)

        for part in parts:
            part_duration = duration * (len(part) / total_chars)
            start = segment.start + offset
            end = min(segment.end, start + part_duration)
            split_segment = Segment(start=start, end=end, speaker=segment.speaker, text=part)
            try:
                split_segment.validate()
                result.append(split_segment)
            except ValueError as error:
                warn(f"skipping invalid split segment: {error}")
            offset += part_duration

    return result


def build_emoji_map(segments: list[Segment], emoji_pool: list[str]) -> dict[str, str]:
    speakers = sorted({segment.speaker for segment in segments})
    if len(speakers) > len(emoji_pool):
        raise ValueError(
            f"not enough emojis configured for {len(speakers)} speakers; "
            f"pool has {len(emoji_pool)} entries"
        )

    return {speaker: emoji_pool[index] for index, speaker in enumerate(speakers)}


def escape_tsv(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def contains_whole_word(text: str, word: str) -> bool:
    if not word:
        return False

    pattern = rf"(?<!\w){re.escape(word)}(?!\w)"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def render_segment_text(text: str, highlight_word: str, highlight_emoji: str) -> tuple[str, bool]:
    highlighted = contains_whole_word(text, highlight_word)
    if highlighted and highlight_emoji:
        return f"{highlight_emoji} {text}", True

    return text, highlighted


def write_outputs(
    out_dir: Path,
    wav_path: Path,
    segments: list[Segment],
    emoji_map: dict[str, str],
    highlight_word: str = DEFAULT_HIGHLIGHT_WORD,
    highlight_emoji: str = DEFAULT_HIGHLIGHT_EMOJI,
) -> tuple[Path, Path]:
    json_path = out_dir / "transcript.json"
    tsv_path = out_dir / "transcript.tsv"

    rendered_segments = [
        (segment, *render_segment_text(segment.text, highlight_word, highlight_emoji)) for segment in segments
    ]

    payload = {
        "source_wav": str(wav_path),
        "emoji_map": emoji_map,
        "highlight": {
            "word": highlight_word,
            "emoji": highlight_emoji,
        },
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "speaker": segment.speaker,
                "emoji": emoji_map[segment.speaker],
                "text": segment.text,
                "display_text": display_text,
                "highlighted": highlighted,
            }
            for segment, display_text, highlighted in rendered_segments
        ],
    }

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    with tsv_path.open("w", encoding="utf-8", newline="\n") as file:
        file.write("start\tend\tspeaker\temoji\ttext\n")
        for segment, display_text, _highlighted in rendered_segments:
            file.write(
                "\t".join(
                    [
                        f"{segment.start:.3f}",
                        f"{segment.end:.3f}",
                        escape_tsv(segment.speaker),
                        escape_tsv(emoji_map[segment.speaker]),
                        escape_tsv(display_text),
                    ]
                )
                + "\n"
            )

    return json_path, tsv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create offline transcript files for REAPER.")
    parser.add_argument("--config", required=True, type=Path, help="Path to config.json.")
    parser.add_argument("--wav", required=True, type=Path, help="Path to the selected WAV file.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory for transcript outputs.")
    parser.add_argument(
        "--from-whisper-json",
        type=Path,
        help="Use an existing whisper.cpp JSON file instead of invoking whisper.cpp.",
    )
    parser.add_argument(
        "--clip-start",
        type=float,
        default=0.0,
        help="Start offset inside the WAV file, in seconds.",
    )
    parser.add_argument(
        "--clip-duration",
        type=float,
        help="Duration to transcribe from the WAV file, in seconds.",
    )
    parser.add_argument(
        "--item-index",
        type=int,
        default=1,
        help="1-based index of this item within the current REAPER run (for progress display).",
    )
    parser.add_argument(
        "--total-items",
        type=int,
        default=1,
        help="Total number of items being transcribed in this REAPER run (for progress display).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config = load_config(args.config)
        args.out_dir.mkdir(parents=True, exist_ok=True)

        max_chars = int(config.get("max_chars_per_item", 280))
        if max_chars <= 0:
            raise ValueError("max_chars_per_item must be positive")

        whisper_json = args.from_whisper_json
        if whisper_json is None:
            segments = transcribe_audio_chunks(
                config=config,
                wav_path=args.wav,
                out_dir=args.out_dir,
                clip_start=args.clip_start,
                clip_duration=args.clip_duration,
                max_chars=max_chars,
                item_index=args.item_index,
                total_items=args.total_items,
            )
        else:
            segments = load_whisper_segments(whisper_json, max_chars=max_chars)
        emoji_pool = config.get("speaker_emoji_pool") or DEFAULT_EMOJI_POOL
        emoji_map = build_emoji_map(segments, list(emoji_pool))
        json_path, tsv_path = write_outputs(
            args.out_dir,
            args.wav,
            segments,
            emoji_map,
            highlight_word=str(config.get("highlight_word", DEFAULT_HIGHLIGHT_WORD)),
            highlight_emoji=str(config.get("highlight_emoji", DEFAULT_HIGHLIGHT_EMOJI)),
        )
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"wrote {json_path}")
    print(f"wrote {tsv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
