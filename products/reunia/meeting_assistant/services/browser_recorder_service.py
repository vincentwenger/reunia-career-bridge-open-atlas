from __future__ import annotations

import hashlib
import inspect
import json
import re
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
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
from meeting_assistant.services.meeting_materials_service import MeetingMaterialsService
from meeting_assistant.services.transcript_service import TranscriptService
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

_NORMALIZE_TEXT_PATTERN = re.compile(r"[^a-z0-9]+", re.IGNORECASE)
ProgressCallback = Callable[[str, str | None], None]


@dataclass(frozen=True)
class TranscriptSegment:
    source: str
    start_seconds: float
    end_seconds: float
    text: str
    no_speech_probability: float | None = None
    average_log_probability: float | None = None
    compression_ratio: float | None = None


@dataclass(frozen=True)
class SavedUpload:
    path: Path
    mime_type: str
    size_bytes: int




@dataclass(frozen=True)
class RecordedSource:
    source: str
    path: Path
    offset_seconds: float = 0.0
    duration_seconds: float = 0.0
    sequence: int = 0


@dataclass(frozen=True)
class TranscriptQualityReport:
    total_segments: int
    kept_segments: int
    removed_no_speech: int
    removed_low_confidence: int
    removed_repetitions: int
    removed_segments: tuple[dict[str, Any], ...]

    @property
    def removed_total(self) -> int:
        return (
            self.removed_no_speech
            + self.removed_low_confidence
            + self.removed_repetitions
        )

    @property
    def adjusted(self) -> bool:
        return self.removed_total > 0

    def as_dict(self, *, include_removed_segments: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        payload["removed_total"] = self.removed_total
        payload["adjusted"] = self.adjusted
        if include_removed_segments:
            payload["removed_segments"] = list(self.removed_segments)
        else:
            payload.pop("removed_segments", None)
        return payload


class BrowserRecorderService:
    def __init__(
        self,
        transcript_service: TranscriptService | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self.transcript_service = transcript_service or TranscriptService()
        self._client = client

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI()
        return self._client

    def create_meeting(
        self,
        user_id: str,
        started_at: str,
        microphone_audio: FileStorage | None,
        speaker_audio: FileStorage | None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Synchronous compatibility path used by tests and non-browser callers."""
        uploads = [
            ("MICROPHONE", microphone_audio),
            ("SPEAKER", speaker_audio),
        ]
        uploads = [
            (source, upload)
            for source, upload in uploads
            if _has_upload(upload)
        ]
        if not uploads:
            raise ValidationError("No browser audio was received.")

        saved_uploads: list[SavedUpload] = []
        try:
            for source, upload in uploads:
                saved_uploads.append(self.save_upload(upload, source=source))
            return self.create_meeting_from_paths(
                user_id=user_id,
                started_at=started_at,
                source_paths=[
                    (source, saved.path)
                    for (source, _), saved in zip(uploads, saved_uploads)
                ],
                language=language,
            )
        finally:
            for saved in saved_uploads:
                try:
                    saved.path.unlink(missing_ok=True)
                except OSError:
                    current_app.logger.warning(
                        "Could not remove temporary recorder file %s", saved.path
                    )

    def create_meeting_from_paths(
        self,
        *,
        user_id: str,
        started_at: str,
        source_paths: list[Any],
        progress_callback: ProgressCallback | None = None,
        reference_id: str = "",
        prepared_meeting: dict[str, Any] | None = None,
        language: str | None = None,
        processing_cache: dict[str, Any] | None = None,
        cache_callback: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if not source_paths:
            raise ValidationError("No browser audio was received.")

        recorded_sources = _normalize_recorded_sources(source_paths)
        source_totals: dict[str, int] = defaultdict(int)
        for item in recorded_sources:
            source_totals[item.source] += 1
        source_positions: dict[str, int] = defaultdict(int)

        raw_segments: list[TranscriptSegment] = []
        raw_responses: dict[str, Any] = {}
        for item in recorded_sources:
            source = item.source
            path = item.path
            source_positions[source] += 1
            stage = (
                "transcribing_microphone"
                if source == "MICROPHONE"
                else "transcribing_speaker"
            )
            source_label = (
                "microphone" if source == "MICROPHONE" else "shared meeting audio"
            )
            message = (
                f"Transcribing {source_label} segment "
                f"{source_positions[source]} of {source_totals[source]}."
            )
            if progress_callback:
                progress_callback(stage, message)
            stage_started = time.perf_counter()
            cache_key = _transcription_cache_key(
                path,
                source=source,
                model=str(current_app.config.get("FINAL_TRANSCRIPTION_MODEL") or "whisper-1"),
                language=language,
            )
            cached_transcription = (
                ((processing_cache or {}).get("transcriptions") or {}).get(cache_key)
            )
            if isinstance(cached_transcription, dict):
                local_segments = _deserialize_transcript_segments(
                    cached_transcription.get("segments"), source
                )
                source_response = dict(cached_transcription.get("response") or {})
                current_app.logger.info(
                    "Recorder job %s reused cached transcription source=%s sequence=%s",
                    reference_id or "synchronous",
                    source,
                    item.sequence,
                )
            else:
                local_segments, source_response = self._transcribe(
                    path,
                    source,
                    user_id=user_id,
                    reference_id=reference_id,
                    language=language,
                    feature="meeting_transcription",
                    audio_duration_seconds=item.duration_seconds,
                    event_id=(
                        f"transcription-{reference_id}-{source.lower()}-{item.sequence}"
                        if reference_id
                        else f"transcription-{uuid4().hex}"
                    ),
                )
                if cache_callback:
                    cache_callback(
                        "transcriptions",
                        cache_key,
                        {
                            "segments": [asdict(segment) for segment in local_segments],
                            "response": _compact_transcription_response(source_response),
                        },
                    )
            source_segments = [
                _offset_transcript_segment(segment, item.offset_seconds)
                for segment in local_segments
            ]
            raw_segments.extend(source_segments)
            response_key = source if source_totals[source] == 1 else f"{source}:{item.sequence:04d}"
            raw_responses[response_key] = {
                **source_response,
                "segment_offset_seconds": item.offset_seconds,
                "segment_duration_seconds": item.duration_seconds,
                "segment_sequence": item.sequence,
            }
            current_app.logger.info(
                "Recorder job %s transcription source=%s sequence=%s offset_seconds=%.3f "
                "duration_ms=%s segments=%s",
                reference_id or "synchronous",
                source,
                item.sequence,
                item.offset_seconds,
                int((time.perf_counter() - stage_started) * 1000),
                len(source_segments),
            )

        if progress_callback:
            progress_callback(
                "cleaning_transcript",
                "Removing silent, low-confidence, and suspicious repeated text.",
            )
        cleaned_segments, quality_report = _clean_transcript_segments(raw_segments)
        raw_transcript = _format_transcript(raw_segments)
        transcript = _format_transcript(cleaned_segments)
        _write_raw_transcription_diagnostics(
            source_paths=recorded_sources,
            raw_responses=raw_responses,
            raw_transcript=raw_transcript,
            quality_report=quality_report,
            reference_id=reference_id,
        )

        current_app.logger.info(
            "Recorder job %s transcript quality total=%s kept=%s removed_no_speech=%s "
            "removed_low_confidence=%s removed_repetitions=%s",
            reference_id or "synchronous",
            quality_report.total_segments,
            quality_report.kept_segments,
            quality_report.removed_no_speech,
            quality_report.removed_low_confidence,
            quality_report.removed_repetitions,
        )

        if not transcript:
            raise ValidationError(
                "The recording did not contain reliable speech that could be transcribed. "
                "Check the microphone and try again closer to the speaker."
            )

        timestamp = _normalize_started_at(started_at)
        meeting_id = _build_meeting_id(timestamp, reference_id=reference_id)
        if progress_callback:
            progress_callback(
                "analyzing",
                "Generating the meeting summary, insights, and scorecard.",
            )

        create_kwargs: dict[str, Any] = {}
        if progress_callback and _accepts_progress_callback(self.transcript_service.create):
            create_kwargs["progress_callback"] = lambda stage: progress_callback(
                stage,
                "Saving the completed meeting review."
                if stage == "saving"
                else None,
            )

        quality_payload = quality_report.as_dict()
        transcript_payload: dict[str, Any] = {
            "meeting_id": meeting_id,
            "timestamp": timestamp,
            "transcript": transcript,
            "raw_transcript": raw_transcript,
            "transcript_quality": quality_payload,
        }
        if prepared_meeting and prepared_meeting.get("id"):
            transcript_payload.update(
                {
                    "prepared_meeting_id": str(prepared_meeting.get("id") or ""),
                    "prepared_meeting_title": str(prepared_meeting.get("title") or ""),
                    "prepared_meeting_scheduled_at": str(prepared_meeting.get("scheduled_at") or ""),
                    "prepared_meeting_participants": list(prepared_meeting.get("participants") or []),
                    "prepared_meeting_purpose": str(prepared_meeting.get("purpose") or ""),
                }
            )

        if processing_cache is not None:
            create_kwargs["analysis_cache"] = (processing_cache.get("analyses") or {})
        if cache_callback is not None:
            create_kwargs["analysis_cache_callback"] = lambda key, value: cache_callback(
                "analyses", key, value
            )

        result = self.transcript_service.create(
            user_id,
            transcript_payload,
            **create_kwargs,
        )
        quality_warning = _quality_warning(quality_report)
        result.update(
            {
                "message": "Recording transcribed, analyzed, and saved successfully.",
                "source_count": len({item.source for item in recorded_sources}),
                "segment_count": len(recorded_sources),
                "transcript_quality": quality_payload,
                "quality_warning": quality_warning,
            }
        )
        if prepared_meeting and prepared_meeting.get("id"):
            prepared_meeting_id = str(prepared_meeting.get("id") or "")
            result["prepared_meeting_id"] = prepared_meeting_id
            result["prepared_meeting_title"] = str(prepared_meeting.get("title") or "")
            try:
                MeetingMaterialsService().complete_meeting(user_id, prepared_meeting_id, meeting_id)
            except Exception:
                current_app.logger.exception(
                    "Meeting review was saved, but prepared package %s could not be completed.",
                    prepared_meeting_id,
                )
        return result

    def save_upload(
        self,
        upload: FileStorage,
        *,
        destination_directory: Path | None = None,
        source: str = "AUDIO",
    ) -> SavedUpload:
        mime_type = str(upload.mimetype or "").lower().split(";", 1)[0].strip()
        if mime_type not in _ALLOWED_MIME_TYPES:
            raise ValidationError(
                "Unsupported browser recording format. Use Chrome or Edge and try again."
            )

        max_bytes = int(current_app.config["RECORDER_MAX_FILE_BYTES"])
        suffix = _MIME_EXTENSIONS.get(mime_type, ".webm")
        if destination_directory is not None:
            destination_directory.mkdir(parents=True, exist_ok=True)

        temporary_file = tempfile.NamedTemporaryFile(
            prefix=f"{source.lower()}-",
            suffix=suffix,
            dir=str(destination_directory) if destination_directory else None,
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
                        "One recording segment is too large to transcribe safely. "
                        "Keep this page open and retry; if the problem continues, "
                        "use a supported browser or the Windows Desktop Recorder."
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
            raise ValidationError("One of the browser audio recordings was empty.")
        return SavedUpload(path=path, mime_type=mime_type, size_bytes=total_bytes)

    def transcribe_live_upload(
        self,
        upload: FileStorage | None,
        *,
        source: str,
        user_id: str = "",
        reference_id: str = "",
        language: str | None = None,
    ) -> dict[str, Any]:
        """Transcribe one short, self-contained browser audio window.

        Live chunks intentionally use the same confidence filters as final meeting
        processing. The temporary file is removed before this method returns.
        """
        if not _has_upload(upload):
            raise ValidationError("No live browser audio chunk was received.")

        normalized_source = str(source or "").strip().upper()
        if normalized_source not in {"MICROPHONE", "SPEAKER"}:
            raise ValidationError("The live audio source must be microphone or speaker.")

        saved = self.save_upload(upload, source=f"LIVE-{normalized_source}")
        try:
            segments, response_payload = self._transcribe(
                saved.path,
                normalized_source,
                user_id=user_id,
                reference_id=reference_id,
                language=language,
                feature="live_qa_transcription",
                audio_duration_seconds=float(
                    current_app.config.get("RECORDER_LIVE_CHUNK_WINDOW_SECONDS", 10) or 10
                ),
                event_id=(
                    f"live-transcription-{reference_id}"
                    if reference_id
                    else f"live-transcription-{uuid4().hex}"
                ),
            )
            cleaned_segments, quality_report = _clean_transcript_segments(segments)
            transcript_text = " ".join(
                segment.text.strip()
                for segment in sorted(
                    cleaned_segments,
                    key=lambda item: (item.start_seconds, item.end_seconds),
                )
                if segment.text.strip()
            ).strip()
            return {
                "text": transcript_text,
                "quality": quality_report.as_dict(),
                "language": response_payload.get("language"),
                "duration": response_payload.get("duration"),
                "size_bytes": saved.size_bytes,
            }
        finally:
            try:
                saved.path.unlink(missing_ok=True)
            except OSError:
                current_app.logger.warning(
                    "Could not remove temporary live recorder file %s",
                    saved.path,
                )

    def _transcribe(
        self,
        path: Path,
        source: str,
        *,
        user_id: str = "",
        reference_id: str = "",
        language: str | None = None,
        feature: str = "meeting_transcription",
        audio_duration_seconds: float = 0.0,
        event_id: str | None = None,
    ) -> tuple[list[TranscriptSegment], dict[str, Any]]:
        is_live = feature == "live_qa_transcription"
        model = str(
            current_app.config.get(
                "LIVE_TRANSCRIPTION_MODEL" if is_live else "FINAL_TRANSCRIPTION_MODEL",
                "gpt-4o-mini-transcribe" if is_live else "whisper-1",
            )
            or ("gpt-4o-mini-transcribe" if is_live else "whisper-1")
        ).strip()
        configured_language = str(
            language or current_app.config.get("AUDIO_TRANSCRIPTION_LANGUAGE", "") or ""
        ).strip()
        language = transcription_language(configured_language) if configured_language else ""
        request_kwargs: dict[str, Any] = {
            "model": model,
            "response_format": "json" if is_live else "verbose_json",
        }
        if not is_live:
            request_kwargs["timestamp_granularities"] = ["segment"]
        if language:
            request_kwargs["language"] = language

        reservation = None
        estimated_audio_seconds = float(audio_duration_seconds or 0)
        if estimated_audio_seconds <= 0:
            estimated_audio_seconds = float(
                current_app.config.get(
                    "RECORDER_LIVE_CHUNK_WINDOW_SECONDS"
                    if is_live
                    else "RECORDER_FINAL_SEGMENT_SECONDS",
                    10 if is_live else 600,
                )
                or (10 if is_live else 600)
            )
        if user_id:
            reservation = AICostControlService().reserve_transcription_request(
                user_id,
                feature=feature,
                model=model,
                audio_seconds=max(1.0, estimated_audio_seconds),
            )

        request_started = time.perf_counter()
        try:
            with path.open("rb") as audio_file:
                try:
                    response = self.client.audio.transcriptions.create(
                        file=audio_file,
                        **request_kwargs,
                    )
                except TypeError:
                    # Compatibility with older 1.x OpenAI SDK releases.
                    audio_file.seek(0)
                    request_kwargs.pop("timestamp_granularities", None)
                    response = self.client.audio.transcriptions.create(
                        file=audio_file,
                        **request_kwargs,
                    )
        except Exception as exc:
            if reservation is not None:
                reservation.release()
            raise_if_openai_limited(exc)
            current_app.logger.exception(
                "Recorder job %s audio transcription failed source=%s",
                reference_id or "synchronous",
                source,
                exc_info=exc,
            )
            raise ExternalServiceError(
                "The audio transcription service could not process the recording."
            ) from exc

        payload = _response_payload(response)
        measured_duration = _safe_float(
            payload.get("duration"),
            estimated_audio_seconds,
            minimum=0.0,
        )
        if reservation is not None:
            reservation.settle(
                AICostControlService().transcription_cost_usd(model, measured_duration)
            )
        if user_id:
            try:
                UsageMetricsService().record_transcription_usage(
                    user_id,
                    feature=feature,
                    model=model,
                    audio_seconds=measured_duration,
                    event_id=event_id,
                    duration_ms=int((time.perf_counter() - request_started) * 1000),
                    source=source.lower(),
                )
            except Exception:
                current_app.logger.exception(
                    "Could not record transcription usage for recorder job %s",
                    reference_id or "synchronous",
                )
        return _segments_from_payload(payload, source), payload



def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _transcription_cache_key(
    path: Path, *, source: str, model: str, language: str | None
) -> str:
    raw = "|".join(
        (
            _sha256_file(path),
            str(source or "").upper(),
            str(model or "").lower(),
            str(language or "").lower(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _deserialize_transcript_segments(value: Any, source: str) -> list[TranscriptSegment]:
    result: list[TranscriptSegment] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        result.append(
            TranscriptSegment(
                source=str(item.get("source") or source),
                start_seconds=_safe_float(item.get("start_seconds"), 0.0, minimum=0.0),
                end_seconds=_safe_float(item.get("end_seconds"), 0.0, minimum=0.0),
                text=text,
                no_speech_probability=_optional_float(item.get("no_speech_probability")),
                average_log_probability=_optional_float(item.get("average_log_probability")),
                compression_ratio=_optional_float(item.get("compression_ratio")),
            )
        )
    return result


def _compact_transcription_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in ("text", "language", "duration")
        if payload.get(key) is not None
    }


def _normalize_recorded_sources(source_paths: list[Any]) -> list[RecordedSource]:
    normalized: list[RecordedSource] = []
    per_source_sequence: dict[str, int] = defaultdict(int)
    for item in source_paths:
        if isinstance(item, RecordedSource):
            recorded = item
        elif isinstance(item, dict):
            source = str(item.get("source") or "").strip().upper()
            path = Path(item.get("path"))
            recorded = RecordedSource(
                source=source,
                path=path,
                offset_seconds=_safe_float(item.get("offset_seconds"), 0.0, minimum=0.0),
                duration_seconds=_safe_float(item.get("duration_seconds"), 0.0, minimum=0.0),
                sequence=(
                    int(item["sequence"])
                    if item.get("sequence") is not None
                    else per_source_sequence[source]
                ),
            )
        else:
            values = tuple(item)
            if len(values) < 2:
                raise ValidationError("A recorded audio source is invalid.")
            source = str(values[0] or "").strip().upper()
            path = Path(values[1])
            offset_seconds = (
                _safe_float(values[2], 0.0, minimum=0.0) if len(values) >= 3 else 0.0
            )
            duration_seconds = (
                _safe_float(values[3], 0.0, minimum=0.0) if len(values) >= 4 else 0.0
            )
            recorded = RecordedSource(
                source=source,
                path=path,
                offset_seconds=offset_seconds,
                duration_seconds=duration_seconds,
                sequence=per_source_sequence[source],
            )
        if recorded.source not in {"MICROPHONE", "SPEAKER"}:
            raise ValidationError("A recorded audio source is invalid.")
        normalized.append(recorded)
        per_source_sequence[recorded.source] = max(
            per_source_sequence[recorded.source] + 1,
            recorded.sequence + 1,
        )
    return sorted(
        normalized,
        key=lambda item: (item.offset_seconds, 0 if item.source == "SPEAKER" else 1, item.sequence),
    )


def _offset_transcript_segment(
    segment: TranscriptSegment,
    offset_seconds: float,
) -> TranscriptSegment:
    if not offset_seconds:
        return segment
    return TranscriptSegment(
        source=segment.source,
        start_seconds=segment.start_seconds + offset_seconds,
        end_seconds=segment.end_seconds + offset_seconds,
        text=segment.text,
        no_speech_probability=segment.no_speech_probability,
        average_log_probability=segment.average_log_probability,
        compression_ratio=segment.compression_ratio,
    )

def _accepts_progress_callback(callable_object: Any) -> bool:
    try:
        return "progress_callback" in inspect.signature(callable_object).parameters
    except (TypeError, ValueError):
        return False


def _has_upload(upload: FileStorage | None) -> bool:
    return bool(upload and upload.filename)


def _response_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, str):
        return {"text": response, "segments": []}
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
        "segments": getattr(response, "segments", []),
        "language": getattr(response, "language", None),
        "duration": getattr(response, "duration", None),
    }


def _segments_from_payload(payload: dict[str, Any], source: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for item in payload.get("segments") or []:
        if isinstance(item, dict):
            get_value = item.get
        else:
            get_value = lambda key, default=None, item=item: getattr(
                item,
                key,
                default,
            )

        text = str(get_value("text", "") or "").strip()
        if not text:
            continue
        start_seconds = _safe_float(get_value("start", 0.0), 0.0, minimum=0.0)
        end_seconds = _safe_float(
            get_value("end", start_seconds),
            start_seconds,
            minimum=start_seconds,
        )
        segments.append(
            TranscriptSegment(
                source=source,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                text=text,
                no_speech_probability=_optional_float(
                    get_value("no_speech_prob", get_value("no_speech_probability"))
                ),
                average_log_probability=_optional_float(
                    get_value("avg_logprob", get_value("average_log_probability"))
                ),
                compression_ratio=_optional_float(get_value("compression_ratio")),
            )
        )

    if segments:
        return segments

    text = str(payload.get("text") or "").strip()
    return [TranscriptSegment(source, 0.0, 0.0, text)] if text else []


def _clean_transcript_segments(
    segments: list[TranscriptSegment],
) -> tuple[list[TranscriptSegment], TranscriptQualityReport]:
    no_speech_threshold = float(
        current_app.config.get("RECORDER_NO_SPEECH_PROBABILITY_THRESHOLD", 0.60)
    )
    high_no_speech_threshold = float(
        current_app.config.get("RECORDER_HIGH_NO_SPEECH_PROBABILITY_THRESHOLD", 0.80)
    )
    min_avg_log_probability = float(
        current_app.config.get("RECORDER_MIN_AVG_LOGPROB", -1.0)
    )
    very_low_avg_log_probability = float(
        current_app.config.get("RECORDER_VERY_LOW_AVG_LOGPROB", -1.5)
    )
    max_compression_ratio = float(
        current_app.config.get("RECORDER_MAX_COMPRESSION_RATIO", 2.4)
    )

    first_pass: list[TranscriptSegment] = []
    removed: list[dict[str, Any]] = []
    removed_no_speech = 0
    removed_low_confidence = 0

    for segment in segments:
        reason = _confidence_rejection_reason(
            segment,
            no_speech_threshold=no_speech_threshold,
            high_no_speech_threshold=high_no_speech_threshold,
            min_avg_log_probability=min_avg_log_probability,
            very_low_avg_log_probability=very_low_avg_log_probability,
            max_compression_ratio=max_compression_ratio,
        )
        if reason is None:
            first_pass.append(segment)
            continue
        if reason == "no_speech":
            removed_no_speech += 1
        else:
            removed_low_confidence += 1
        removed.append(_removed_segment_payload(segment, reason))

    repetition_cleaned, repetition_removed = _remove_suspicious_repetitions(first_pass)
    removed.extend(repetition_removed)
    report = TranscriptQualityReport(
        total_segments=len(segments),
        kept_segments=len(repetition_cleaned),
        removed_no_speech=removed_no_speech,
        removed_low_confidence=removed_low_confidence,
        removed_repetitions=len(repetition_removed),
        removed_segments=tuple(removed[:100]),
    )
    return repetition_cleaned, report


def _confidence_rejection_reason(
    segment: TranscriptSegment,
    *,
    no_speech_threshold: float,
    high_no_speech_threshold: float,
    min_avg_log_probability: float,
    very_low_avg_log_probability: float,
    max_compression_ratio: float,
) -> str | None:
    no_speech = segment.no_speech_probability
    avg_logprob = segment.average_log_probability
    compression_ratio = segment.compression_ratio

    if no_speech is not None and no_speech >= high_no_speech_threshold:
        return "no_speech"
    if (
        no_speech is not None
        and no_speech >= no_speech_threshold
        and avg_logprob is not None
        and avg_logprob <= min_avg_log_probability
    ):
        return "no_speech"
    if (
        avg_logprob is not None
        and avg_logprob <= very_low_avg_log_probability
        and compression_ratio is not None
        and compression_ratio >= max_compression_ratio
    ):
        return "low_confidence"
    return None


def _remove_suspicious_repetitions(
    segments: list[TranscriptSegment],
) -> tuple[list[TranscriptSegment], list[dict[str, Any]]]:
    minimum_words = int(current_app.config.get("RECORDER_REPEAT_MIN_WORDS", 5))
    allowed_occurrences = max(
        1,
        int(current_app.config.get("RECORDER_REPEAT_ALLOW_COUNT", 1)),
    )
    trigger_count = max(
        allowed_occurrences + 1,
        int(current_app.config.get("RECORDER_REPEAT_TRIGGER_COUNT", 3)),
    )
    window_seconds = float(
        current_app.config.get("RECORDER_REPEAT_WINDOW_SECONDS", 180.0)
    )

    by_source_and_text: dict[tuple[str, str], deque[tuple[int, TranscriptSegment]]] = (
        defaultdict(deque)
    )
    remove_indexes: set[int] = set()

    ordered_indexes = sorted(
        range(len(segments)),
        key=lambda index: (
            segments[index].source,
            segments[index].start_seconds,
            segments[index].end_seconds,
        ),
    )
    for index in ordered_indexes:
        segment = segments[index]
        normalized = _normalize_spoken_text(segment.text)
        if len(normalized.split()) < minimum_words:
            continue
        key = (segment.source, normalized)
        occurrences = by_source_and_text[key]
        cutoff = segment.start_seconds - window_seconds
        while occurrences and occurrences[0][1].start_seconds < cutoff:
            occurrences.popleft()
        occurrences.append((index, segment))
        if len(occurrences) >= trigger_count:
            for repeated_index, _ in list(occurrences)[allowed_occurrences:]:
                remove_indexes.add(repeated_index)

    cleaned: list[TranscriptSegment] = []
    removed: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if index in remove_indexes:
            removed.append(_removed_segment_payload(segment, "repetition"))
        else:
            cleaned.append(segment)
    return cleaned, removed


def _removed_segment_payload(
    segment: TranscriptSegment,
    reason: str,
) -> dict[str, Any]:
    return {
        "source": segment.source,
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
        "text": segment.text,
        "reason": reason,
        "no_speech_probability": segment.no_speech_probability,
        "average_log_probability": segment.average_log_probability,
        "compression_ratio": segment.compression_ratio,
    }


def _normalize_spoken_text(text: str) -> str:
    return _NORMALIZE_TEXT_PATTERN.sub(" ", str(text or "").lower()).strip()


def _quality_warning(report: TranscriptQualityReport) -> str:
    if not report.adjusted:
        return ""
    return (
        f"Transcript quality protection removed {report.removed_total} silent, "
        "low-confidence, or suspicious repeated segment"
        f"{'s' if report.removed_total != 1 else ''}."
    )


def _write_raw_transcription_diagnostics(
    *,
    source_paths: list[Any],
    raw_responses: dict[str, Any],
    raw_transcript: str,
    quality_report: TranscriptQualityReport,
    reference_id: str,
) -> None:
    if not reference_id or not source_paths:
        return
    parent_directories = {item.path.parent.resolve() for item in source_paths}
    if len(parent_directories) != 1:
        return
    output_path = next(iter(parent_directories)) / "transcription_raw.json"
    payload = {
        "reference_id": reference_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": current_app.config.get("AUDIO_TRANSCRIPTION_MODEL"),
        "language": current_app.config.get("AUDIO_TRANSCRIPTION_LANGUAGE"),
        "responses": raw_responses,
        "raw_transcript": raw_transcript,
        "quality_report": quality_report.as_dict(include_removed_segments=True),
    }
    try:
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError):
        current_app.logger.exception(
            "Recorder job %s could not write raw transcription diagnostics",
            reference_id,
        )


def _safe_float(value: Any, default: float, *, minimum: float) -> float:
    try:
        return max(minimum, float(value if value is not None else default))
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_transcript(segments: list[TranscriptSegment]) -> str:
    ordered = sorted(
        segments,
        key=lambda segment: (
            segment.start_seconds,
            0 if segment.source == "SPEAKER" else 1,
            segment.end_seconds,
        ),
    )
    lines = []
    for segment in ordered:
        timestamp = _format_offset(segment.start_seconds)
        lines.append(f"{timestamp} [{segment.source}] {segment.text}")
    return "\n".join(lines)


def _format_offset(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _normalize_started_at(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc).isoformat()

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            "The browser supplied an invalid recording start time."
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _build_meeting_id(timestamp: str, *, reference_id: str = "") -> str:
    parsed = datetime.fromisoformat(timestamp)
    suffix = (
        hashlib.sha256(str(reference_id).encode("utf-8")).hexdigest()[:8]
        if reference_id
        else uuid4().hex[:8]
    )
    return f"browser-{parsed.strftime('%Y%m%d_%H%M%S')}-{suffix}"
