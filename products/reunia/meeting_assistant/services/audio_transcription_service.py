"""Focused audio transcription for Adaptive Mock Interview answers."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import current_app
from openai import OpenAI
from werkzeug.datastructures import FileStorage

from meeting_assistant.i18n import transcription_language
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.services.ai_cost_control_service import (
    AICostControlService,
    raise_if_openai_limited,
)
from meeting_assistant.utils.exceptions import (
    ExternalServiceError,
    PayloadTooLargeError,
    ValidationError,
)

_ALLOWED_MIME_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "video/webm",
    "video/mp4",
}

_MIME_EXTENSIONS = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


class ShortAudioTranscriptionService:
    """Transcribe one short mock-interview answer and delete its temporary file."""

    def __init__(self, client: OpenAI | None = None) -> None:
        self._client = client

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI()
        return self._client

    def transcribe_upload(
        self,
        upload: FileStorage | None,
        *,
        source: str,
        user_id: str = "",
        reference_id: str = "",
        language: str | None = None,
    ) -> dict[str, Any]:
        if not upload or not upload.filename:
            raise ValidationError("No mock-interview audio answer was received.")

        normalized_source = str(source or "").strip().upper()
        if normalized_source not in {"MICROPHONE", "SPEAKER"}:
            raise ValidationError("The audio source must be microphone or speaker.")

        path, size_bytes = self._save_upload(upload, source=normalized_source)
        try:
            payload = self._transcribe(
                path,
                source=normalized_source,
                user_id=user_id,
                reference_id=reference_id,
                language=language,
            )
            text = str(payload.get("text") or "").strip()
            kept = 1 if text else 0
            return {
                "text": text,
                "quality": {
                    "total_segments": kept,
                    "kept_segments": kept,
                    "removed_no_speech": 0,
                    "removed_low_confidence": 0,
                    "removed_repetitions": 0,
                    "removed_total": 0,
                    "adjusted": False,
                },
                "language": payload.get("language"),
                "duration": payload.get("duration"),
                "size_bytes": size_bytes,
            }
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                current_app.logger.warning(
                    "Could not remove temporary mock-interview audio file %s",
                    path,
                )

    def _save_upload(self, upload: FileStorage, *, source: str) -> tuple[Path, int]:
        mime_type = str(upload.mimetype or "").lower().split(";", 1)[0].strip()
        if mime_type not in _ALLOWED_MIME_TYPES:
            raise ValidationError(
                "Unsupported audio format. Record the answer in Chrome or Edge and try again."
            )

        max_bytes = int(
            current_app.config.get("SHORT_AUDIO_MAX_FILE_BYTES", 24_000_000)
            or 24_000_000
        )
        temporary_file = tempfile.NamedTemporaryFile(
            prefix=f"mock-interview-{source.lower()}-",
            suffix=_MIME_EXTENSIONS.get(mime_type, ".webm"),
            delete=False,
        )
        path = Path(temporary_file.name)
        total_bytes = 0
        try:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise PayloadTooLargeError(
                        "The recorded answer is too large to transcribe safely. "
                        "Record a shorter answer and try again."
                    )
                temporary_file.write(chunk)
        except Exception:
            temporary_file.close()
            path.unlink(missing_ok=True)
            raise
        else:
            temporary_file.close()

        if total_bytes == 0:
            path.unlink(missing_ok=True)
            raise ValidationError("The recorded answer was empty.")
        return path, total_bytes

    def _transcribe(
        self,
        path: Path,
        *,
        source: str,
        user_id: str,
        reference_id: str,
        language: str | None,
    ) -> dict[str, Any]:
        model = str(
            current_app.config.get(
                "SHORT_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"
            )
            or "gpt-4o-mini-transcribe"
        ).strip()
        configured_language = str(
            language or current_app.config.get("AUDIO_TRANSCRIPTION_LANGUAGE", "") or ""
        ).strip()
        normalized_language = (
            transcription_language(configured_language) if configured_language else ""
        )
        request_kwargs: dict[str, Any] = {
            "model": model,
            "response_format": "json",
        }
        if normalized_language:
            request_kwargs["language"] = normalized_language

        estimated_seconds = float(
            current_app.config.get("SHORT_TRANSCRIPTION_ESTIMATED_SECONDS", 90) or 90
        )
        reservation = None
        if user_id:
            reservation = AICostControlService().reserve_transcription_request(
                user_id,
                feature="mock_interview_transcription",
                model=model,
                audio_seconds=max(1.0, estimated_seconds),
            )

        started = time.perf_counter()
        try:
            with path.open("rb") as audio_file:
                response = self.client.audio.transcriptions.create(
                    file=audio_file,
                    **request_kwargs,
                )
        except Exception as exc:
            if reservation is not None:
                reservation.release()
            raise_if_openai_limited(exc)
            current_app.logger.exception(
                "Mock interview transcription failed reference=%s source=%s",
                reference_id or "unavailable",
                source,
                exc_info=exc,
            )
            raise ExternalServiceError(
                "The audio transcription service could not process the answer."
            ) from exc

        payload = _response_payload(response)
        measured_duration = _safe_float(
            payload.get("duration"), estimated_seconds, minimum=0.0
        )
        if reservation is not None:
            reservation.settle(
                AICostControlService().transcription_cost_usd(
                    model, measured_duration
                )
            )
        if user_id:
            try:
                UsageMetricsService().record_transcription_usage(
                    user_id,
                    feature="mock_interview_transcription",
                    model=model,
                    audio_seconds=measured_duration,
                    event_id=(
                        f"short-transcription-{reference_id}"
                        if reference_id
                        else f"short-transcription-{uuid4().hex}"
                    ),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    source=source.lower(),
                )
            except Exception:
                current_app.logger.exception(
                    "Could not record mock interview transcription usage reference=%s",
                    reference_id or "unavailable",
                )
        return payload


def _response_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, str):
        return {"text": response}
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        payload = response.model_dump()
        return payload if isinstance(payload, dict) else {"text": str(payload)}
    if hasattr(response, "to_dict"):
        payload = response.to_dict()
        return payload if isinstance(payload, dict) else {"text": str(payload)}
    return {
        "text": getattr(response, "text", ""),
        "language": getattr(response, "language", None),
        "duration": getattr(response, "duration", None),
    }


def _safe_float(value: Any, default: float, *, minimum: float) -> float:
    try:
        return max(minimum, float(value if value is not None else default))
    except (TypeError, ValueError):
        return default
