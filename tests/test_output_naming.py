import unittest

from output_naming import transcript_output_stem


class TranscriptOutputNameTests(unittest.TestCase):
    def test_name_contains_language_and_preprocessing_mode(self) -> None:
        self.assertEqual(
            transcript_output_stem("lecture", "en", True, "balanced"),
            "lecture_en_balanced",
        )

    def test_name_marks_disabled_preprocessing(self) -> None:
        self.assertEqual(
            transcript_output_stem("lecture", "it", False, "light"),
            "lecture_it_none",
        )

    def test_merged_name_uses_the_same_suffix(self) -> None:
        self.assertEqual(
            transcript_output_stem("Lezioni unite", "en", True, "aggressive"),
            "Lezioni unite_en_aggressive",
        )

    def test_automatic_language_is_named_when_whisper_receives_none(self) -> None:
        self.assertEqual(
            transcript_output_stem("lecture", None, False, "balanced"),
            "lecture_auto_none",
        )


if __name__ == "__main__":
    unittest.main()
