from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from transcription_service import TranscriptionCancelled, TranscriptionService

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("TRANSCRIBER_DATA_DIR", BASE_DIR / "data")).resolve()
UPLOAD_DIR, OUTPUT_DIR, PROCESSED_DIR = DATA_DIR / "uploads", DATA_DIR / "transcriptions", DATA_DIR / "processed_audio"
for directory in (UPLOAD_DIR, OUTPUT_DIR, PROCESSED_DIR):
    directory.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".mp4", ".mov"}


@dataclass
class Job:
    id: str
    group_id: str
    source_name: str
    source_path: str
    model: str
    language: str
    use_preprocessing: bool
    preprocessing_mode: str
    keep_processed_audio: bool
    merge_requested: bool
    merge_name: str
    compute_device: str
    status: str = "queued"
    phase: str = "waiting"
    progress: float = 0.0
    preprocessing_progress: float = 0.0
    transcription_progress: float = 0.0
    transcript: str = ""
    output_file: str | None = None
    error: str | None = None
    remove_requested: bool = False
    created_at: float = field(default_factory=time.time)


class QueueManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.group_order: dict[str, list[str]] = {}
        self.group_outputs: dict[str, str] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.pause_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.work: queue.Queue[str] = queue.Queue()
        self.service = TranscriptionService(PROCESSED_DIR)
        threading.Thread(target=self._worker, daemon=True, name="transcription-worker").start()

    def add(self, jobs: list[Job]) -> None:
        with self.changed:
            if jobs:
                self.group_order[jobs[0].group_id] = [job.id for job in jobs]
            for job in jobs:
                self.jobs[job.id] = job
                self.cancel_events[job.id] = threading.Event()
                self.pause_events[job.id] = threading.Event()
                self.order.append(job.id)
                self.work.put(job.id)
            self.changed.notify_all()

    def cancel(self, job_id: str) -> bool:
        with self.changed:
            job = self.jobs.get(job_id)
            if not job:
                return False
            target_ids = list(self.group_order.get(job.group_id, [])) if job.merge_requested else [job_id]
            targets = [self.jobs[target_id] for target_id in target_ids if target_id in self.jobs]
            if not targets:
                return False
            for target in targets:
                if target.status in {"processing", "pausing", "paused", "stopping"}:
                    self.cancel_events[target.id].set()
                    self.pause_events[target.id].clear()
                    target.remove_requested = True
                    target.status = "stopping"
                elif target.status == "queued":
                    self.cancel_events[target.id].set()
                    Path(target.source_path).unlink(missing_ok=True)
                    self._remove_job(target, finish_group=False)
                else:
                    self._remove_job(target, finish_group=False)
            self.changed.notify_all()
            return True

    def stop(self, job_id: str) -> bool:
        with self.changed:
            job = self.jobs.get(job_id)
            if not job or job.status not in {"processing", "pausing", "paused"}:
                return False
            self.cancel_events[job_id].set()
            self.pause_events[job_id].clear()
            job.status = "stopping"
            self.changed.notify_all()
            return True

    def pause(self, job_id: str) -> bool:
        with self.changed:
            job = self.jobs.get(job_id)
            if not job or job.status != "processing":
                return False
            self.pause_events[job_id].set()
            job.status = "pausing"
            self.changed.notify_all()
            return True

    def resume(self, job_id: str) -> bool:
        with self.changed:
            job = self.jobs.get(job_id)
            if not job or job.status not in {"pausing", "paused"}:
                return False
            self.pause_events[job_id].clear()
            job.status = "processing"
            self.changed.notify_all()
            return True

    def _remove_job(self, job: Job, *, finish_group: bool = True) -> None:
        group_id = job.group_id
        self.jobs.pop(job.id, None)
        self.cancel_events.pop(job.id, None)
        self.pause_events.pop(job.id, None)
        if job.id in self.order:
            self.order.remove(job.id)
        group_jobs = self.group_order.get(group_id, [])
        if job.id in group_jobs:
            group_jobs.remove(job.id)
        if not group_jobs:
            self.group_order.pop(group_id, None)
        elif finish_group:
            self._finish_group_if_ready(group_id)

    def reorder(self, requested_order: list[str]) -> bool:
        with self.changed:
            queued_ids = [job_id for job_id in self.order if self.jobs[job_id].status == "queued"]
            if len(requested_order) != len(queued_ids) or set(requested_order) != set(queued_ids):
                return False
            queued_positions = [index for index, job_id in enumerate(self.order) if self.jobs[job_id].status == "queued"]
            for index, job_id in zip(queued_positions, requested_order):
                self.order[index] = job_id
            positions = {job_id: index for index, job_id in enumerate(self.order)}
            for group_jobs in self.group_order.values():
                group_jobs.sort(key=lambda job_id: positions.get(job_id, len(positions)))
            with self.work.mutex:
                current = list(self.work.queue)
                remaining = [job_id for job_id in current if job_id not in requested_order]
                self.work.queue.clear()
                self.work.queue.extend(requested_order + remaining)
            self.changed.notify_all()
            return True

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"jobs": [asdict(self.jobs[job_id]) for job_id in self.order], "merged_outputs": dict(self.group_outputs)}

    def _worker(self) -> None:
        while True:
            job_id = self.work.get()
            try:
                self._run(job_id)
            finally:
                self.work.task_done()

    def _run(self, job_id: str) -> None:
        with self.changed:
            job = self.jobs.get(job_id)
            if job is None:
                return
            if job.status == "cancelled":
                Path(job.source_path).unlink(missing_ok=True)
                self._finish_group_if_ready(job.group_id)
                return
            job.status = "processing"
            job.phase = "preprocessing" if job.use_preprocessing else "transcription"
            self.changed.notify_all()

        def on_update(text: str, progress: float) -> None:
            with self.changed:
                job.transcript = text
                job.progress = max(job.progress, min(99.0, progress))
                job.transcription_progress = max(job.transcription_progress, min(99.0, progress))
                self.changed.notify_all()

        def on_phase(phase: str, progress: float) -> None:
            with self.changed:
                job.phase = phase
                if phase == "preprocessing":
                    job.preprocessing_progress = progress
                elif phase == "transcription":
                    job.transcription_progress = progress
                    job.progress = progress
                self.changed.notify_all()

        def on_pause_state(paused: bool) -> None:
            with self.changed:
                if job.status != "stopping":
                    job.status = "paused" if paused else "processing"
                self.changed.notify_all()

        try:
            final_text = self.service.transcribe(
                Path(job.source_path), model=job.model, language=job.language,
                use_preprocessing=job.use_preprocessing, preprocessing_mode=job.preprocessing_mode,
                keep_processed_audio=job.keep_processed_audio, compute_device=job.compute_device,
                on_update=on_update, on_phase=on_phase,
                should_cancel=self.cancel_events[job_id].is_set,
                should_pause=self.pause_events[job_id].is_set,
                on_pause_state=on_pause_state,
            )
            if self.cancel_events[job_id].is_set():
                raise TranscriptionCancelled()
            output_name = self._unique_output_name(Path(job.source_name).stem)
            (OUTPUT_DIR / output_name).write_text(final_text.strip() + "\n", encoding="utf-8")
            with self.changed:
                job.transcript, job.progress, job.output_file, job.status = final_text, 100.0, output_name, "completed"
                job.preprocessing_progress = 100.0
                job.transcription_progress = 100.0
                job.phase = "completed"
                self.changed.notify_all()
        except TranscriptionCancelled:
            with self.changed:
                if job.remove_requested:
                    self._remove_job(job)
                else:
                    job.status = "cancelled"
                    job.phase = "cancelled"
                    job.error = None
                self.changed.notify_all()
        except Exception as exc:
            with self.changed:
                job.error, job.status = str(exc), "failed"
                job.phase = "failed"
                self.changed.notify_all()
        finally:
            Path(job.source_path).unlink(missing_ok=True)
            self._finish_group_if_ready(job.group_id)

    def _finish_group_if_ready(self, group_id: str) -> None:
        with self.changed:
            jobs = [self.jobs[job_id] for job_id in self.group_order.get(group_id, [])]
            if not jobs or not all(job.status in {"completed", "failed", "cancelled"} for job in jobs):
                return
            if not jobs[0].merge_requested or any(job.status != "completed" for job in jobs):
                return
            sections = [f"-----------------\nPart {index}\n-----------------\n\n{job.transcript.strip()}" for index, job in enumerate(jobs, 1)]
            filename = self._unique_output_name(Path(jobs[0].merge_name or "trascrizione_unita").stem)
            (OUTPUT_DIR / filename).write_text("\n\n".join(sections) + "\n", encoding="utf-8")
            self.group_outputs[group_id] = filename
            self.changed.notify_all()

    def _unique_output_name(self, stem: str) -> str:
        safe = secure_filename(stem).strip("._") or "trascrizione"
        candidate, counter = f"{safe}.txt", 2
        while (OUTPUT_DIR / candidate).exists():
            candidate, counter = f"{safe}_{counter}.txt", counter + 1
        return candidate


app = Flask(__name__)
manager = QueueManager()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/jobs")
def create_jobs():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Select at least one audio file."}), 400
    model, language = request.form.get("model", "Turbo"), request.form.get("language", "it")
    mode = request.form.get("preprocessing_mode", "balanced")
    use_preprocessing = request.form.get("use_preprocessing", "true") == "true"
    keep_processed = request.form.get("keep_processed_audio", "false") == "true"
    merge_requested = request.form.get("merge_requested", "false") == "true"
    merge_name = request.form.get("merge_name", "trascrizione_unita")
    compute_device = request.form.get("compute_device", "gpu").lower()
    if model not in {"Turbo", "Large"} or mode not in {"light", "balanced", "aggressive"} or compute_device not in {"gpu", "cpu"}:
        return jsonify({"error": "Invalid configuration."}), 400
    if not re.fullmatch(r"[a-zA-Z-]{2,12}|auto", language):
        return jsonify({"error": "Invalid language code."}), 400
    invalid = [upload.filename or "audio" for upload in files if Path(upload.filename or "audio").suffix.lower() not in ALLOWED_EXTENSIONS]
    if invalid:
        return jsonify({"error": f"Unsupported format: {invalid[0]}"}), 400
    group_id, jobs = uuid.uuid4().hex, []
    for upload in files:
        original = upload.filename or "audio"
        extension = Path(original).suffix.lower()
        job_id = uuid.uuid4().hex
        stored_path = UPLOAD_DIR / f"{job_id}{extension}"
        upload.save(stored_path)
        jobs.append(Job(job_id, group_id, original, str(stored_path), model, language, use_preprocessing, mode, keep_processed, merge_requested, merge_name, compute_device))
    manager.add(jobs)
    return jsonify({"group_id": group_id, "jobs": [asdict(job) for job in jobs]}), 201


@app.delete("/api/jobs/<job_id>")
def cancel_job(job_id: str):
    if not manager.cancel(job_id):
        return jsonify({"error": "This item cannot be removed right now."}), 409
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/stop")
def stop_job(job_id: str):
    if not manager.stop(job_id):
        return jsonify({"error": "Only an active transcription can be stopped."}), 409
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/pause")
def pause_job(job_id: str):
    if not manager.pause(job_id):
        return jsonify({"error": "Only an active transcription can be paused."}), 409
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/resume")
def resume_job(job_id: str):
    if not manager.resume(job_id):
        return jsonify({"error": "Only a paused transcription can be resumed."}), 409
    return jsonify({"ok": True})


@app.patch("/api/queue")
def reorder_queue():
    payload = request.get_json(silent=True) or {}
    requested_order = payload.get("order")
    if not isinstance(requested_order, list) or not all(isinstance(item, str) for item in requested_order):
        return jsonify({"error": "Invalid queue order."}), 400
    if not manager.reorder(requested_order):
        return jsonify({"error": "The queue changed. Refresh and try again."}), 409
    return jsonify({"ok": True})


@app.get("/api/state")
def state():
    return jsonify(manager.snapshot())


@app.get("/api/events")
def events():
    def stream():
        last_payload = ""
        while True:
            payload = json.dumps(manager.snapshot(), ensure_ascii=False)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            else:
                yield ": keep-alive\n\n"
            with manager.changed:
                manager.changed.wait(timeout=2)
    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/download/<path:filename>")
def download(filename: str):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


@app.get("/health")
def health():
    details = manager.service.health()
    return jsonify({"status": "ok" if details.get("native_mlx") else "degraded", **details})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "7860")), threaded=True)
