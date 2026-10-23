from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from transcription_service import TranscriptionCancelled, TranscriptionService, legacy


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

    def test_persistent_loop_skips_only_after_recovery_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            service = TranscriptionService(Path(directory), engine="mlx")
            with patch.object(legacy, "get_audio_duration", return_value=60), \
                 patch("transcription_service.subprocess.run") as ffmpeg, \
                 patch.object(legacy, "transcribe_mlx",
                              side_effect=legacy.RepetitiveTranscriptionError("loop")) as decode:
                ffmpeg.return_value.returncode = 0
                updates = []
                text = service._transcribe_mlx_chunks(
                    Path(directory) / "audio.wav", "Large", "en", lambda *args: updates.append(args),
                    lambda: False, lambda: False, lambda _: None,
                )
                self.assertIn("UNTRANSCRIBED AUDIO 0.00–7.50 s", text)
                self.assertIn("57.00–60.00 s", text)
                self.assertEqual(decode.call_count, 30)
                self.assertEqual(len(updates), 3)
                self.assertEqual(updates[-1][1], 99)

    def test_second_pass_recovers_without_skipping(self):
        self._check_recovery(["loop", "Recovered."], "Recovered.", 2)

    def test_valid_text_after_skipped_chunk_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result.txt"
            result.write_text("The lecture continues normally.", encoding="utf-8")
            service = TranscriptionService(root, engine="mlx")
            with patch.object(legacy, "get_audio_duration", return_value=60), \
                 patch("transcription_service.subprocess.run") as ffmpeg, \
                 patch.object(legacy, "transcribe_mlx", side_effect=
                              [legacy.RepetitiveTranscriptionError("loop")] * 14 + [result, result]):
                ffmpeg.return_value.returncode = 0
                text = service._transcribe_mlx_chunks(
                    root / "audio.wav", "Large", "en", lambda *_: None,
                    lambda: False, lambda: False, lambda _: None,
                )
                self.assertIn("UNTRANSCRIBED AUDIO", text)
                self.assertTrue(text.endswith("The lecture continues normally."))

    def test_pause_between_retries_is_honored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result.txt"
            result.write_text("Recovered.", encoding="utf-8")
            service = TranscriptionService(root, engine="mlx")
            pause = [False]
            states = []

            def on_pause(paused):
                states.append(paused)
                pause[0] = False

            def decode(*args, **kwargs):
                if not states:
                    pause[0] = True
                    raise legacy.RepetitiveTranscriptionError("loop")
                return result

            with patch.object(legacy, "get_audio_duration", return_value=30), \
                 patch("transcription_service.subprocess.run") as ffmpeg, \
                 patch.object(legacy, "transcribe_mlx", side_effect=decode):
                ffmpeg.return_value.returncode = 0
                text = service._transcribe_mlx_chunks(
                    root / "audio.wav", "Large", "en", lambda *_: None,
                    lambda: False, lambda: pause[0], on_pause,
                )
                self.assertEqual(states, [True, False])
                self.assertEqual(text, "Recovered.")

    def test_split_recovers_both_halves_without_skipping(self):
        self._check_recovery(["loop", "loop", "First half.", "Second half."],
                             "First half. Second half.", 4)

    def test_only_unrecoverable_subinterval_is_skipped(self):
        self._check_recovery(["loop"] * 6 + ["Recovered right.", "Recovered second half."],
                             "[UNTRANSCRIBED AUDIO 0.00–7.50 s: persistent repetition "
                             "after all recovery attempts] Recovered right. Recovered second half.", 8)

    def _check_recovery(self, responses, expected, calls):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result.txt"
            service = TranscriptionService(root, engine="mlx")

            def decode(*args, **kwargs):
                response = responses.pop(0)
                if response == "loop":
                    raise legacy.RepetitiveTranscriptionError("loop")
                result.write_text(response, encoding="utf-8")
                return result

            with patch.object(legacy, "get_audio_duration", return_value=30), \
                 patch("transcription_service.subprocess.run") as ffmpeg, \
                 patch.object(legacy, "transcribe_mlx", side_effect=decode) as mocked:
                ffmpeg.return_value.returncode = 0
                text = service._transcribe_mlx_chunks(
                    root / "audio.wav", "Large", "en", lambda *_: None,
                    lambda: False, lambda: False, lambda _: None,
                )
                self.assertEqual(text, expected)
                self.assertEqual(mocked.call_count, calls)
                self.assertEqual(mocked.call_args_list[1].kwargs["temperature"][0], 0.2)

    def test_other_errors_still_propagate(self):
        self._check_interruption(RuntimeError("disk failure"), RuntimeError, lambda: False)

    def test_cancellation_during_retry_does_not_skip(self):
        cancelled = []

        def decode(*args, **kwargs):
            cancelled.append(True)
            raise legacy.RepetitiveTranscriptionError("loop")

        self._check_interruption(decode, TranscriptionCancelled, lambda: bool(cancelled))

    def _check_interruption(self, side_effect, exception, should_cancel):
        with tempfile.TemporaryDirectory() as directory:
            service = TranscriptionService(Path(directory), engine="mlx")
            with patch.object(legacy, "get_audio_duration", return_value=30), \
                 patch("transcription_service.subprocess.run") as ffmpeg, \
                 patch.object(legacy, "transcribe_mlx", side_effect=side_effect) as decode:
                ffmpeg.return_value.returncode = 0
                with self.assertRaises(exception):
                    service._transcribe_mlx_chunks(
                        Path(directory) / "audio.wav", "Large", "en", lambda *_: None,
                        should_cancel, lambda: False, lambda _: None,
                    )
                self.assertEqual(decode.call_count, 1)


if __name__ == "__main__":
    unittest.main()
