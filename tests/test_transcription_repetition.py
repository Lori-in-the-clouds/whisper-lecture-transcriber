from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from transcription_service import TranscriptionService, legacy


class RepetitionTests(unittest.TestCase):
    def test_screenshot_loop_is_detected_with_punctuation_variations(self):
        self.assertTrue(legacy.has_repetition_loop(
            "Intro. " + "you can, " * 15 + "you can you can, " * 8
        ))

    def test_single_word_and_longer_phrase_loops_are_detected(self):
        self.assertTrue(legacy.has_repetition_loop("yes " * 30))
        self.assertTrue(legacy.has_repetition_loop("this is a repeated sentence. " * 10))

    def test_normal_speech_and_short_emphasis_are_preserved(self):
        for text in ("", "Sì, sì, sì. Ora passiamo al prossimo argomento.",
                     "You can use a table. You can also use a graph.",
                     "you can, " * 4):
            with self.subTest(text=text):
                self.assertFalse(legacy.has_repetition_loop(text))

    def test_fallback_is_enabled_and_loop_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            audio.touch()
            output = root / "text"
            with patch.object(legacy, "get_audio_duration", return_value=30), \
                 patch.object(legacy, "WhisperProgressBar"), \
                 patch.object(legacy.mlx_whisper, "transcribe",
                              return_value={"text": "you can, " * 100}) as decode:
                with self.assertRaises(legacy.RepetitiveTranscriptionError):
                    legacy.transcribe_mlx(audio, output_dir=output,
                                          processed_dir=root / "processed",
                                          use_preprocessing=False)
                self.assertGreater(len(decode.call_args.kwargs["temperature"]), 1)
                self.assertFalse(decode.call_args.kwargs["condition_on_previous_text"])
                self.assertEqual(list(output.glob("*.txt")), [])

    def test_chunks_do_not_receive_generated_text_as_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result.txt"
            result.write_text("A normal sentence.", encoding="utf-8")
            service = TranscriptionService(root / "processed", engine="mlx")
            with patch.object(legacy, "get_audio_duration", return_value=60), \
                 patch("transcription_service.subprocess.run") as ffmpeg, \
                 patch.object(legacy, "transcribe_mlx", return_value=result) as decode:
                ffmpeg.return_value.returncode = 0
                service._transcribe_mlx_chunks(
                    root / "audio.wav", "Large", "en", lambda *_: None,
                    lambda: False, lambda: False, lambda _: None,
                )
                self.assertEqual(decode.call_count, 3)
                for call in decode.call_args_list:
                    self.assertIsNone(call.kwargs.get("initial_prompt"))

    def test_persistent_loop_reports_audio_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            service = TranscriptionService(Path(directory), engine="mlx")
            with patch.object(legacy, "get_audio_duration", return_value=60), \
                 patch("transcription_service.subprocess.run") as ffmpeg, \
                 patch.object(legacy, "transcribe_mlx",
                              side_effect=legacy.RepetitiveTranscriptionError("loop")):
                ffmpeg.return_value.returncode = 0
                with self.assertRaisesRegex(legacy.RepetitiveTranscriptionError, "0.0–30.0 s"):
                    service._transcribe_mlx_chunks(
                        Path(directory) / "audio.wav", "Large", "en", lambda *_: None,
                        lambda: False, lambda: False, lambda _: None,
                    )


if __name__ == "__main__":
    unittest.main()
