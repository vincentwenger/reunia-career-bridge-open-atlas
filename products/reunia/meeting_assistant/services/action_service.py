from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from flask import current_app

from meeting_assistant.repositories.action_repository import ActionRepository
from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.utils.exceptions import (
    DatabaseError,
    ResourceNotFoundError,
    ValidationError,
)
from meeting_assistant.utils.json_parsing import to_json_compatible


_ALLOWED_PRIORITIES = {"none", "low", "medium", "high", "urgent"}
_ALLOWED_STATUSES = {"not_started", "in_progress", "blocked", "done"}
_ALLOWED_SOURCES = {"manual", "meeting"}
_ACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


class ActionService:
    def __init__(
        self,
        repository: ActionRepository | None = None,
        transcript_service: TranscriptService | None = None,
    ) -> None:
        self.repository = repository or current_app.extensions["action_repository"]
        self.transcript_service = transcript_service or TranscriptService()

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        try:
            stored_items = self.repository.list_for_user(user_id)
        except (BotoCoreError, ClientError) as exc:
            raise DatabaseError("Failed to retrieve action items.") from exc

        meetings = self.transcript_service.list_for_user(user_id)
        derived_actions = self._derive_actions_from_meetings(user_id, meetings)
        stored_by_id = {
            str(item.get("action_id") or ""): to_json_compatible(item)
            for item in stored_items
            if item.get("action_id")
        }

        merged: list[dict[str, Any]] = []
        consumed_ids: set[str] = set()

        for derived in derived_actions:
            action_id = derived["action_id"]
            stored = stored_by_id.get(action_id)
            consumed_ids.add(action_id)

            if stored and stored.get("deleted"):
                continue

            if stored:
                effective = {**derived, **stored}
                # Keep the meeting label/date current after a meeting is renamed,
                # unless the action was explicitly relinked to another meeting.
                if str(stored.get("meeting_id") or "") == derived["meeting_id"]:
                    effective["meeting_name"] = derived["meeting_name"]
                    effective["meeting_date"] = derived["meeting_date"]
                    effective["meeting_timestamp"] = derived["meeting_timestamp"]
            else:
                effective = derived

            effective.pop("deleted", None)
            effective.pop("deleted_at", None)
            merged.append(self._public_action(effective))

        # Keep manual actions and previously managed meeting actions available,
        # even if the source meeting was later removed.
        for action_id, stored in stored_by_id.items():
            if action_id in consumed_ids or stored.get("deleted"):
                continue
            merged.append(self._public_action(stored))

        merged.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("action_id") or ""),
            ),
            reverse=True,
        )
        return merged

    def create(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now()
        requested_id = str(data.get("action_id") or data.get("id") or "").strip()
        action_id = (
            requested_id
            if requested_id.startswith("manual-") and _ACTION_ID_PATTERN.fullmatch(requested_id)
            else f"manual-{uuid4()}"
        )

        item = self._validated_action(
            user_id=user_id,
            action_id=action_id,
            data=data,
            existing=None,
            source="manual",
            created_at=now,
        )

        try:
            existing = self.repository.get(user_id, action_id)
            if existing:
                # Makes browser-to-server migration safe to retry.
                item["created_at"] = str(existing.get("created_at") or item["created_at"])
                self.repository.save(item)
            else:
                self.repository.create(item)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise ValidationError("An action with this ID already exists.") from exc
            raise DatabaseError("Failed to save the action.") from exc
        except BotoCoreError as exc:
            raise DatabaseError("Failed to save the action.") from exc

        return self._public_action(item)

    def update(
        self,
        user_id: str,
        action_id: Any,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        action_id = self._validate_action_id(action_id)
        current = self._find_effective_action(user_id, action_id)
        if not current:
            raise ResourceNotFoundError("Action not found.")

        item = self._validated_action(
            user_id=user_id,
            action_id=action_id,
            data=data,
            existing=current,
            source=str(current.get("source") or "manual"),
            created_at=str(current.get("created_at") or _utc_now()),
        )
        item["updated_at"] = _utc_now()

        try:
            self.repository.save(item)
        except (BotoCoreError, ClientError) as exc:
            raise DatabaseError("Failed to update the action.") from exc

        return self._public_action(item)

    def delete(self, user_id: str, action_id: Any) -> dict[str, Any]:
        action_id = self._validate_action_id(action_id)
        current = self._find_effective_action(user_id, action_id)
        if not current:
            raise ResourceNotFoundError("Action not found.")

        try:
            if str(current.get("source") or "manual") == "meeting":
                tombstone = {
                    "user_id": user_id,
                    "action_id": action_id,
                    "source": "meeting",
                    "meeting_id": str(current.get("meeting_id") or ""),
                    "meeting_timestamp": str(current.get("meeting_timestamp") or ""),
                    "deleted": True,
                    "deleted_at": _utc_now(),
                    "updated_at": _utc_now(),
                    "created_at": str(current.get("created_at") or _utc_now()),
                }
                self.repository.save(tombstone)
            else:
                self.repository.delete(user_id, action_id)
        except ResourceNotFoundError:
            raise
        except (BotoCoreError, ClientError) as exc:
            raise DatabaseError("Failed to delete the action.") from exc

        return {"message": "Action successfully deleted.", "action_id": action_id}

    def _find_effective_action(self, user_id: str, action_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.list_for_user(user_id) if item["action_id"] == action_id),
            None,
        )

    def _validated_action(
        self,
        *,
        user_id: str,
        action_id: str,
        data: dict[str, Any],
        existing: dict[str, Any] | None,
        source: str,
        created_at: str,
    ) -> dict[str, Any]:
        base = dict(existing or {})
        description = _bounded_text(
            data.get("description", base.get("description")),
            "description",
            1000,
            required=True,
        )
        owner = _bounded_text(
            data.get("owner", data.get("assignee", base.get("owner", "Unassigned"))),
            "owner",
            200,
        ) or "Unassigned"

        priority = str(data.get("priority", base.get("priority", "none")) or "none").strip().lower()
        if priority not in _ALLOWED_PRIORITIES:
            raise ValidationError("priority must be none, low, medium, high, or urgent.")

        status = str(data.get("status", base.get("status", "not_started")) or "not_started").strip().lower()
        if status not in _ALLOWED_STATUSES:
            raise ValidationError("status must be not_started, in_progress, blocked, or done.")

        normalized_source = source if source in _ALLOWED_SOURCES else "manual"
        due_date = _optional_date(data.get("due_date", base.get("due_date")), "due_date")
        meeting_date = _optional_date(data.get("meeting_date", base.get("meeting_date")), "meeting_date")
        meeting_id = _bounded_text(data.get("meeting_id", base.get("meeting_id")), "meeting_id", 300)
        meeting_name = _bounded_text(
            data.get("meeting_name", base.get("meeting_name")),
            "meeting_name",
            300,
        ) or ("No linked meeting" if not meeting_id else "Linked meeting")
        meeting_timestamp = _bounded_text(
            data.get("meeting_timestamp", base.get("meeting_timestamp")),
            "meeting_timestamp",
            100,
        )

        completed_at = base.get("completed_at")
        if status == "done":
            completed_at = _optional_datetime(data.get("completed_at", completed_at)) or _utc_now()
        else:
            completed_at = None

        return {
            "user_id": user_id,
            "action_id": action_id,
            "description": description,
            "meeting_id": meeting_id,
            "meeting_name": meeting_name,
            "meeting_date": meeting_date,
            "meeting_timestamp": meeting_timestamp,
            "owner": owner,
            "due_date": due_date,
            "priority": priority,
            "status": status,
            "source": normalized_source,
            "created_at": _optional_datetime(created_at) or _utc_now(),
            "completed_at": completed_at,
        }

    def _derive_actions_from_meetings(
        self,
        user_id: str,
        meetings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for meeting_index, meeting in enumerate(meetings):
            meeting_id = str(
                _unwrap_scalar(meeting.get("meeting_id"))
                or _unwrap_scalar(meeting.get("transcript_id"))
                or _unwrap_scalar(meeting.get("id"))
                or _unwrap_scalar(meeting.get("timestamp"))
                or f"meeting-{meeting_index}"
            )
            timestamp = str(_unwrap_scalar(meeting.get("timestamp")) or "")
            meeting_name = str(
                _unwrap_scalar(meeting.get("meeting_name"))
                or f"Meeting {meeting_index + 1}"
            )
            meeting_date = _date_from_value(timestamp)
            raw_actions = _unwrap_list(meeting.get("action_items"))

            for action_index, raw_action in enumerate(raw_actions):
                action_value = _unwrap_value(raw_action)
                action_object = action_value if isinstance(action_value, dict) else {}
                description = _action_text(action_value)
                if not description:
                    continue

                action_id = f"meeting-{_js_hash_base36(f'{meeting_id}|{action_index}|{description}')}"
                priority = str(_unwrap_scalar(action_object.get("priority")) or "none").lower()
                status = str(
                    _unwrap_scalar(action_object.get("status"))
                    or ("done" if _unwrap_scalar(action_object.get("completed")) else "not_started")
                ).lower()

                if priority not in _ALLOWED_PRIORITIES:
                    priority = "none"
                if status not in _ALLOWED_STATUSES:
                    status = "not_started"

                owner = str(
                    _unwrap_scalar(action_object.get("owner"))
                    or _unwrap_scalar(action_object.get("assignee"))
                    or "Unassigned"
                ).strip() or "Unassigned"
                due_date = _optional_date(
                    _unwrap_scalar(action_object.get("due_date"))
                    or _unwrap_scalar(action_object.get("deadline"))
                    or _unwrap_scalar(action_object.get("due")),
                    "due_date",
                    strict=False,
                )
                completed_at = (
                    _optional_datetime(_unwrap_scalar(action_object.get("completed_at")))
                    if status == "done"
                    else None
                )

                actions.append(
                    {
                        "user_id": user_id,
                        "action_id": action_id,
                        "description": description[:1000],
                        "meeting_id": meeting_id,
                        "meeting_name": meeting_name[:300],
                        "meeting_date": meeting_date,
                        "meeting_timestamp": timestamp[:100],
                        "owner": owner[:200],
                        "due_date": due_date,
                        "priority": priority,
                        "status": status,
                        "source": "meeting",
                        "created_at": _optional_datetime(timestamp) or _utc_now(),
                        "completed_at": completed_at,
                    }
                )
        return actions

    @staticmethod
    def _public_action(item: dict[str, Any]) -> dict[str, Any]:
        result = to_json_compatible(item)
        result.pop("user_id", None)
        result.pop("deleted", None)
        result.pop("deleted_at", None)
        result["id"] = result.get("action_id")
        return result

    @staticmethod
    def _validate_action_id(value: Any) -> str:
        action_id = str(value or "").strip()
        if not _ACTION_ID_PATTERN.fullmatch(action_id):
            raise ValidationError("A valid action_id is required.")
        return action_id


def _bounded_text(
    value: Any,
    label: str,
    maximum: int,
    *,
    required: bool = False,
) -> str:
    value = _unwrap_scalar(value)
    cleaned = str(value or "").strip()
    if required and not cleaned:
        raise ValidationError(f"{label} is required.")
    if len(cleaned) > maximum:
        raise ValidationError(f"{label} must be {maximum} characters or fewer.")
    return cleaned


def _optional_date(value: Any, label: str, *, strict: bool = True) -> str:
    value = _unwrap_scalar(value)
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    candidate = cleaned[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError as exc:
        if strict:
            raise ValidationError(f"{label} must use YYYY-MM-DD format.") from exc
        return ""
    return candidate


def _optional_datetime(value: Any) -> str:
    value = _unwrap_scalar(value)
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _date_from_value(value: Any) -> str:
    cleaned = str(_unwrap_scalar(value) or "").strip()
    if not cleaned:
        return ""
    try:
        return date.fromisoformat(cleaned[:10]).isoformat()
    except ValueError:
        return ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unwrap_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if not isinstance(value, dict):
        return value
    if set(value) == {"S"}:
        return value["S"]
    if set(value) == {"N"}:
        return value["N"]
    if set(value) == {"BOOL"}:
        return value["BOOL"]
    if set(value) == {"NULL"}:
        return None
    if set(value) == {"L"}:
        return [_unwrap_value(item) for item in value["L"]]
    if set(value) == {"M"}:
        return {key: _unwrap_value(item) for key, item in value["M"].items()}
    return {key: _unwrap_value(item) for key, item in value.items()}


def _unwrap_scalar(value: Any) -> Any:
    unwrapped = _unwrap_value(value)
    return unwrapped if not isinstance(unwrapped, (dict, list)) else ""


def _unwrap_list(value: Any) -> list[Any]:
    unwrapped = _unwrap_value(value)
    return unwrapped if isinstance(unwrapped, list) else []


def _action_text(value: Any) -> str:
    value = _unwrap_value(value)
    if isinstance(value, dict):
        for field in ("description", "task", "text", "action"):
            text = str(_unwrap_scalar(value.get(field)) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _js_hash_base36(value: str) -> str:
    """Match the Action Center's JavaScript FNV-1a ID generation."""
    hash_value = 2166136261
    encoded = str(value or "").encode("utf-16-le", "surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        hash_value ^= code_unit
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return _base36(hash_value)


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = digits[remainder] + result
    return result
