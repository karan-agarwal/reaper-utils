"""Offline video-to-reference-audio sync worker for REAPER.

The worker extracts low-rate mono audio with local FFmpeg, builds compact
energy/onset features, estimates each video's offset against the selected
reference audio, and writes TSV/JSON results for the REAPER Lua action.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


@dataclass(frozen=True)
class ReferenceSegment:
    """One WAV item from the REAPER reference track."""

    source_path: Path
    clip_start: float   # D_STARTOFFS: offset into the source file (seconds)
    clip_duration: float  # D_LENGTH: how long the item plays in the project (seconds)


@dataclass(frozen=True)
class SyncResult:
    video_path: Path
    status: str
    insert_position_seconds: float
    offset_seconds: float | None
    confidence: float
    reason: str


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not config.get("ffmpeg_binary"):
        raise ValueError("config is missing required key: ffmpeg_binary")

    return config


def escape_tsv(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def read_video_manifest(path: Path) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(f"videos manifest not found: {path}")

    videos: list[Path] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().strip('"')
        if stripped and not stripped.startswith("#"):
            videos.append(Path(stripped))

    if not videos:
        raise ValueError(f"videos manifest is empty: {path}")

    return videos


def read_reference_segments(path: Path) -> list[ReferenceSegment]:
    """Read a tab-separated reference segments file written by the Lua action.

    Format (one header line, then one row per REAPER track item):
        source_path<TAB>clip_start<TAB>clip_duration

    Paths are written raw (no escaping needed — Windows paths cannot contain tabs).
    """
    if not path.exists():
        raise FileNotFoundError(f"reference segments file not found: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"reference segments file is empty: {path}")

    expected_header = ["source_path", "clip_start", "clip_duration"]
    if lines[0].split("\t")[:3] != expected_header:
        raise ValueError(
            f"invalid reference segments header: expected {chr(9).join(expected_header)!r}, got {lines[0]!r}"
        )

    segments: list[ReferenceSegment] = []
    for line in lines[1:]:  # skip header
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            raise ValueError(
                f"expected 3 tab-separated columns in reference segments file, got {len(parts)}: {line!r}"
            )
        try:
            clip_start = float(parts[1])
            clip_duration = float(parts[2])
        except ValueError as exc:
            raise ValueError(f"non-numeric timing in reference segments file: {line!r}") from exc
        if clip_duration <= 0:
            raise ValueError(f"clip_duration must be positive, got {clip_duration!r}")
        segments.append(
            ReferenceSegment(source_path=Path(parts[0]), clip_start=clip_start, clip_duration=clip_duration)
        )

    if not segments:
        raise ValueError(f"reference segments file has no data rows: {path}")

    return segments


def concatenate_reference_audio(
    config: dict[str, Any],
    segments: list[ReferenceSegment],
    output_path: Path,
    sample_rate: int,
) -> Path:
    """Extract and join all reference segments into one mono s16 WAV.

    Single-segment fast path delegates to extract_audio() directly.
    Multi-segment path extracts each clip to a temp WAV then concatenates
    using the stdlib wave module — no subprocess or path-escaping involved.
    """
    if len(segments) == 1:
        seg = segments[0]
        return extract_audio(config, seg.source_path, output_path, sample_rate, seg.clip_start, seg.clip_duration)

    work_dir = output_path.parent
    temp_wavs: list[Path] = []
    try:
        for index, seg in enumerate(segments):
            temp_wav = work_dir / f"ref_seg_{index:04d}.wav"
            temp_wavs.append(temp_wav)  # register before extraction so finally cleans it up on failure
            extract_audio(config, seg.source_path, temp_wav, sample_rate, seg.clip_start, seg.clip_duration)

        # All segments are mono s16 at the same sample_rate — frame copy is safe.
        with wave.open(str(output_path), "wb") as out_wav:
            out_wav.setnchannels(1)
            out_wav.setsampwidth(2)
            out_wav.setframerate(sample_rate)
            for temp_wav in temp_wavs:
                with wave.open(str(temp_wav), "rb") as in_wav:
                    out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))
    finally:
        for temp_wav in temp_wavs:
            try:
                temp_wav.unlink()
            except FileNotFoundError:
                pass

    if not output_path.exists():
        raise FileNotFoundError(f"failed to produce concatenated reference audio: {output_path}")

    return output_path


def validate_video_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"video file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_VIDEO_SUFFIXES:
        warn(f"video file has an uncommon extension and will still be tried: {path}")


def extract_audio(
    config: dict[str, Any],
    input_path: Path,
    output_path: Path,
    sample_rate: int,
    clip_start: float = 0.0,
    clip_duration: float | None = None,
) -> Path:
    ffmpeg_binary = Path(config["ffmpeg_binary"])
    if not ffmpeg_binary.exists():
        raise FileNotFoundError(f"ffmpeg binary not found: {ffmpeg_binary}")
    if not input_path.exists():
        raise FileNotFoundError(f"input media file not found: {input_path}")
    if clip_start < 0:
        raise ValueError(f"clip start must be non-negative: {clip_start}")
    if clip_duration is not None and clip_duration <= 0:
        raise ValueError(f"clip duration must be positive: {clip_duration}")

    command = [
        str(ffmpeg_binary),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{clip_start:.6f}",
        "-i",
        str(input_path),
    ]
    if clip_duration is not None:
        command.extend(["-t", f"{clip_duration:.6f}"])

    command.extend(["-vn", "-ac", "1", "-ar", str(sample_rate), "-sample_fmt", "s16", str(output_path)])

    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {input_path}: {result.stderr.strip()}")
    if not output_path.exists():
        raise FileNotFoundError(f"ffmpeg did not produce audio file: {output_path}")

    return output_path


def read_features(path: Path, frame_seconds: float) -> list[float]:
    if frame_seconds <= 0:
        raise ValueError("feature frame size must be positive")

    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise ValueError(f"expected mono 16-bit WAV features input: {path}")

        sample_rate = wav_file.getframerate()
        frame_samples = max(1, int(round(sample_rate * frame_seconds)))
        features: list[float] = []

        while True:
            raw = wav_file.readframes(frame_samples)
            if not raw:
                break

            samples = array("h")
            samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
            if sys.byteorder != "little":
                samples.byteswap()
            if not samples:
                continue

            total_abs = sum(abs(sample) for sample in samples)
            features.append(math.log1p(total_abs / len(samples)))

    return add_onset_emphasis(features)


def add_onset_emphasis(values: list[float]) -> list[float]:
    if not values:
        return []

    emphasized = [values[0]]
    previous = values[0]
    for value in values[1:]:
        emphasized.append(value + 2.0 * max(0.0, value - previous))
        previous = value

    return emphasized


def normalize(values: list[float]) -> list[float]:
    if not values:
        return []

    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    stddev = math.sqrt(variance)
    if stddev <= 1e-9:
        return [0.0 for _ in values]

    return [(value - mean) / stddev for value in values]


def prefix_squares(values: list[float]) -> list[float]:
    prefix = [0.0]
    total = 0.0
    for value in values:
        total += value * value
        prefix.append(total)
    return prefix


def best_correlation_offset(
    reference: list[float],
    query: list[float],
    start_offset: int = 0,
    end_offset: int | None = None,
    exclusion_radius: int = 0,
) -> tuple[int | None, float, float]:
    if not reference or not query or len(query) > len(reference):
        return None, 0.0, 0.0

    reference_norm = normalize(reference)
    query_norm = normalize(query)
    query_power = sum(value * value for value in query_norm)
    if query_power <= 1e-9:
        return None, 0.0, 0.0

    max_offset = len(reference_norm) - len(query_norm)
    search_start = max(0, start_offset)
    search_end = min(max_offset, max_offset if end_offset is None else end_offset)
    if search_start > search_end:
        return None, 0.0, 0.0

    reference_power = prefix_squares(reference_norm)
    best_offset: int | None = None
    best_score = -1.0
    second_score = -1.0

    for offset in range(search_start, search_end + 1):
        window_power = reference_power[offset + len(query_norm)] - reference_power[offset]
        if window_power <= 1e-9:
            continue

        dot = 0.0
        for index, query_value in enumerate(query_norm):
            dot += reference_norm[offset + index] * query_value

        score = dot / math.sqrt(window_power * query_power)
        if score > best_score:
            if best_offset is None or abs(offset - best_offset) > exclusion_radius:
                second_score = best_score
            best_score = score
            best_offset = offset
        elif best_offset is None or abs(offset - best_offset) > exclusion_radius:
            second_score = max(second_score, score)

    return best_offset, max(0.0, best_score), max(0.0, second_score)


def overlap_for_lag(reference_len: int, query_len: int, lag: int) -> tuple[int, int, int]:
    reference_start = max(0, lag)
    query_start = max(0, -lag)
    overlap = min(reference_len - reference_start, query_len - query_start)
    return reference_start, query_start, max(0, overlap)


def correlation_score_at_lag(reference: list[float], query: list[float], lag: int, min_overlap: int) -> float | None:
    reference_start, query_start, overlap = overlap_for_lag(len(reference), len(query), lag)
    if overlap < min_overlap:
        return None

    reference_slice = normalize(reference[reference_start : reference_start + overlap])
    query_slice = normalize(query[query_start : query_start + overlap])
    reference_power = sum(value * value for value in reference_slice)
    query_power = sum(value * value for value in query_slice)
    if reference_power <= 1e-9 or query_power <= 1e-9:
        return None

    dot = sum(reference_slice[index] * query_slice[index] for index in range(overlap))
    return max(0.0, dot / math.sqrt(reference_power * query_power))


def best_partial_correlation_offset(
    reference: list[float],
    query: list[float],
    start_lag: int,
    end_lag: int,
    min_overlap: int,
    exclusion_radius: int = 0,
) -> tuple[int | None, float, float]:
    if not reference or not query:
        return None, 0.0, 0.0

    minimum_lag = -(len(query) - min_overlap)
    maximum_lag = len(reference) - min_overlap
    search_start = max(minimum_lag, start_lag)
    search_end = min(maximum_lag, end_lag)
    if search_start > search_end:
        return None, 0.0, 0.0

    best_lag: int | None = None
    best_score = -1.0
    second_score = -1.0
    for lag in range(search_start, search_end + 1):
        score = correlation_score_at_lag(reference, query, lag, min_overlap)
        if score is None:
            continue

        if score > best_score:
            if best_lag is None or abs(lag - best_lag) > exclusion_radius:
                second_score = best_score
            best_score = score
            best_lag = lag
        elif best_lag is None or abs(lag - best_lag) > exclusion_radius:
            second_score = max(second_score, score)

    return best_lag, max(0.0, best_score), max(0.0, second_score)


def best_alignment_offset(
    reference: list[float],
    query: list[float],
    min_overlap: int,
    exclusion_radius: int = 0,
) -> tuple[int | None, float, float]:
    candidates: list[tuple[int, float, float]] = []

    offset, _score, _second = best_correlation_offset(reference, query, exclusion_radius=exclusion_radius)
    if offset is not None:
        score = correlation_score_at_lag(reference, query, offset, min_overlap)
        if score is not None:
            candidates.append((offset, score, 0.0))

    reverse_offset, _reverse_score, _reverse_second = best_correlation_offset(
        query,
        reference,
        exclusion_radius=exclusion_radius,
    )
    if reverse_offset is not None:
        score = correlation_score_at_lag(reference, query, -reverse_offset, min_overlap)
        if score is not None:
            candidates.append((-reverse_offset, score, 0.0))

    negative_edge, negative_score, negative_second = best_partial_correlation_offset(
        reference,
        query,
        start_lag=-(len(query) - min_overlap),
        end_lag=-1,
        min_overlap=min_overlap,
        exclusion_radius=exclusion_radius,
    )
    if negative_edge is not None:
        candidates.append((negative_edge, negative_score, negative_second))

    positive_edge, positive_score, positive_second = best_partial_correlation_offset(
        reference,
        query,
        start_lag=max(0, len(reference) - len(query) + 1),
        end_lag=len(reference) - min_overlap,
        min_overlap=min_overlap,
        exclusion_radius=exclusion_radius,
    )
    if positive_edge is not None:
        candidates.append((positive_edge, positive_score, positive_second))

    if not candidates:
        return None, 0.0, 0.0

    return max(candidates, key=lambda candidate: candidate[1])


def estimate_sync_offset(
    reference_wav: Path,
    video_wav: Path,
    coarse_frame_seconds: float,
    refine_frame_seconds: float,
    refine_window_seconds: float,
    min_overlap_seconds: float = 10.0,
) -> tuple[float | None, float]:
    coarse_reference = read_features(reference_wav, coarse_frame_seconds)
    coarse_video = read_features(video_wav, coarse_frame_seconds)
    coarse_min_overlap = max(3, int(round(min_overlap_seconds / coarse_frame_seconds)))
    coarse_offset, coarse_score, _second_score = best_alignment_offset(
        coarse_reference,
        coarse_video,
        min_overlap=min(coarse_min_overlap, len(coarse_reference), len(coarse_video)),
        exclusion_radius=max(1, int(round(10.0 / coarse_frame_seconds))),
    )
    if coarse_offset is None:
        return None, 0.0

    fine_reference = read_features(reference_wav, refine_frame_seconds)
    fine_video = read_features(video_wav, refine_frame_seconds)
    coarse_offset_seconds = coarse_offset * coarse_frame_seconds
    search_center = int(round(coarse_offset_seconds / refine_frame_seconds))
    search_radius = max(1, int(round(refine_window_seconds / refine_frame_seconds)))
    fine_min_overlap = max(3, int(round(min_overlap_seconds / refine_frame_seconds)))
    fine_offset, fine_score, second_score = best_partial_correlation_offset(
        fine_reference,
        fine_video,
        start_lag=search_center - search_radius,
        end_lag=search_center + search_radius,
        min_overlap=min(fine_min_overlap, len(fine_reference), len(fine_video)),
        exclusion_radius=max(1, int(round(2.0 / refine_frame_seconds))),
    )
    if fine_offset is None:
        return coarse_offset_seconds, coarse_score

    ambiguity_penalty = 1.0
    if second_score > 0:
        ambiguity_penalty = max(0.25, min(1.0, (fine_score - second_score + 0.1) / 0.1))

    return fine_offset * refine_frame_seconds, max(0.0, min(1.0, fine_score * ambiguity_penalty))


def sync_videos(
    config: dict[str, Any],
    reference_segments: list[ReferenceSegment],
    videos: list[Path],
    out_dir: Path,
    reference_project_position: float,
) -> list[SyncResult]:
    sample_rate = int(config.get("video_sync_sample_rate", 4000))
    coarse_frame = float(config.get("video_sync_coarse_frame_seconds", 0.5))
    refine_frame = float(config.get("video_sync_refine_frame_seconds", 0.05))
    refine_window = float(config.get("video_sync_refine_window_seconds", 8.0))
    min_overlap = float(config.get("video_sync_min_overlap_seconds", 10.0))
    min_confidence = float(config.get("min_sync_confidence", 0.25))
    manual_gap = float(config.get("manual_sync_stage_gap_seconds", 5.0))

    work_dir = out_dir / "video-sync-audio"
    work_dir.mkdir(parents=True, exist_ok=True)
    reference_wav = work_dir / "reference.wav"
    concatenate_reference_audio(config, reference_segments, reference_wav, sample_rate)

    results: list[SyncResult] = []
    manual_fail_index = 0
    for index, video_path in enumerate(videos, start=1):
        video_wav = work_dir / f"video_{index:04d}.wav"
        try:
            validate_video_path(video_path)
            extract_audio(config, video_path, video_wav, sample_rate=sample_rate)
            offset, confidence = estimate_sync_offset(
                reference_wav,
                video_wav,
                coarse_frame_seconds=coarse_frame,
                refine_frame_seconds=refine_frame,
                refine_window_seconds=refine_window,
                min_overlap_seconds=min_overlap,
            )
            if offset is None:
                insert_position = reference_project_position + manual_fail_index * manual_gap
                manual_fail_index += 1
                results.append(
                    SyncResult(video_path, "manual", insert_position, None, 0.0, "no usable audio match")
                )
                continue

            insert_position = reference_project_position + offset
            if insert_position < 0:
                manual_position = reference_project_position + manual_fail_index * manual_gap
                manual_fail_index += 1
                results.append(
                    SyncResult(
                        video_path,
                        "manual",
                        manual_position,
                        offset,
                        confidence,
                        "matched before project start",
                    )
                )
            elif confidence >= min_confidence:
                results.append(SyncResult(video_path, "synced", insert_position, offset, confidence, ""))
            else:
                results.append(SyncResult(video_path, "manual", insert_position, offset, confidence, "low confidence"))
        except Exception as error:
            insert_position = reference_project_position + manual_fail_index * manual_gap
            manual_fail_index += 1
            results.append(SyncResult(video_path, "manual", insert_position, None, 0.0, str(error)))
        finally:
            try:
                video_wav.unlink()
            except FileNotFoundError:
                pass

    try:
        reference_wav.unlink()
    except FileNotFoundError:
        pass

    return results


def write_outputs(out_dir: Path, results: list[SyncResult]) -> tuple[Path, Path]:
    json_path = out_dir / "video_sync_results.json"
    tsv_path = out_dir / "video_sync_results.tsv"

    payload = {
        "results": [
            {
                "video_path": str(result.video_path),
                "status": result.status,
                "insert_position_seconds": result.insert_position_seconds,
                "offset_seconds": result.offset_seconds,
                "confidence": result.confidence,
                "reason": result.reason,
            }
            for result in results
        ]
    }
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    with tsv_path.open("w", encoding="utf-8", newline="\n") as file:
        file.write("video_path\tstatus\tinsert_position\toffset\tconfidence\treason\n")
        for result in results:
            offset = "" if result.offset_seconds is None else f"{result.offset_seconds:.6f}"
            file.write(
                "\t".join(
                    [
                        escape_tsv(str(result.video_path)),
                        result.status,
                        f"{result.insert_position_seconds:.6f}",
                        offset,
                        f"{result.confidence:.6f}",
                        escape_tsv(result.reason),
                    ]
                )
                + "\n"
            )

    return json_path, tsv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline sync videos to a selected REAPER audio track.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--reference-segments-file", required=True, type=Path)
    parser.add_argument("--videos-file", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--reference-project-position", required=True, type=float)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config = load_config(args.config)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        videos = read_video_manifest(args.videos_file)
        segments = read_reference_segments(args.reference_segments_file)
        results = sync_videos(
            config=config,
            reference_segments=segments,
            videos=videos,
            out_dir=args.out_dir,
            reference_project_position=args.reference_project_position,
        )
        json_path, tsv_path = write_outputs(args.out_dir, results)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"wrote {json_path}")
    print(f"wrote {tsv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
