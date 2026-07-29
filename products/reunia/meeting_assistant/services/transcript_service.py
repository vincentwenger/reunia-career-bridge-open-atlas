from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from decimal import Decimal
from typing import Any, Callable

from botocore.exceptions import ClientError
from flask import current_app

from meeting_assistant.repositories.transcript_repository import TranscriptRepository
from meeting_assistant.services.transcript_analysis_service import TranscriptAnalysisService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.exceptions import DatabaseError, ValidationError
from meeting_assistant.utils.json_parsing import to_json_compatible


class TranscriptService:
    def __init__(
        self,
        repository: TranscriptRepository | None = None,
        analysis_service: TranscriptAnalysisService | None = None,
        user_service: UserService | None = None,
    ) -> None:
        self.repository = repository or TranscriptRepository()
        self.analysis_service = analysis_service or TranscriptAnalysisService()
        self.user_service = user_service or UserService()

    def create(
        self,
        user_id: str,
        data: dict[str, Any],
        progress_callback: Callable[[str], None] | None = None,
        analysis_cache: dict[str, Any] | None = None,
        analysis_cache_callback: Callable[[str, dict[str, Any]], None] | None = None,
        scorecard_source_override: str | None = None,
        analysis_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        meeting_id = str(data.get("meeting_id") or "").strip()
        transcript = str(data.get("transcript") or "").strip()
        if not meeting_id or not transcript:
            raise ValidationError("meeting_id and transcript are required.")

        settings = dict(self.user_service.get_settings(user_id))
        normalized_scorecard_source = str(scorecard_source_override or "").strip().lower()
        if normalized_scorecard_source in {"microphone", "speaker", "all"}:
            settings["scorecardSource"] = normalized_scorecard_source
            settings["scorecard_source"] = normalized_scorecard_source
        model = settings.get("aiModel") or current_app.config["DEFAULT_AI_MODEL"]
        if current_app.config["ALLOW_CLIENT_AI_MODEL_OVERRIDE"] and data.get("aiModel"):
            model = data["aiModel"]

        analysis_key = hashlib.sha256(
            json.dumps(
                {
                    "transcript": transcript,
                    "model": model,
                    "settings": {
                        key: settings.get(key)
                        for key in (
                            "language",
                            "meetingSummaryDetail",
                            "meetingExtractActionItems",
                            "meetingGenerateScorecard",
                            "scorecardSource",
                            "scorecard_source",
                        )
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if isinstance(analysis_override, dict):
            analysis = dict(analysis_override)
        else:
            cached_analysis = (analysis_cache or {}).get(analysis_key)
            if isinstance(cached_analysis, dict):
                analysis = dict(cached_analysis)
                current_app.logger.info("Reused cached meeting analysis %s", analysis_key[:12])
            else:
                analysis = self.analysis_service.analyze(
                    transcript=transcript,
                    model=model,
                    settings=settings,
                    user_id=user_id,
                )
                if analysis_cache_callback:
                    analysis_cache_callback(analysis_key, to_json_compatible(analysis))
        if progress_callback:
            progress_callback("saving")

        requested_meeting_name = str(data.get("meeting_name") or "").strip()
        if requested_meeting_name:
            analysis["meeting_name"] = requested_meeting_name
        if "topics" in data:
            analysis["topics"] = _normalize_topics(data.get("topics"))
        timestamp = str(
            data.get("timestamp") or datetime.now(timezone.utc).isoformat()
        )
        item = {
            "meeting_id": meeting_id,
            "user_id": user_id,
            "timestamp": timestamp,
            "transcript": transcript,
            "ai_model": model,
            **_for_dynamodb(analysis),
        }
        retention_days = int(settings.get("meetingRetentionDays") or 0)
        if retention_days > 0:
            item["retention_expires_at"] = int(
                (datetime.now(timezone.utc) + timedelta(days=retention_days)).timestamp()
            )

        raw_transcript = str(data.get("raw_transcript") or "").strip()
        if raw_transcript and raw_transcript != transcript:
            item["raw_transcript"] = raw_transcript

        transcript_quality = data.get("transcript_quality")
        if isinstance(transcript_quality, dict):
            item["transcript_quality"] = _for_dynamodb(transcript_quality)

        preparation_fields = {
            "prepared_meeting_id": str(data.get("prepared_meeting_id") or "").strip(),
            "prepared_meeting_title": str(data.get("prepared_meeting_title") or "").strip(),
            "prepared_meeting_scheduled_at": str(data.get("prepared_meeting_scheduled_at") or "").strip(),
            "prepared_meeting_purpose": str(data.get("prepared_meeting_purpose") or "").strip(),
        }
        for field, value in preparation_fields.items():
            if value:
                item[field] = value
        participants = data.get("prepared_meeting_participants")
        if isinstance(participants, list):
            normalized_participants = [str(value).strip() for value in participants if str(value).strip()]
            if normalized_participants:
                item["prepared_meeting_participants"] = normalized_participants

        try:
            self.repository.create(item)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise ValidationError("A transcript with this meeting ID and timestamp already exists.") from exc
            raise DatabaseError("Failed to save the transcript.") from exc

        return {
            "message": "Transcript successfully submitted!",
            "meeting_id": meeting_id,
            "timestamp": timestamp,
        }

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        try:
            records = self.repository.list_for_user(user_id)
        except ClientError as exc:
            raise DatabaseError("Failed to retrieve transcripts.") from exc
        active_records: list[dict[str, Any]] = []
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        for record in records:
            try:
                expired = int(record.get("retention_expires_at") or 0) <= now_epoch
                expired = expired and bool(record.get("retention_expires_at"))
            except (TypeError, ValueError):
                expired = False
            if not expired:
                active_records.append(record)
                continue
            try:
                self.repository.delete_owned(
                    user_id,
                    str(record.get("meeting_id") or ""),
                    str(record.get("timestamp") or ""),
                )
            except Exception:
                current_app.logger.exception(
                    "Could not remove expired meeting %s for %s.",
                    record.get("meeting_id"),
                    user_id,
                )
        return to_json_compatible(active_records)

    def update(
        self,
        user_id: str,
        meeting_id: Any,
        timestamp: Any,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        meeting_id, timestamp = _validate_key(meeting_id, timestamp)
        fields = {
            field: data[field]
            for field in ("meeting_name", "summary", "topics")
            if field in data
        }
        if not fields:
            raise ValidationError("Provide meeting_name, summary, or topics.")
        if "meeting_name" in fields:
            fields["meeting_name"] = str(fields["meeting_name"] or "").strip()
            if not fields["meeting_name"]:
                raise ValidationError("The meeting name cannot be empty.")
        if "summary" in fields:
            fields["summary"] = str(fields["summary"] or "").strip()
        if "topics" in fields:
            fields["topics"] = _normalize_topics(fields["topics"])

        try:
            self.repository.update_owned(user_id, meeting_id, timestamp, fields)
        except ClientError as exc:
            raise DatabaseError("Failed to update meeting details.") from exc

        return {
            "message": "Meeting details successfully updated.",
            "meeting_id": meeting_id,
            "timestamp": timestamp,
            **fields,
        }

    def manage_topics(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        operation = str(data.get("operation") or "").strip().lower()
        source = _normalize_topic_name(data.get("source"))
        target = _normalize_topic_name(data.get("target"))

        if operation not in {"rename", "merge", "delete"}:
            raise ValidationError("operation must be rename, merge, or delete.")
        if not source:
            raise ValidationError("source topic is required.")
        if operation in {"rename", "merge"} and not target:
            raise ValidationError("target topic is required.")
        if target and source.casefold() == target.casefold():
            raise ValidationError("Choose a different target topic.")

        try:
            records = self.repository.list_for_user(user_id)
            updated_meetings = 0
            for record in records:
                current_topics = _normalize_topics(record.get("topics"))
                if not any(topic.casefold() == source.casefold() for topic in current_topics):
                    continue

                transformed: list[str] = []
                for topic in current_topics:
                    if topic.casefold() != source.casefold():
                        transformed.append(topic)
                    elif operation in {"rename", "merge"}:
                        transformed.append(target)

                normalized_topics = _normalize_topics(transformed)
                self.repository.update_owned(
                    user_id,
                    str(record.get("meeting_id") or ""),
                    str(record.get("timestamp") or ""),
                    {"topics": normalized_topics},
                )
                updated_meetings += 1
        except ClientError as exc:
            raise DatabaseError("Failed to update meeting topics.") from exc

        return {
            "message": "Meeting topics successfully updated.",
            "operation": operation,
            "source": source,
            "target": target if operation in {"rename", "merge"} else "",
            "updated_meetings": updated_meetings,
        }

    def delete(self, user_id: str, meeting_id: Any, timestamp: Any) -> dict[str, Any]:
        meeting_id, timestamp = _validate_key(meeting_id, timestamp)
        try:
            self.repository.delete_owned(user_id, meeting_id, timestamp)
        except ClientError as exc:
            raise DatabaseError("Failed to delete the meeting.") from exc
        return {
            "message": "Meeting successfully deleted.",
            "meeting_id": meeting_id,
            "timestamp": timestamp,
        }


def _normalize_topic_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:60].strip()


def _normalize_topics(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []

    topics: list[str] = []
    seen: set[str] = set()
    for item in values:
        topic = _normalize_topic_name(item)
        key = topic.casefold()
        if not topic or key in seen:
            continue
        seen.add(key)
        topics.append(topic)
        if len(topics) >= 20:
            break
    return topics


def _validate_key(meeting_id: Any, timestamp: Any) -> tuple[str, str]:
    meeting_id = str(meeting_id or "").strip()
    timestamp = str(timestamp or "").strip()
    if not meeting_id:
        raise ValidationError("meeting_id is required.")
    if not timestamp:
        raise ValidationError("timestamp is required.")
    return meeting_id, timestamp


def _for_dynamodb(value: Any) -> Any:
    """Convert floats to Decimal while retaining native list/map structures."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _for_dynamodb(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_for_dynamodb(item) for item in value]
    return value
