import tempfile
import unittest
import wave
from pathlib import Path

from worker.sync_videos import (
    SyncResult,
    add_onset_emphasis,
    best_alignment_offset,
    best_correlation_offset,
    estimate_sync_offset,
    read_features,
    read_video_manifest,
    write_outputs,
)


def write_test_wav(path: Path, values: list[int], sample_rate: int = 100) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = b"".join(value.to_bytes(2, byteorder="little", signed=True) for value in values)
        wav_file.writeframes(frames)


class SyncVideosTests(unittest.TestCase):
    def test_manifest_ignores_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = Path(temp_dir) / "videos.txt"
            manifest.write_text('\n# comment\n"C:\\Videos\\a.mp4"\nC:\\Videos\\b.mov\n', encoding="utf-8")

            videos = read_video_manifest(manifest)

        self.assertEqual(videos, [Path("C:\\Videos\\a.mp4"), Path("C:\\Videos\\b.mov")])

    def test_best_correlation_offset_finds_embedded_query(self) -> None:
        reference = [0, 0, 1, 3, 6, 3, 1, 0, 0]
        query = [1, 3, 6, 3]

        offset, score, _second = best_correlation_offset(reference, query)

        self.assertEqual(offset, 2)
        self.assertGreater(score, 0.7)

    def test_add_onset_emphasis_preserves_length(self) -> None:
        self.assertEqual(len(add_onset_emphasis([0.0, 1.0, 0.5])), 3)

    def test_best_alignment_offset_supports_negative_offsets(self) -> None:
        reference = [1, 3, 6, 3, 1, 0, 0]
        video = [0, 0, 1, 3, 6, 3, 1]

        offset, score, _second = best_alignment_offset(reference, video, min_overlap=4)

        self.assertEqual(offset, -2)
        self.assertGreater(score, 0.7)

    def test_best_alignment_offset_supports_video_longer_than_reference(self) -> None:
        reference = [1, 3, 6, 3, 1]
        video = [0, 0, 1, 3, 6, 3, 1, 0, 0]

        offset, score, _second = best_alignment_offset(reference, video, min_overlap=4)

        self.assertEqual(offset, -2)
        self.assertGreater(score, 0.7)

    def test_read_features_creates_energy_features(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = Path(temp_dir) / "audio.wav"
            write_test_wav(wav_path, [0] * 10 + [1000] * 10, sample_rate=10)

            features = read_features(wav_path, frame_seconds=1.0)

        self.assertEqual(len(features), 2)
        self.assertGreater(features[1], features[0])

    def test_estimate_sync_offset_refines_known_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.wav"
            video = Path(temp_dir) / "video.wav"
            pattern = [0] * 50 + [1000] * 10 + [0] * 20 + [2000] * 10 + [0] * 50
            write_test_wav(reference, [0] * 200 + pattern + [0] * 200, sample_rate=100)
            write_test_wav(video, pattern, sample_rate=100)

            offset, confidence = estimate_sync_offset(
                reference,
                video,
                coarse_frame_seconds=0.5,
                refine_frame_seconds=0.1,
                refine_window_seconds=1.0,
            )

        self.assertIsNotNone(offset)
        self.assertAlmostEqual(offset or 0.0, 2.0, delta=0.2)
        self.assertGreater(confidence, 0.2)

    def test_estimate_sync_offset_can_return_negative_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.wav"
            video = Path(temp_dir) / "video.wav"
            pattern = [0] * 50 + [1000] * 10 + [0] * 20 + [2000] * 10 + [0] * 50
            write_test_wav(reference, pattern, sample_rate=100)
            write_test_wav(video, [0] * 200 + pattern, sample_rate=100)

            offset, confidence = estimate_sync_offset(
                reference,
                video,
                coarse_frame_seconds=0.5,
                refine_frame_seconds=0.1,
                refine_window_seconds=1.0,
                min_overlap_seconds=1.0,
            )

        self.assertIsNotNone(offset)
        self.assertAlmostEqual(offset or 0.0, -2.0, delta=0.2)
        self.assertGreater(confidence, 0.2)

    def test_write_outputs_writes_manual_and_synced_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            results = [
                SyncResult(Path("C:\\v1.mp4"), "synced", 12.0, 2.0, 0.8, ""),
                SyncResult(Path("C:\\v2.mp4"), "manual", 0.0, None, 0.0, "failed"),
            ]

            _json_path, tsv_path = write_outputs(out_dir, results)
            lines = tsv_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(lines[0], "video_path\tstatus\tinsert_position\toffset\tconfidence\treason")
        self.assertIn("synced", lines[1])
        self.assertIn("manual", lines[2])


if __name__ == "__main__":
    unittest.main()
