from __future__ import annotations

import importlib
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Callable

UpdateCallback = Callable[[str, float], None]
PhaseCallback = Callable[[str, float], None]


class TranscriptionCancelled(Exception):
    """Raised when the user stops the active transcription."""


def _load_legacy_module():
    """Load the untouched transcribe.py, with a harmless MLX stub on Linux."""
    try:
        return importlib.import_module("transcribe")
    except (ModuleNotFoundError, ImportError, RuntimeError) as exc:
        is_mlx_error = getattr(exc, "name", None) == "mlx_whisper" or "metal::" in str(exc).lower()
        if not is_mlx_error:
            raise
        for module_name in [name for name in sys.modules if name == "mlx_whisper" or name.startswith("mlx_whisper.")]:
            sys.modules.pop(module_name, None)
        stub = types.ModuleType("mlx_whisper")
        stub.transcribe = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("mlx-whisper is not available in this environment"))
        sys.modules["mlx_whisper"] = stub
        return importlib.import_module("transcribe")


legacy = _load_legacy_module()


class TranscriptionService:
    def __init__(self, processed_dir: Path, engine: str | None = None) -> None:
        requested = (engine or os.getenv("TRANSCRIPTION_ENGINE", "auto")).lower()
        native_mlx = platform.system() == "Darwin" and platform.machine() == "arm64"
        self.engine_name = "mlx" if requested == "mlx" or (requested == "auto" and native_mlx) else requested
        self.processed_dir = Path(processed_dir)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self._models: dict[str, object] = {}

    def transcribe(self, source: Path, *, model: str, language: str, use_preprocessing: bool,
                   preprocessing_mode: str, keep_processed_audio: bool, compute_device: str = "gpu",
                   preprocessing_sample_rate: str = "source",
                   on_update: UpdateCallback, on_phase: PhaseCallback = lambda _phase, _progress: None,
                   should_cancel: Callable[[], bool] = lambda: False,
                   should_pause: Callable[[], bool] = lambda: False,
                   on_pause_state: Callable[[bool], None] = lambda _paused: None) -> str:
        source, audio = source.resolve(), source.resolve()
        if self.engine_name != "mlx":
            raise RuntimeError("This app requires macOS with mlx-whisper installed")
        try:
            if use_preprocessing:
                audio = self.prepare_audio(
                    source, preprocessing_mode=preprocessing_mode,
                    preprocessing_sample_rate=preprocessing_sample_rate, on_phase=on_phase,
                    should_cancel=should_cancel, should_pause=should_pause,
                    on_pause_state=on_pause_state,
                )
            else:
                on_phase("preprocessing", 100.0)
            return self.transcribe_prepared(
                audio, model=model, language=language, compute_device=compute_device,
                on_update=on_update, on_phase=on_phase, should_cancel=should_cancel,
                should_pause=should_pause, on_pause_state=on_pause_state,
            )
        finally:
            if use_preprocessing and not keep_processed_audio:
                audio.unlink(missing_ok=True)

    def prepare_audio(self, source: Path, *, preprocessing_mode: str,
                      preprocessing_sample_rate: str = "source",
                      on_phase: PhaseCallback = lambda _phase, _progress: None,
                      should_cancel: Callable[[], bool] = lambda: False,
                      should_pause: Callable[[], bool] = lambda: False,
                      on_pause_state: Callable[[bool], None] = lambda _paused: None) -> Path:
        """Run the existing preprocessing unchanged, without occupying the MLX worker."""
        source = source.resolve()
        self._wait_while_paused(should_pause, should_cancel, on_pause_state)
        if should_cancel():
            raise TranscriptionCancelled()
        return self._preprocess_audio(
            source, preprocessing_mode, preprocessing_sample_rate, on_phase,
            should_cancel, should_pause, on_pause_state,
        )

    def transcribe_prepared(self, audio: Path, *, model: str, language: str,
                            compute_device: str = "gpu", on_update: UpdateCallback,
                            on_phase: PhaseCallback = lambda _phase, _progress: None,
                            should_cancel: Callable[[], bool] = lambda: False,
                            should_pause: Callable[[], bool] = lambda: False,
                            on_pause_state: Callable[[bool], None] = lambda _paused: None) -> str:
        """Transcribe an original or already-preprocessed file on the single MLX worker."""
        if self.engine_name != "mlx":
            raise RuntimeError("This app requires macOS with mlx-whisper installed")
        import mlx.core as mx
        previous_device = mx.default_device()
        mx.set_default_device(mx.gpu if compute_device == "gpu" else mx.cpu)
        try:
            if should_cancel():
                raise TranscriptionCancelled()
            self._wait_while_paused(should_pause, should_cancel, on_pause_state)
            on_phase("transcription", 0.0)
            return self._transcribe_mlx_chunks(
                Path(audio).resolve(), model, language, on_update, should_cancel,
                should_pause, on_pause_state,
            )
        finally:
            mx.set_default_device(previous_device)

    def health(self) -> dict[str, object]:
        return {"engine": self.engine_name, "native_mlx": self.engine_name == "mlx"}

    @staticmethod
    def is_metal_gpu_error(exc: BaseException) -> bool:
        message = str(exc).lower()
        return "metal::device" in message and any(fragment in message for fragment in (
            "unable to load kernel",
            "unable to reach mtlcompilerservice",
            "no metal device available",
        ))

    @staticmethod
    def _wait_while_paused(should_pause: Callable[[], bool], should_cancel: Callable[[], bool],
                           on_pause_state: Callable[[bool], None]) -> None:
        announced = False
        while should_pause():
            if should_cancel():
                raise TranscriptionCancelled()
            if not announced:
                on_pause_state(True)
                announced = True
            time.sleep(0.15)
        if announced:
            on_pause_state(False)

    def _preprocess_audio(self, source: Path, mode: str, sample_rate: str, on_phase: PhaseCallback,
                          should_cancel: Callable[[], bool], should_pause: Callable[[], bool],
                          on_pause_state: Callable[[bool], None]) -> Path:
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        # Keep the intermediate lossless. Re-encoding a recording to AAC before
        # transcription adds artifacts and makes the saved copy unpleasant to
        # listen to. The 16 kHz conversion happens only in the Whisper chunks.
        output = self.processed_dir / f"{source.stem}_{mode}.flac"
        duration = max(legacy.get_audio_duration(source), 0.01)
        target_sample_rate = sample_rate if sample_rate != "source" else legacy.get_audio_sample_rate(source)
        # loudnorm uses 192 kHz internally in dynamic mode. Resample explicitly
        # at the end so the FLAC encoder receives the requested/source rate.
        filter_chain = (
            f"{legacy.get_preprocessing_filter_chain(source, mode)},aresample={target_sample_rate},"
            f"aformat=sample_fmts=s16:sample_rates={target_sample_rate},asetnsamples=n=4096:p=0"
        )
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vn", "-ac", "1", "-ar", target_sample_rate,
        ]
        command.extend([
            "-af", filter_chain, "-c:a", "flac", "-compression_level", "5",
            "-progress", "pipe:1", "-nostats", str(output),
        ])
        on_phase("preprocessing", 0.0)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        monitor_done = threading.Event()
        process_paused = threading.Event()

        def monitor_controls() -> None:
            while not monitor_done.is_set() and process.poll() is None:
                if should_cancel():
                    if process_paused.is_set():
                        process.send_signal(signal.SIGCONT)
                        process_paused.clear()
                    process.terminate()
                    return
                if should_pause() and not process_paused.is_set():
                    process.send_signal(signal.SIGSTOP)
                    process_paused.set()
                    on_pause_state(True)
                elif not should_pause() and process_paused.is_set():
                    process.send_signal(signal.SIGCONT)
                    process_paused.clear()
                    on_pause_state(False)
                time.sleep(0.1)

        monitor = threading.Thread(target=monitor_controls, daemon=True, name="preprocessing-controls")
        monitor.start()
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if should_cancel():
                    process.terminate()
                    raise TranscriptionCancelled()
                key, _, value = line.strip().partition("=")
                if key in {"out_time_us", "out_time_ms"}:
                    try:
                        seconds = int(value) / 1_000_000
                        on_phase("preprocessing", min(99.0, seconds / duration * 100))
                    except ValueError:
                        pass
            return_code = process.wait()
            if return_code != 0:
                if should_cancel():
                    raise TranscriptionCancelled()
                error = process.stderr.read().strip() if process.stderr else ""
                raise RuntimeError(f"FFmpeg preprocessing failed: {error}")
            if should_cancel():
                raise TranscriptionCancelled()
            on_phase("preprocessing", 100.0)
            return output
        except BaseException:
            if process.poll() is None:
                if process_paused.is_set():
                    process.send_signal(signal.SIGCONT)
                process.terminate()
                process.wait()
            output.unlink(missing_ok=True)
            raise
        finally:
            monitor_done.set()
            if process_paused.is_set() and process.poll() is None:
                process.send_signal(signal.SIGCONT)
            monitor.join(timeout=0.5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def _transcribe_mlx_chunks(self, audio: Path, model: str, language: str, on_update: UpdateCallback,
                               should_cancel: Callable[[], bool], should_pause: Callable[[], bool],
                               on_pause_state: Callable[[bool], None]) -> str:
        duration = max(legacy.get_audio_duration(audio), 0.01)
        with tempfile.TemporaryDirectory(prefix="lecture_chunks_") as temp_name:
            temp = Path(temp_name)
            output_dir, transcript = temp / "text", ""
            chunk_duration, overlap = 30.0, 1.5
            stride, start, index = chunk_duration - overlap, 0.0, 0
            while start < duration:
                self._wait_while_paused(should_pause, should_cancel, on_pause_state)
                if should_cancel():
                    raise TranscriptionCancelled()
                chunk = temp / f"chunk_{index:04d}.wav"
                length = min(chunk_duration, duration - start)
                command = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start:.3f}", "-i", str(audio), "-t", f"{length:.3f}",
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(chunk),
                ]
                completed = subprocess.run(command, capture_output=True, text=True)
                if completed.returncode != 0:
                    raise RuntimeError(f"Unable to create audio chunk: {completed.stderr.strip()}")
                context = transcript[-500:].strip() or None
                transcript_file = legacy.transcribe_mlx(
                    chunk, output_dir=output_dir, model=model, use_preprocessing=False,
                    preprocessing_mode="balanced", language=None if language == "auto" else language,
                    keep_processed_audio=False, processed_dir=temp / "processed",
                    initial_prompt=context,
                )
                if should_cancel():
                    raise TranscriptionCancelled()
                text = Path(transcript_file).read_text(encoding="utf-8").strip()
                if text:
                    transcript = self._merge_overlapping_text(transcript, text)
                elapsed = min(duration, start + length)
                on_update(transcript, min(99.0, elapsed / duration * 100))
                if elapsed >= duration:
                    break
                start += stride
                index += 1
            self._wait_while_paused(should_pause, should_cancel, on_pause_state)
            return transcript.strip()

    @staticmethod
    def _merge_overlapping_text(existing: str, addition: str, max_overlap_words: int = 40) -> str:
        """Join overlapping chunks without repeating the shared boundary words."""
        if not existing.strip():
            return addition.strip()
        if not addition.strip():
            return existing.strip()
        left, right = existing.split(), addition.split()

        def normalized(word: str) -> str:
            return re.sub(r"[^\w']+", "", word, flags=re.UNICODE).casefold()

        limit = min(max_overlap_words, len(left), len(right))
        overlap = 0
        for size in range(limit, 0, -1):
            left_tail = [normalized(word) for word in left[-size:]]
            right_head = [normalized(word) for word in right[:size]]
            if all(left_tail) and left_tail == right_head:
                overlap = size
                break
        return " ".join(left + right[overlap:]).strip()
