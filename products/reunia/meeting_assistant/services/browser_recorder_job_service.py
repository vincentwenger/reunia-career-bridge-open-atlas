from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from uuid import uuid4

from flask import current_app

from meeting_assistant.i18n import normalize_language
from meeting_assistant.repositories.recorder_job_store import LocalRecorderJobStore, RecorderJobStore
from meeting_assistant.services.browser_recorder_service import (
    BrowserRecorderService,
    _build_meeting_id,
    _normalize_started_at,
)
from meeting_assistant.services.recorder_job_queue import RedisRecorderJobQueue
from meeting_assistant.utils.exceptions import (
    ApplicationError,
    PayloadTooLargeError,
    ResourceNotFoundError,
    ValidationError,
)


_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{16,80}$")
_TERMINAL_STATUSES = {"complete", "failed"}

_STAGE_MESSAGES = {
    "recording": "Recording in progress. Audio segments are being saved securely.",
    "uploading_segments": "Saving recorded audio segments securely.",
    "uploading": "Receiving and validating the recorded audio.",
    "queued": "Audio uploaded. Waiting for processing to begin.",
    "transcribing_microphone": "Transcribing the microphone recording.",
    "transcribing_speaker": "Transcribing the shared meeting audio.",
    "cleaning_transcript": "Removing silent, low-confidence, and suspicious repeated text.",
    "analyzing": "Generating the meeting summary, insights, and scorecard.",
    "saving": "Saving the completed meeting review.",
    "complete": "The meeting was processed and saved successfully.",
    "failed": "Processing stopped because an error occurred.",
}


class BrowserRecorderJobService:
    """Persist recorder jobs durably and dispatch them to the configured queue.

    Production uses S3 metadata/audio plus a Redis queue, so uploads, status polling,
    and processing survive worker changes and container restarts. Development and
    tests retain a local inline mode.
    """

    def __init__(
        self,
        recorder_service: BrowserRecorderService | None = None,
        job_store: RecorderJobStore | None = None,
    ) -> None:
        self.recorder_service = recorder_service or BrowserRecorderService()
        # Resolve the application-managed store lazily. This preserves the service's
        # testability outside an application context while production still uses S3.
        self._job_store = job_store

    @property
    def job_store(self) -> RecorderJobStore:
        configured_directory = Path(
            str(current_app.config.get("RECORDER_JOB_DIR", "/tmp/meeting-assistant-recorder-jobs"))
        )
        store = self._job_store or current_app.extensions.get("recorder_job_store")
        if store is None:
            store = LocalRecorderJobStore(configured_directory)
        elif (
            self._job_store is None
            and isinstance(store, LocalRecorderJobStore)
            and store.root.resolve() != configured_directory.resolve()
        ):
            # Tests and local callers may override RECORDER_JOB_DIR after app
            # initialization; honor that override without changing production S3.
            store = LocalRecorderJobStore(configured_directory)
        self._job_store = store
        return store

    def create_upload_session(
        self,
        *,
        user_id: str,
        started_at: str,
        requested_reference_id: str = "",
        prepared_meeting: dict[str, Any] | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Create a durable segmented-upload session before recording begins."""
        self.cleanup_expired_jobs()
        reference_id = _normalize_reference_id(requested_reference_id)
        self.job_store.create(reference_id)
        self._job_dir(reference_id).mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        job: dict[str, Any] = {
            "job_id": reference_id,
            "reference_id": reference_id,
            "user_id": user_id,
            "upload_mode": "segmented",
            "status": "recording",
            "stage": "recording",
            "stage_message": _STAGE_MESSAGES["recording"],
            "started_at": started_at,
            "prepared_meeting": _normalize_prepared_meeting(prepared_meeting),
            "language": normalize_language(language, default="en"),
            "created_at": now,
            "updated_at": now,
            "duration_seconds": 0.0,
            "total_size_bytes": 0,
            "sources": [],
            "events": [],
            "processing_cache": {},
        }
        self._append_event(job, "recording", _STAGE_MESSAGES["recording"])
        self._write_job(job)
        return self.public_job(job)

    def append_segment(
        self,
        *,
        job_id: str,
        user_id: str,
        source: str,
        sequence: Any,
        offset_seconds: Any,
        duration_seconds: Any,
        audio_segment,
    ) -> dict[str, Any]:
        job = self._owned_job(job_id, user_id)
        if str(job.get("status") or "") != "recording":
            raise ValidationError("This recording is no longer accepting audio segments.")

        normalized_source = str(source or "").strip().upper()
        if normalized_source not in {"MICROPHONE", "SPEAKER"}:
            raise ValidationError("The recording segment source is invalid.")
        normalized_sequence = _bounded_int(sequence, minimum=0, maximum=100000, name="sequence")
        normalized_offset = _bounded_float(
            offset_seconds, minimum=0.0, maximum=172800.0, name="offset_seconds"
        )
        normalized_duration = _bounded_float(
            duration_seconds, minimum=0.0, maximum=7200.0, name="duration_seconds"
        )
        if not audio_segment or not getattr(audio_segment, "filename", ""):
            raise ValidationError("No recording segment was received.")

        existing = next(
            (
                item
                for item in job.get("sources") or []
                if str(item.get("source") or "") == normalized_source
                and int(item.get("sequence") or 0) == normalized_sequence
            ),
            None,
        )
        if existing is not None:
            return {
                "status": "already_uploaded",
                "job_id": job_id,
                "reference_id": job_id,
                "segment": _public_source(existing),
                "total_size_bytes": int(job.get("total_size_bytes") or 0),
            }

        source_segments = [
            item
            for item in job.get("sources") or []
            if str(item.get("source") or "") == normalized_source
        ]
        max_segments = int(current_app.config["RECORDER_MAX_SEGMENTS_PER_SOURCE"])
        if len(source_segments) >= max_segments:
            raise PayloadTooLargeError(
                "This recording contains too many audio segments. Stop and process the meeting."
            )

        job_dir = self._job_dir(job_id)
        saved = self.recorder_service.save_upload(
            audio_segment,
            destination_directory=job_dir,
            source=f"SEGMENT-{normalized_source}-{normalized_sequence:04d}",
        )
        total_size = int(job.get("total_size_bytes") or 0) + saved.size_bytes
        max_total_size = int(current_app.config["RECORDER_MAX_TOTAL_BYTES"])
        if total_size > max_total_size:
            saved.path.unlink(missing_ok=True)
            raise PayloadTooLargeError(
                "The complete recording is too large to store safely. Stop and process the meeting."
            )

        source_record = {
            "source": normalized_source,
            "sequence": normalized_sequence,
            "offset_seconds": normalized_offset,
            "duration_seconds": normalized_duration,
            "path": str(saved.path),
            "filename": str(audio_segment.filename or f"segment-{normalized_sequence}.webm"),
            "mime_type": saved.mime_type,
            "size_bytes": saved.size_bytes,
            "uploaded_at": _utc_now(),
        }
        try:
            persisted = self.job_store.persist_source(job_id, source_record)
        except Exception:
            # Local stores retain the saved file as their durable copy, while S3
            # stores delete it only after a successful upload. Remove any remaining
            # temporary file if persistence fails before the job metadata is updated.
            saved.path.unlink(missing_ok=True)
            raise
        sources = list(job.get("sources") or [])
        sources.append(persisted)
        sources.sort(
            key=lambda item: (
                float(item.get("offset_seconds") or 0.0),
                0 if str(item.get("source") or "") == "SPEAKER" else 1,
                int(item.get("sequence") or 0),
            )
        )
        job["sources"] = sources
        job["total_size_bytes"] = total_size
        job["duration_seconds"] = max(
            float(job.get("duration_seconds") or 0.0),
            normalized_offset + normalized_duration,
        )
        job["stage"] = "recording"
        job["stage_message"] = _STAGE_MESSAGES["recording"]
        job["updated_at"] = _utc_now()
        self._write_job(job)
        current_app.logger.info(
            "Recorder job %s saved segment source=%s sequence=%s bytes=%s offset_seconds=%.3f",
            job_id,
            normalized_source,
            normalized_sequence,
            saved.size_bytes,
            normalized_offset,
        )
        return {
            "status": "uploaded",
            "job_id": job_id,
            "reference_id": job_id,
            "segment": _public_source(persisted),
            "total_size_bytes": total_size,
        }

    def finalize_upload_session(
        self,
        *,
        job_id: str,
        user_id: str,
        duration_seconds: Any = 0,
    ) -> dict[str, Any]:
        job = self._owned_job(job_id, user_id)
        status = str(job.get("status") or "")
        if status == "queued":
            # Re-dispatch safely when a previous finalize response was interrupted.
            # Redis enqueue is deduplicated, and inline jobs cannot remain queued
            # after a successful dispatch.
            return self._dispatch_job(job)
        if status in {"processing", "complete", "failed"}:
            # Returning the current terminal/active state lets a browser recover
            # when the original finalize response was lost after processing began.
            return self.public_job(job)
        if status != "recording":
            raise ValidationError("This recording cannot be finalized.")

        sources = list(job.get("sources") or [])
        if not any(str(item.get("source") or "") == "MICROPHONE" for item in sources):
            raise ValidationError("No microphone recording segments were uploaded.")
        _validate_segment_sequences(sources)

        requested_duration = _bounded_float(
            duration_seconds, minimum=0.0, maximum=172800.0, name="duration_seconds"
        )
        job["duration_seconds"] = max(
            requested_duration,
            float(job.get("duration_seconds") or 0.0),
        )
        job["status"] = "queued"
        job["stage"] = "queued"
        job["stage_message"] = _STAGE_MESSAGES["queued"]
        job["updated_at"] = _utc_now()
        self._append_event(job, "queued", _STAGE_MESSAGES["queued"])
        self._write_job(job)
        return self._dispatch_job(job)

    def discard_upload_session(self, *, job_id: str, user_id: str) -> None:
        job = self._owned_job(job_id, user_id)
        if str(job.get("status") or "") in {"processing", "complete"}:
            raise ValidationError("A recording that is already processing cannot be discarded.")
        self._remove_job_directory(job_id)

    def retry_job(self, *, job_id: str, user_id: str) -> dict[str, Any]:
        job = self._owned_job(job_id, user_id)
        status = str(job.get("status") or "")
        if status == "queued":
            return self._dispatch_job(job)
        if status in {"processing", "complete"}:
            return self.public_job(job)
        if status != "failed":
            raise ValidationError("This recording is not ready to retry.")
        if not job.get("sources"):
            raise ValidationError("The saved audio is no longer available for retry.")
        job.pop("error", None)
        job.pop("failure_status_code", None)
        job["status"] = "queued"
        job["stage"] = "queued"
        job["stage_message"] = _STAGE_MESSAGES["queued"]
        job["updated_at"] = _utc_now()
        self._append_event(job, "queued", "Processing retry requested.")
        self._write_job(job)
        return self._dispatch_job(job)

    def queue_meeting(
        self,
        *,
        user_id: str,
        started_at: str,
        microphone_audio,
        speaker_audio,
        requested_reference_id: str = "",
        prepared_meeting: dict[str, Any] | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        self.cleanup_expired_jobs()
        reference_id = _normalize_reference_id(requested_reference_id)
        self.job_store.create(reference_id)
        job_dir = self._job_dir(reference_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        now = _utc_now()
        job: dict[str, Any] = {
            "job_id": reference_id,
            "reference_id": reference_id,
            "user_id": user_id,
            "status": "uploading",
            "stage": "uploading",
            "stage_message": _STAGE_MESSAGES["uploading"],
            "started_at": started_at,
            "prepared_meeting": _normalize_prepared_meeting(prepared_meeting),
            "language": normalize_language(language, default="en"),
            "created_at": now,
            "updated_at": now,
            "sources": [],
            "events": [],
            "processing_cache": {},
        }
        self._append_event(job, "uploading", _STAGE_MESSAGES["uploading"])
        self._write_job(job)

        uploads = [
            ("MICROPHONE", microphone_audio),
            ("SPEAKER", speaker_audio),
        ]
        uploads = [(source, upload) for source, upload in uploads if upload and upload.filename]
        if not uploads:
            self._remove_job_directory(reference_id)
            raise ValidationError("No browser audio was received.")

        try:
            for source, upload in uploads:
                stage_started = time.perf_counter()
                saved = self.recorder_service.save_upload(
                    upload,
                    destination_directory=job_dir,
                    source=source,
                )
                source_record = {
                    "source": source,
                    "path": str(saved.path),
                    "filename": upload.filename,
                    "mime_type": saved.mime_type,
                    "size_bytes": saved.size_bytes,
                }
                job["sources"].append(
                    self.job_store.persist_source(reference_id, source_record)
                )
                current_app.logger.info(
                    "Recorder job %s uploaded source=%s bytes=%s duration_ms=%s",
                    reference_id,
                    source,
                    saved.size_bytes,
                    int((time.perf_counter() - stage_started) * 1000),
                )

            job["status"] = "queued"
            job["stage"] = "queued"
            job["stage_message"] = _STAGE_MESSAGES["queued"]
            job["updated_at"] = _utc_now()
            self._append_event(job, "queued", _STAGE_MESSAGES["queued"])
            self._write_job(job)
        except Exception:
            self._remove_job_directory(reference_id)
            raise

        return self._dispatch_job(job)

    def _dispatch_job(self, job: dict[str, Any]) -> dict[str, Any]:
        reference_id = str(job["job_id"])
        queue_backend = str(
            current_app.config.get("RECORDER_JOB_QUEUE_BACKEND", "inline")
        ).strip().lower()
        if queue_backend == "redis":
            queue = current_app.extensions.get("recorder_job_queue")
            if not isinstance(queue, RedisRecorderJobQueue):
                raise RuntimeError("Redis recorder job queue is not initialized.")
            queue.enqueue(reference_id)
            return self.public_job(job)
        if queue_backend == "inline":
            self.process_job(reference_id)
            return self.public_job(self._read_job(reference_id))
        raise RuntimeError("RECORDER_JOB_QUEUE_BACKEND must be 'inline' or 'redis'.")

    def get_job(self, *, job_id: str, user_id: str) -> dict[str, Any]:
        job = self._read_job(job_id)
        if str(job.get("user_id")) != str(user_id):
            raise ResourceNotFoundError("Recorder job not found.")
        return self.public_job(job)

    def process_job(self, job_id: str) -> None:
        job = self._read_job(job_id)
        if job.get("status") in _TERMINAL_STATUSES:
            return

        existing_result = self._existing_meeting_result(job)
        if existing_result is not None:
            self._complete_job(job_id, existing_result, started_at=time.perf_counter())
            return

        current_app.logger.info(
            "Recorder job %s processing started user_id=%s sources=%s",
            job_id,
            job.get("user_id"),
            len(job.get("sources") or []),
        )
        overall_started = time.perf_counter()
        stage_started = time.perf_counter()
        current_stage = "queued"

        def progress(stage: str, message: str | None = None) -> None:
            nonlocal stage_started, current_stage, job
            now = time.perf_counter()
            if current_stage:
                current_app.logger.info(
                    "Recorder job %s stage=%s duration_ms=%s",
                    job_id,
                    current_stage,
                    int((now - stage_started) * 1000),
                )
            current_stage = stage
            stage_started = now
            job = self._read_job(job_id)
            job["status"] = "processing"
            job["stage"] = stage
            job["stage_message"] = message or _STAGE_MESSAGES.get(stage, "Processing the meeting.")
            job["updated_at"] = _utc_now()
            self._append_event(job, stage, job["stage_message"])
            self._write_job(job)


        def save_processing_cache(bucket: str, key: str, value: dict[str, Any]) -> None:
            latest = self._read_job(job_id)
            processing_cache = dict(latest.get("processing_cache") or {})
            bucket_values = dict(processing_cache.get(bucket) or {})
            bucket_values[str(key)] = value
            processing_cache[bucket] = bucket_values
            latest["processing_cache"] = processing_cache
            latest["updated_at"] = _utc_now()
            self._write_job(latest)

        try:
            working_directory = self._job_dir(job_id)
            working_directory.mkdir(parents=True, exist_ok=True)
            source_paths = self.job_store.materialize_sources(job, working_directory)
            result = self.recorder_service.create_meeting_from_paths(
                user_id=str(job["user_id"]),
                started_at=str(job.get("started_at") or ""),
                source_paths=source_paths,
                progress_callback=progress,
                reference_id=job_id,
                prepared_meeting=job.get("prepared_meeting") or None,
                language=str(job.get("language") or "en"),
                processing_cache=job.get("processing_cache") or {},
                cache_callback=save_processing_cache,
            )

            current_app.logger.info(
                "Recorder job %s stage=%s duration_ms=%s",
                job_id,
                current_stage,
                int((time.perf_counter() - stage_started) * 1000),
            )
            self._complete_job(job_id, result, started_at=overall_started)
        except ApplicationError as exc:
            current_app.logger.info(
                "Recorder job %s stage=%s duration_ms=%s failed=true",
                job_id,
                current_stage,
                int((time.perf_counter() - stage_started) * 1000),
            )
            self._mark_failed(job_id, exc, current_stage, exc.status_code)
        except Exception as exc:  # pragma: no cover - defensive boundary around worker
            current_app.logger.info(
                "Recorder job %s stage=%s duration_ms=%s failed=true",
                job_id,
                current_stage,
                int((time.perf_counter() - stage_started) * 1000),
            )
            current_app.logger.exception(
                "Recorder job %s failed unexpectedly stage=%s", job_id, current_stage
            )
            self._mark_failed(
                job_id,
                exc,
                current_stage,
                500,
                public_message="An unexpected server error occurred while processing the meeting.",
            )

    def _existing_meeting_result(self, job: dict[str, Any]) -> dict[str, Any] | None:
        if not current_app.config.get("RECORDER_JOB_IDEMPOTENCY_CHECK", False):
            return None
        try:
            timestamp = _normalize_started_at(str(job.get("started_at") or ""))
            meeting_id = _build_meeting_id(
                timestamp,
                reference_id=str(job.get("job_id") or ""),
            )
            repository = self.recorder_service.transcript_service.repository
            repository.get_owned(str(job.get("user_id") or ""), meeting_id, timestamp)
        except ResourceNotFoundError:
            return None
        except ApplicationError:
            return None
        except (ClientError, AttributeError):
            current_app.logger.exception(
                "Recorder job %s could not perform its idempotency lookup",
                job.get("job_id"),
            )
            return None
        return {
            "meeting_id": meeting_id,
            "timestamp": timestamp,
            "message": "The meeting had already been saved before processing restarted.",
            "source_count": len(
                {str(item.get("source") or "") for item in job.get("sources") or []}
            ),
            "segment_count": len(job.get("sources") or []),
        }

    def _complete_job(
        self,
        job_id: str,
        result: dict[str, Any],
        *,
        started_at: float,
    ) -> None:
        job = self._read_job(job_id)
        job.update(result)
        job.pop("processing_cache", None)
        job["status"] = "complete"
        job["stage"] = "complete"
        job["stage_message"] = _STAGE_MESSAGES["complete"]
        job["updated_at"] = _utc_now()
        job["completed_at"] = job["updated_at"]
        self._append_event(job, "complete", _STAGE_MESSAGES["complete"])
        self._write_job(job)
        self._delete_audio_files(job)
        current_app.logger.info(
            "Recorder job %s completed duration_ms=%s meeting_id=%s",
            job_id,
            int((time.perf_counter() - started_at) * 1000),
            result.get("meeting_id"),
        )

    def recoverable_job_ids(self) -> list[str]:
        try:
            return self.job_store.list_recoverable_jobs()
        except Exception:
            current_app.logger.exception("Could not inspect persisted recorder jobs")
            return []

    def cleanup_expired_jobs(self) -> None:
        retention = int(current_app.config["RECORDER_JOB_RETENTION_SECONDS"])
        try:
            self.job_store.cleanup(retention)
        except Exception:
            current_app.logger.exception("Could not clean up expired recorder jobs")

    def public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "job_id",
            "reference_id",
            "status",
            "stage",
            "stage_message",
            "created_at",
            "updated_at",
            "completed_at",
            "message",
            "meeting_id",
            "timestamp",
            "source_count",
            "segment_count",
            "duration_seconds",
            "total_size_bytes",
            "upload_mode",
            "transcript_quality",
            "quality_warning",
            "prepared_meeting_id",
            "prepared_meeting_title",
            "error",
            "failure_status_code",
            "events",
        }
        payload = {key: job[key] for key in allowed if key in job}
        payload["sources"] = [_public_source(item) for item in job.get("sources") or []]
        payload["segment_count"] = len(job.get("sources") or [])
        return payload

    def _mark_failed(
        self,
        job_id: str,
        error: Exception,
        stage: str,
        status_code: int,
        *,
        public_message: str | None = None,
    ) -> None:
        message = public_message or str(error) or "The meeting could not be processed."
        current_app.logger.exception(
            "Recorder job %s failed stage=%s status_code=%s error=%s",
            job_id,
            stage,
            status_code,
            error,
            exc_info=error,
        )
        job = self._read_job(job_id)
        job["status"] = "failed"
        job["stage"] = stage or "failed"
        job["stage_message"] = _STAGE_MESSAGES["failed"]
        job["error"] = message
        job["failure_status_code"] = int(status_code)
        job["updated_at"] = _utc_now()
        self._append_event(job, "failed", message)
        self._write_job(job)

    def _append_event(self, job: dict[str, Any], stage: str, message: str) -> None:
        events = list(job.get("events") or [])
        events.append({"timestamp": _utc_now(), "stage": stage, "message": message})
        job["events"] = events[-30:]

    def _delete_audio_files(self, job: dict[str, Any]) -> None:
        try:
            self.job_store.delete_sources(job)
        except Exception:
            current_app.logger.exception(
                "Recorder job %s could not remove persisted audio files",
                job.get("job_id"),
            )
        working_directory = self._job_dir(str(job.get("job_id") or ""))
        if working_directory.exists():
            for path in working_directory.iterdir():
                if path.name != "job.json" and path.is_file():
                    path.unlink(missing_ok=True)

    def _jobs_root(self) -> Path:
        root = Path(str(current_app.config["RECORDER_JOB_DIR"]))
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _job_dir(self, job_id: str) -> Path:
        if not _JOB_ID_PATTERN.fullmatch(str(job_id or "")):
            raise ResourceNotFoundError("Recorder job not found.")
        return self._jobs_root() / job_id

    def _read_job(self, job_id: str) -> dict[str, Any]:
        try:
            return self.job_store.read(job_id)
        except (OSError, FileNotFoundError, json.JSONDecodeError, ClientError) as exc:
            raise ResourceNotFoundError("Recorder job not found.") from exc

    def _owned_job(self, job_id: str, user_id: str) -> dict[str, Any]:
        job = self._read_job(job_id)
        if str(job.get("user_id") or "") != str(user_id):
            raise ResourceNotFoundError("Recorder job not found.")
        return job

    def _write_job(self, job: dict[str, Any]) -> None:
        self.job_store.write(job)

    def _remove_job_directory(self, job_id: str) -> None:
        self.job_store.remove(job_id)
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)


def _public_source(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": item.get("source"),
        "sequence": int(item.get("sequence") or 0),
        "offset_seconds": float(item.get("offset_seconds") or 0.0),
        "duration_seconds": float(item.get("duration_seconds") or 0.0),
        "filename": item.get("filename"),
        "mime_type": item.get("mime_type"),
        "size_bytes": int(item.get("size_bytes") or 0),
    }


def _validate_segment_sequences(sources: list[dict[str, Any]]) -> None:
    for source in {str(item.get("source") or "") for item in sources}:
        sequences = sorted(
            int(item.get("sequence") or 0)
            for item in sources
            if str(item.get("source") or "") == source
        )
        if sequences and sequences != list(range(sequences[-1] + 1)):
            raise ValidationError(
                f"One or more {source.lower()} recording segments are missing. Retry the upload."
            )


def _bounded_int(value: Any, *, minimum: int, maximum: int, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"The {name} value is invalid.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValidationError(f"The {name} value is invalid.")
    return parsed


def _bounded_float(value: Any, *, minimum: float, maximum: float, name: str) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"The {name} value is invalid.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValidationError(f"The {name} value is invalid.")
    return parsed


def _normalize_prepared_meeting(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    meeting_id = str(value.get("id") or "").strip()[:160]
    if not meeting_id:
        return {}
    participants = value.get("participants") or []
    if not isinstance(participants, list):
        participants = []
    return {
        "id": meeting_id,
        "title": str(value.get("title") or "").strip()[:240],
        "scheduled_at": str(value.get("scheduled_at") or "").strip()[:80],
        "participants": [str(item).strip()[:200] for item in participants if str(item).strip()][:100],
        "purpose": str(value.get("purpose") or "").strip()[:2000],
    }


def _normalize_reference_id(value: str) -> str:
    candidate = str(value or "").strip()
    if candidate and _JOB_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
