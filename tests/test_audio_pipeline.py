from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from transcription_service import TranscriptionService, legacy

get_filter_chain = legacy.get_filter_chain


class FilterProfileTests(unittest.TestCase):
    def test_light_profile_does_not_denoise_or_equalize(self) -> None:
        chain = get_filter_chain("light")
        self.assertNotIn("afftdn", chain)
        self.assertNotIn("equalizer", chain)
        self.assertNotIn("treble", chain)
        self.assertIn("I=-18", chain)

    def test_noise_reduction_increases_with_profile_strength(self) -> None:
        self.assertIn("nr=10", get_filter_chain("balanced"))
        self.assertNotIn("anlmdn", get_filter_chain("balanced"))
        strong = get_filter_chain("aggressive")
        self.assertIn("aresample=48000", strong)
        self.assertIn("arnndn", strong)
        self.assertIn("mix=0.75", strong)
        self.assertIn("afftdn=nr=16", strong)
        self.assertIn("somnolent-hogwash.rnnn", strong)
        self.assertNotIn("anlmdn", strong)

    def test_invalid_profile_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            get_filter_chain("unknown")


class TranscriptMergeTests(unittest.TestCase):
    def test_exact_overlap_is_removed(self) -> None:
        merged = TranscriptionService._merge_overlapping_text(
            "Questa è una lezione sulle reti sociali.",
            "sulle reti sociali, e sulla diffusione.",
        )
        self.assertEqual(
            merged,
            "Questa è una lezione sulle reti sociali. e sulla diffusione.",
        )

    def test_unrelated_text_is_preserved(self) -> None:
        merged = TranscriptionService._merge_overlapping_text("prima parte", "seconda parte")
        self.assertEqual(merged, "prima parte seconda parte")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class PreprocessingIntegrationTests(unittest.TestCase):
    @staticmethod
    def _write_tone(path: Path, sample_rate: int = 48000) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            frames = bytearray()
            for index in range(sample_rate * 2):
                sample = int(8000 * math.sin(2 * math.pi * 440 * index / sample_rate))
                frames.extend(struct.pack("<h", sample))
            output.writeframes(frames)

    @staticmethod
    def _write_stereo_quality_sample(path: Path, sample_rate: int = 16000) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            frames = bytearray()
            for index in range(sample_rate * 4):
                speech = int(6000 * math.sin(2 * math.pi * 440 * index / sample_rate)) if index % sample_rate >= sample_rate // 2 else 0
                left_noise = int(2500 * math.sin(2 * math.pi * 120 * index / sample_rate))
                right_noise = int(300 * math.sin(2 * math.pi * 120 * index / sample_rate))
                frames.extend(struct.pack("<hh", speech + left_noise, speech + right_noise))
            output.writeframes(frames)

    @staticmethod
    def _probe(path: Path) -> dict[str, object]:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "json", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return json.loads(completed.stdout)["streams"][0]

    def test_source_rate_is_preserved_in_lossless_flac(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            source = temp / "source.wav"
            self._write_tone(source)
            service = TranscriptionService(temp / "processed", engine="mlx")

            output = service.prepare_audio(
                source,
                preprocessing_mode="light",
                preprocessing_sample_rate="source",
            )

            self.assertEqual(output.suffix, ".flac")
            self.assertEqual(
                self._probe(output),
                {"codec_name": "flac", "sample_rate": "48000", "channels": 1},
            )

    def test_explicit_rate_is_applied_without_lossy_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            source = temp / "source.wav"
            self._write_tone(source)
            service = TranscriptionService(temp / "processed", engine="mlx")

            output = service.prepare_audio(
                source,
                preprocessing_mode="balanced",
                preprocessing_sample_rate="16000",
            )

            self.assertEqual(
                self._probe(output),
                {"codec_name": "flac", "sample_rate": "16000", "channels": 1},
            )

    def test_strong_profile_runs_the_bundled_rnnoise_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            source = temp / "source.wav"
            self._write_tone(source)
            service = TranscriptionService(temp / "processed", engine="mlx")

            output = service.prepare_audio(
                source,
                preprocessing_mode="aggressive",
                preprocessing_sample_rate="source",
            )

            self.assertEqual(
                self._probe(output),
                {"codec_name": "flac", "sample_rate": "48000", "channels": 1},
            )

    def test_cleaner_stereo_channel_is_selected_for_denoising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            source = Path(temp_name) / "stereo.wav"
            self._write_stereo_quality_sample(source)

            self.assertEqual(
                legacy.get_best_channel_filter(source, analysis_seconds=4),
                "pan=mono|c0=c1",
            )


if __name__ == "__main__":
    unittest.main()
