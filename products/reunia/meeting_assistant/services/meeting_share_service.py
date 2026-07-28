from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import current_app, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from meeting_assistant.i18n import normalize_language, supported_language
from meeting_assistant.repositories.meeting_share_repository import MeetingShareRepository
from meeting_assistant.repositories.transcript_repository import TranscriptRepository
from meeting_assistant.services.user_service import UserService, default_user_settings
from meeting_assistant.utils.exceptions import ResourceNotFoundError, ValidationError
from meeting_assistant.utils.json_parsing import to_json_compatible


class MeetingShareService:
    def __init__(
        self,
        repository: MeetingShareRepository | None = None,
        transcript_repository: TranscriptRepository | None = None,
        user_service: UserService | None = None,
    ) -> None:
        self.repository = repository or MeetingShareRepository()
        self.transcript_repository = transcript_repository or TranscriptRepository()
        self.user_service = user_service or UserService()

    def create(
        self,
        user_id: str,
        meeting_id: Any,
        timestamp: Any,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        meeting_id, timestamp = _validate_meeting_key(meeting_id, timestamp)
        meeting = to_json_compatible(
            self.transcript_repository.get_owned(user_id, meeting_id, timestamp)
        )

        settings = (
            default_user_settings()
            if current_app.testing
            else self.user_service.get_settings(user_id)
        )
        language = normalize_language(
            data.get("language") or settings.get("language"),
            default="en",
        )
        include_scorecard = bool(
            data.get("include_scorecard", settings.get("shareIncludeScorecard", False))
        )
        include_transcript = bool(data.get("include_transcript"))
        allow_download = bool(
            data.get("allow_download", settings.get("shareAllowDownload", False))
        )
        password = str(data.get("password") or "")
        if len(password) > 128:
            raise ValidationError("The share password is too long.")
        if bool(settings.get("shareRequirePassword")) and not password:
            raise ValidationError("A password is required by your sharing defaults.")

        created_at = _now()
        expires_value = data.get("expires_in_days")
        if expires_value is None:
            expires_value = int(settings.get("shareDefaultExpirationDays") or 0)
        expires_at = _expiration_from_value(
            "never"
            if str(expires_value).strip().lower() in {"0", "never", "none"}
            else expires_value,
            created_at,
        )
        share_id = secrets.token_urlsafe(32)
        snapshot = _build_snapshot(
            meeting,
            include_scorecard=include_scorecard,
            include_transcript=include_transcript,
        )
        item = {
            "share_id": share_id,
            "user_id": user_id,
            "meeting_id": meeting_id,
            "meeting_timestamp": timestamp,
            "meeting_name": snapshot["meeting_name"],
            "language": language,
            "include_summary": True,
            "include_scorecard": include_scorecard,
            "include_transcript": include_transcript,
            "allow_download": allow_download,
            "password_hash": generate_password_hash(password) if password else "",
            "is_active": True,
            "created_at": created_at,
            "updated_at": created_at,
            "expires_at": expires_at,
            "expires_at_epoch": _expiration_epoch(expires_at),
            "last_accessed_at": "",
            "access_count": 0,
            "snapshot": snapshot,
        }
        self.repository.create(item)
        return self._public_management_record(item, reveal_url=True)

    def list_for_meeting(
        self,
        user_id: str,
        meeting_id: Any,
        timestamp: Any,
    ) -> list[dict[str, Any]]:
        meeting_id, timestamp = _validate_meeting_key(meeting_id, timestamp)
        records = self.repository.list_for_meeting(user_id, meeting_id, timestamp)
        fallback_language = self._owner_language(user_id)
        for record in records:
            if not supported_language(record.get("language")):
                record["language"] = fallback_language
        return [self._public_management_record(record) for record in records]

    def update(
        self,
        user_id: str,
        share_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        share_id = str(share_id or "").strip()
        existing = self.repository.get(share_id)
        if not existing or str(existing.get("user_id") or "") != user_id:
            raise ResourceNotFoundError("Share link not found.")

        fields: dict[str, Any] = {"updated_at": _now()}
        if "is_active" in data:
            fields["is_active"] = bool(data.get("is_active"))
        if "expires_in_days" in data:
            fields["expires_at"] = _expiration_from_value(data.get("expires_in_days"), _now())
            fields["expires_at_epoch"] = _expiration_epoch(fields["expires_at"])
        if "allow_download" in data:
            fields["allow_download"] = bool(data.get("allow_download"))

        password = data.get("password")
        if password is not None:
            password_text = str(password)
            if len(password_text) > 128:
                raise ValidationError("The share password is too long.")
            fields["password_hash"] = (
                generate_password_hash(password_text) if password_text else ""
            )

        if bool(data.get("refresh_snapshot")):
            meeting = to_json_compatible(
                self.transcript_repository.get_owned(
                    user_id,
                    str(existing.get("meeting_id") or ""),
                    str(existing.get("meeting_timestamp") or ""),
                )
            )
            snapshot = _build_snapshot(
                meeting,
                include_scorecard=bool(existing.get("include_scorecard")),
                include_transcript=bool(existing.get("include_transcript")),
            )
            fields["snapshot"] = snapshot
            fields["meeting_name"] = snapshot["meeting_name"]

        updated = self.repository.update_owned(user_id, share_id, fields)
        return self._public_management_record(updated)

    def revoke(self, user_id: str, share_id: str) -> dict[str, Any]:
        updated = self.repository.update_owned(
            user_id,
            str(share_id or "").strip(),
            {"is_active": False, "updated_at": _now()},
        )
        return {
            "message": "Share link revoked.",
            "share": self._public_management_record(updated),
        }

    def get_public(self, share_id: str) -> dict[str, Any]:
        record = self.repository.get(str(share_id or "").strip())
        if not record or not bool(record.get("is_active")):
            raise ResourceNotFoundError("This shared meeting is unavailable.")
        if _is_expired(record.get("expires_at")):
            raise ResourceNotFoundError("This shared meeting link has expired.")
        if not supported_language(record.get("language")):
            record["language"] = self._owner_language(str(record.get("user_id") or ""))
        return record

    def _owner_language(self, user_id: str) -> str:
        if not user_id:
            return "en"
        try:
            settings = self.user_service.get_settings(user_id)
        except Exception:  # Keep legacy public links available if user settings cannot be loaded.
            current_app.logger.warning(
                "Could not load the owner language for a legacy meeting share.",
                exc_info=True,
            )
            return "en"
        return normalize_language(settings.get("language"), default="en")

    def requires_password(self, record: dict[str, Any]) -> bool:
        return bool(str(record.get("password_hash") or ""))

    def verify_password(self, record: dict[str, Any], password: str) -> bool:
        password_hash = str(record.get("password_hash") or "")
        return not password_hash or check_password_hash(password_hash, str(password or ""))

    def record_access(self, record: dict[str, Any]) -> None:
        now = _now()
        updated = self.repository.record_access(
            str(record.get("share_id") or ""),
            now,
        )
        if updated:
            record["access_count"] = int(updated.get("access_count") or 0)
            record["last_accessed_at"] = updated.get("last_accessed_at") or now

    def build_download_text(
        self,
        record: dict[str, Any],
        language: str | None = None,
    ) -> str:
        snapshot = record.get("snapshot") or {}
        language = normalize_language(language or record.get("language"), default="en")
        labels = _download_labels(language)
        lines = [
            str(snapshot.get("meeting_name") or labels["shared_meeting"]),
            str(snapshot.get("meeting_date") or ""),
            "",
            labels["summary"],
            str(snapshot.get("summary") or labels["no_summary"]),
        ]
        _append_list(lines, labels["key_wins"], snapshot.get("key_wins"))
        _append_list(lines, labels["improvement_areas"], snapshot.get("improvement_areas"))
        _append_list(lines, labels["action_items"], snapshot.get("action_items"))
        _append_list(lines, labels["open_questions"], snapshot.get("open_questions"))

        scorecard = snapshot.get("scorecard")
        if isinstance(scorecard, dict):
            lines.extend([
                "",
                labels["scorecard"],
                f"{labels['overall_score']}: {_display_value(scorecard.get('overall_score'), language)}",
                f"{labels['content_score']}: {_display_value(scorecard.get('content_average_score'), language)}",
                f"{labels['form_score']}: {_display_value(scorecard.get('form_average_score'), language)}",
                str((scorecard.get("form_metrics") or {}).get("overall_assessment") or ""),
            ])

        transcript = snapshot.get("transcript")
        if transcript:
            lines.extend(["", labels["transcript"], str(transcript)])
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _public_management_record(
        self,
        record: dict[str, Any],
        *,
        reveal_url: bool = False,
    ) -> dict[str, Any]:
        share_id = str(record.get("share_id") or "")
        language = normalize_language(record.get("language"), default="en")
        result = {
            "share_id": share_id,
            "meeting_name": record.get("meeting_name") or "Shared meeting",
            "language": language,
            "include_summary": True,
            "include_scorecard": bool(record.get("include_scorecard")),
            "include_transcript": bool(record.get("include_transcript")),
            "allow_download": bool(record.get("allow_download")),
            "password_protected": bool(record.get("password_hash")),
            "is_active": bool(record.get("is_active")),
            "created_at": record.get("created_at") or "",
            "updated_at": record.get("updated_at") or "",
            "expires_at": record.get("expires_at") or "",
            "last_accessed_at": record.get("last_accessed_at") or "",
            "access_count": int(record.get("access_count") or 0),
            "is_expired": _is_expired(record.get("expires_at")),
            "public_url": url_for(
                "meeting_shares.public_shared_meeting",
                share_id=share_id,
                lang=language,
                _external=True,
            ),
        }
        if reveal_url:
            result["message"] = "Share link created."
        return result


def _validate_meeting_key(meeting_id: Any, timestamp: Any) -> tuple[str, str]:
    meeting_id = str(meeting_id or "").strip()
    timestamp = str(timestamp or "").strip()
    if not meeting_id:
        raise ValidationError("meeting_id is required.")
    if not timestamp:
        raise ValidationError("timestamp is required.")
    return meeting_id, timestamp


def _build_snapshot(
    meeting: dict[str, Any],
    *,
    include_scorecard: bool,
    include_transcript: bool,
) -> dict[str, Any]:
    meeting = _unwrap_storage_value(to_json_compatible(meeting))
    snapshot: dict[str, Any] = {
        "meeting_name": str(
            meeting.get("meeting_name")
            or meeting.get("name")
            or meeting.get("title")
            or "Shared meeting"
        ),
        "meeting_date": str(meeting.get("timestamp") or meeting.get("date") or ""),
        "summary": str(meeting.get("summary") or ""),
        "key_wins": _clean_text_list(meeting.get("key_wins")),
        "improvement_areas": _clean_text_list(meeting.get("improvement_areas")),
        "action_items": _clean_action_items(meeting.get("action_items")),
        "open_questions": _clean_question_items(meeting.get("open_questions")),
    }
    if include_scorecard:
        snapshot["scorecard"] = {
            "overall_score": _first_value(
                meeting,
                "final_grade",
                "final_weighted_grade",
                "overall_score",
            ),
            "content_average_score": meeting.get("content_average_score"),
            "form_average_score": meeting.get("form_average_score"),
            "content_grades": _clean_content_grades(meeting.get("content_grades")),
            "form_metrics": _clean_form_metrics(meeting.get("form_metrics")),
        }
    if include_transcript:
        snapshot["transcript"] = str(
            meeting.get("transcript") or meeting.get("text") or ""
        )
    return snapshot


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None



def _clean_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = _first_value(item, "text", "title", "value", "summary", "question")
        else:
            text = item
        text = str(text or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def _clean_action_items(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    cleaned: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            text = str(item or "").strip()
            if text:
                cleaned.append(text)
            continue
        task = str(_first_value(item, "task", "action", "text", "title") or "").strip()
        if not task:
            continue
        public_item: dict[str, str] = {"task": task}
        owner = str(_first_value(item, "owner", "assignee") or "").strip()
        due_date = str(_first_value(item, "due_date", "deadline", "due") or "").strip()
        priority = str(item.get("priority") or "").strip()
        status = str(item.get("status") or "").strip()
        if owner:
            public_item["owner"] = owner
        if due_date:
            public_item["due_date"] = due_date
        if priority:
            public_item["priority"] = priority
        if status:
            public_item["status"] = status
        cleaned.append(public_item)
    return cleaned


def _clean_question_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        if isinstance(item, dict):
            question = _first_value(item, "question", "text", "title")
        else:
            question = item
        question = str(question or "").strip()
        if question:
            cleaned.append(question)
    return cleaned


def _clean_content_grades(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        public_item = {
            "question": str(item.get("question") or "").strip(),
            "answer": str(item.get("answer") or "").strip(),
            "relevance_analysis": str(item.get("relevance_analysis") or "").strip(),
            "grade": str(item.get("grade") or "").strip(),
        }
        if any(public_item.values()):
            cleaned.append(public_item)
    return cleaned


def _clean_form_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "pace_wpm",
        "pace_grade",
        "filler_words_count",
        "filler_words",
        "filler_words_grade",
        "power_words_count",
        "power_words",
        "power_words_grade",
        "negative_words_count",
        "negative_words",
        "negative_words_grade",
        "negative_tone_count",
        "negative_tone",
        "negative_tone_grade",
        "pauses_count",
        "pauses_grade",
        "overall_assessment",
    }
    return {
        key: to_json_compatible(value[key])
        for key in allowed
        if key in value
    }


def _expiration_from_value(value: Any, start: str) -> str:
    if value in (None, "", "30"):
        days = 30
    elif str(value).strip().lower() in {"never", "none", "0"}:
        return ""
    else:
        try:
            days = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Choose a valid expiration period.") from exc
        if days not in {1, 7, 14, 30, 60, 90}:
            raise ValidationError("Choose a valid expiration period.")
    start_date = _parse_datetime(start) or datetime.now(timezone.utc)
    return (start_date + timedelta(days=days)).isoformat()


def _expiration_epoch(value: Any) -> int | None:
    expires_at = _parse_datetime(value)
    return int(expires_at.timestamp()) if expires_at else None


def _unwrap_storage_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_unwrap_storage_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"S"}:
        return value["S"]
    if set(value) == {"N"}:
        number = str(value["N"])
        try:
            return float(number) if "." in number else int(number)
        except ValueError:
            return number
    if set(value) == {"BOOL"}:
        return bool(value["BOOL"])
    if set(value) == {"NULL"}:
        return None
    if set(value) == {"L"} and isinstance(value["L"], list):
        return [_unwrap_storage_value(item) for item in value["L"]]
    if set(value) == {"M"} and isinstance(value["M"], dict):
        return {key: _unwrap_storage_value(item) for key, item in value["M"].items()}
    return {key: _unwrap_storage_value(item) for key, item in value.items()}


def _is_expired(value: Any) -> bool:
    expires_at = _parse_datetime(value)
    return bool(expires_at and expires_at <= datetime.now(timezone.utc))


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_list(lines: list[str], heading: str, items: Any) -> None:
    if not isinstance(items, list) or not items:
        return
    lines.extend(["", heading])
    for item in items:
        if isinstance(item, dict):
            text = (
                item.get("task")
                or item.get("action")
                or item.get("text")
                or item.get("question")
                or str(item)
            )
        else:
            text = item
        lines.append(f"- {text}")


def _display_value(value: Any, language: str = "en") -> str:
    if value not in (None, ""):
        return str(value)
    return "S.O." if normalize_language(language, default="en") == "fr" else "N/A"


def _download_labels(language: str) -> dict[str, str]:
    if normalize_language(language, default="en") != "fr":
        return {
            "shared_meeting": "Shared meeting",
            "summary": "SUMMARY",
            "no_summary": "No summary available.",
            "key_wins": "KEY WINS",
            "improvement_areas": "IMPROVEMENT AREAS",
            "action_items": "ACTION ITEMS",
            "open_questions": "OPEN QUESTIONS",
            "scorecard": "SCORECARD",
            "overall_score": "Overall score",
            "content_score": "Content score",
            "form_score": "Form score",
            "transcript": "TRANSCRIPT",
        }
    return {
        "shared_meeting": "Réunion partagée",
        "summary": "RÉSUMÉ",
        "no_summary": "Aucun résumé disponible.",
        "key_wins": "POINTS FORTS",
        "improvement_areas": "AXES D’AMÉLIORATION",
        "action_items": "ACTIONS À RÉALISER",
        "open_questions": "QUESTIONS OUVERTES",
        "scorecard": "ÉVALUATION",
        "overall_score": "Score global",
        "content_score": "Score du contenu",
        "form_score": "Score de la forme",
        "transcript": "TRANSCRIPTION",
    }
