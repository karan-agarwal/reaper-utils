import json
import tempfile
import unittest
import wave
from pathlib import Path

from worker.offline_transcribe import (
    Segment,
    build_emoji_map,
    chunk_ranges,
    contains_whole_word,
    escape_tsv,
    load_whisper_segments,
    media_duration_seconds,
    offset_segments,
    parse_timestamp,
    prepare_audio,
    render_segment_text,
    split_text_by_length,
    wav_duration_seconds,
    write_outputs,
)


class OfflineTranscribeTests(unittest.TestCase):
    def test_parse_timestamp_supports_common_formats(self) -> None:
        self.assertEqual(parse_timestamp(12.5), 12.5)
        self.assertEqual(parse_timestamp(1250, centiseconds=True), 12.5)
        self.assertEqual(parse_timestamp("00:01:02.500"), 62.5)
        self.assertEqual(parse_timestamp("00:01:02,500"), 62.5)

    def test_load_whisper_segments_supports_transcription_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "whisper.json"
            json_path.write_text(
                json.dumps(
                    {
                        "transcription": [
                            {"timestamps": {"from": "00:00:01.000", "to": "00:00:03.000"}, "text": "Hello."}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            segments = load_whisper_segments(json_path)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].start, 1.0)
        self.assertEqual(segments[0].end, 3.0)
        self.assertEqual(segments[0].text, "Hello.")

    def test_load_whisper_segments_skips_invalid_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "whisper.json"
            json_path.write_text(
                json.dumps(
                    {
                        "segments": [
                            {"start": 0.0, "end": 0.0, "text": "Bad"},
                            {"start": 1.0, "end": 2.0, "text": "Good"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            segments = load_whisper_segments(json_path)

        self.assertEqual(segments, [Segment(start=1.0, end=2.0, speaker="speaker_1", text="Good")])

    def test_emoji_map_is_stable_by_speaker_id(self) -> None:
        segments = [
            Segment(start=0, end=1, speaker="speaker_2", text="Second"),
            Segment(start=1, end=2, speaker="speaker_1", text="First"),
        ]

        emoji_map = build_emoji_map(segments, ["fox", "panda"])

        self.assertEqual(emoji_map, {"speaker_1": "fox", "speaker_2": "panda"})

    def test_split_text_by_length_preserves_words(self) -> None:
        parts = split_text_by_length("one two three four", max_chars=8)

        self.assertEqual(parts, ["one two", "three", "four"])

    def test_write_outputs_escapes_tsv_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            segment = Segment(start=0, end=1, speaker="speaker_1", text="hello\tworld\nagain")

            _, tsv_path = write_outputs(out_dir, Path("event.wav"), [segment], {"speaker_1": "fox"})

            lines = tsv_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(lines[1], "0.000\t1.000\tspeaker_1\tfox\thello\\tworld\\nagain")

    def test_escape_tsv_escapes_backslash_first(self) -> None:
        self.assertEqual(escape_tsv("a\\b\tc\nd"), "a\\\\b\\tc\\nd")

    def test_contains_whole_word_matches_next_but_not_annexed(self) -> None:
        self.assertTrue(contains_whole_word("Next, we discuss budget.", "next"))
        self.assertTrue(contains_whole_word("What comes next?", "next"))
        self.assertFalse(contains_whole_word("The annex is attached.", "next"))

    def test_render_segment_text_adds_highlight_emoji_for_next(self) -> None:
        display_text, highlighted = render_segment_text("Next we start.", "next", "arrow")

        self.assertTrue(highlighted)
        self.assertEqual(display_text, "arrow Next we start.")

    def test_prepare_audio_rejects_invalid_clip_duration_before_running_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "clip duration must be positive"):
                prepare_audio(
                    {"ffmpeg_binary": str(Path(temp_dir) / "ffmpeg.exe")},
                    Path(temp_dir) / "event.wav",
                    Path(temp_dir),
                    clip_start=0,
                    clip_duration=0,
                )

    def test_chunk_ranges_limits_large_audio_work_units(self) -> None:
        self.assertEqual(chunk_ranges(125.0, 60.0), [(0.0, 60.0), (60.0, 60.0), (120.0, 5.0)])

    def test_chunk_ranges_skips_tiny_floating_point_tail(self) -> None:
        self.assertEqual(chunk_ranges(60.0001, 60.0), [(0.0, 60.0)])

    def test_wav_duration_seconds_reads_uncompressed_wav_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = Path(temp_dir) / "sample.wav"
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * 32000)

            self.assertEqual(wav_duration_seconds(wav_path), 2.0)

    def test_media_duration_seconds_falls_back_to_wave_when_ffprobe_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = Path(temp_dir) / "sample.wav"
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(8000)
                wav_file.writeframes(b"\x00\x00" * 4000)

            duration = media_duration_seconds({"ffmpeg_binary": str(Path(temp_dir) / "ffmpeg.exe")}, wav_path)

        self.assertEqual(duration, 0.5)

    def test_media_duration_seconds_falls_back_to_wave_when_ffprobe_returns_invalid_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wav_path = temp_path / "sample.wav"
            ffprobe_path = temp_path / "ffprobe.bat"
            ffprobe_path.write_text("@echo off\necho N/A\n", encoding="utf-8")
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(8000)
                wav_file.writeframes(b"\x00\x00" * 8000)

            duration = media_duration_seconds(
                {
                    "ffmpeg_binary": str(temp_path / "ffmpeg.exe"),
                    "ffprobe_binary": str(ffprobe_path),
                },
                wav_path,
            )

        self.assertEqual(duration, 1.0)

    def test_offset_segments_moves_chunk_timestamps_to_clip_timestamps(self) -> None:
        segments = [Segment(start=1.5, end=3.0, speaker="speaker_1", text="Hello")]

        shifted = offset_segments(segments, 120.0)

        self.assertEqual(shifted, [Segment(start=121.5, end=123.0, speaker="speaker_1", text="Hello")])


if __name__ == "__main__":
    unittest.main()
