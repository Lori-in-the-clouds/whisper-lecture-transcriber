from __future__ import annotations

import importlib
import os
import platform
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
    except (ModuleNotFoundError, ImportError) as exc:
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
                    source, preprocessing_mode=preprocessing_mode, on_phase=on_phase,
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
            source, preprocessing_mode, on_phase, should_cancel, should_pause, on_pause_state,
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

    def _preprocess_audio(self, source: Path, mode: str, on_phase: PhaseCallback,
                          should_cancel: Callable[[], bool], should_pause: Callable[[], bool],
                          on_pause_state: Callable[[bool], None]) -> Path:
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        output = self.processed_dir / f"{source.stem}_{mode}.mp4"
        duration = max(legacy.get_audio_duration(source), 0.01)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-af", legacy.get_filter_chain(mode),
            "-c:a", "aac", "-b:a", "128k", "-progress", "pipe:1", "-nostats", str(output),
        ]
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

    def _transcribe_mlx_chunks(self, audio: Path, model: str, language: str, on_update: UpdateCallback,
                               should_cancel: Callable[[], bool], should_pause: Callable[[], bool],
                               on_pause_state: Callable[[bool], None]) -> str:
        duration = max(legacy.get_audio_duration(audio), 0.01)
        with tempfile.TemporaryDirectory(prefix="lecture_chunks_") as temp_name:
            temp, pattern = Path(temp_name), Path(temp_name) / "chunk_%04d.wav"
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio),
                "-f", "segment", "-segment_time", "30", "-ac", "1", "-ar", "16000", str(pattern)]
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode != 0:
                raise RuntimeError(f"Unable to split the audio: {completed.stderr.strip()}")
            chunks = sorted(temp.glob("chunk_*.wav"))
            if not chunks:
                raise RuntimeError("No audio chunks were generated")
            output_dir, parts, elapsed = temp / "text", [], 0.0
            for chunk in chunks:
                self._wait_while_paused(should_pause, should_cancel, on_pause_state)
                if should_cancel():
                    raise TranscriptionCancelled()
                legacy.transcribe_mlx(chunk, output_dir=output_dir, model=model, use_preprocessing=False,
                    preprocessing_mode="balanced", language=None if language == "auto" else language,
                    keep_processed_audio=False, processed_dir=temp / "processed")
                if should_cancel():
                    raise TranscriptionCancelled()
                text = (output_dir / f"{chunk.stem}_balanced.txt").read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
                elapsed += legacy.get_audio_duration(chunk)
                on_update(" ".join(parts), min(99.0, elapsed / duration * 100))
            self._wait_while_paused(should_pause, should_cancel, on_pause_state)
            return " ".join(parts).strip()
