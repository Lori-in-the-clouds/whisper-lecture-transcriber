def transcript_output_stem(stem: str, language: str | None, use_preprocessing: bool,
                           preprocessing_mode: str) -> str:
    """Add the transcription settings that produced an output to its file stem."""
    language_tag = (language or "auto").lower()
    preprocessing_tag = preprocessing_mode if use_preprocessing else "none"
    return f"{stem}_{language_tag}_{preprocessing_tag}"
