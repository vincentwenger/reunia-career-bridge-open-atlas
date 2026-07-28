from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import current_app

from meeting_assistant.utils.exceptions import ResourceNotFoundError, ValidationError

_ALLOWED_STATUSES = {"new", "read", "resolved"}


class AdminSupportService:
    """Administrator-facing access to Help & Support submissions."""

    def __init__(self, repository=None) -> None:
        self.repository = repository or current_app.extensions["support_repository"]

    def inbox(self) -> dict[str, Any]:
        requests = [self._summary(item) for item in self.repository.list_all()]
        requests.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        counts = {status: 0 for status in _ALLOWED_STATUSES}
        for item in requests:
            counts[item["status"]] += 1
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(requests),
                "new": counts["new"],
                "read": counts["read"],
                "resolved": counts["resolved"],
            },
            "requests": requests,
        }

    def get(self, request_id: str, *, mark_read: bool = True) -> dict[str, Any]:
        normalized = self._request_id(request_id)
        item = self.repository.get_by_id(normalized)
        if not item:
            raise ResourceNotFoundError("Support request not found.")
        if mark_read and self._status(item.get("status")) == "new":
            updated = self.repository.update_status(
                normalized,
                "read",
                datetime.now(timezone.utc).isoformat(),
            )
            if updated:
                item = updated
        return self._detail(item)

    def set_status(self, request_id: str, status: Any) -> dict[str, Any]:
        normalized = self._request_id(request_id)
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in _ALLOWED_STATUSES:
            raise ValidationError("Status must be new, read, or resolved.")
        updated = self.repository.update_status(
            normalized,
            normalized_status,
            datetime.now(timezone.utc).isoformat(),
        )
        if not updated:
            raise ResourceNotFoundError("Support request not found.")
        return self._detail(updated)

    def attachment_metadata(self, request_id: str) -> dict[str, str]:
        normalized = self._request_id(request_id)
        item = self.repository.get_by_id(normalized)
        if not item:
            raise ResourceNotFoundError("Support request not found.")
        attachment = item.get("attachment") if isinstance(item.get("attachment"), dict) else {}
        bucket = str(attachment.get("bucket") or "").strip()
        object_key = str(attachment.get("object_key") or "").strip()
        if not bucket or not object_key:
            raise ResourceNotFoundError("This support request has no attachment.")
        return {
            "bucket": bucket,
            "object_key": object_key,
            "filename": str(attachment.get("filename") or "support-attachment"),
        }

    @staticmethod
    def _request_id(value: Any) -> str:
        request_id = str(value or "").strip()
        if not request_id or len(request_id) > 80 or not request_id.startswith("SUP-"):
            raise ValidationError("Invalid support request ID.")
        return request_id

    @staticmethod
    def _status(value: Any) -> str:
        status = str(value or "new").strip().lower()
        return status if status in _ALLOWED_STATUSES else "new"

    def _summary(self, item: dict[str, Any]) -> dict[str, Any]:
        attachment = item.get("attachment") if isinstance(item.get("attachment"), dict) else {}
        return {
            "request_id": str(item.get("request_id") or ""),
            "created_at": str(item.get("created_at") or ""),
            "status": self._status(item.get("status")),
            "name": str(item.get("name") or ""),
            "email": str(item.get("email") or ""),
            "subject": str(item.get("subject") or "No subject"),
            "topic_label": str(item.get("topic_label") or item.get("topic") or "Other"),
            "area_label": str(item.get("area_label") or item.get("area") or "Other"),
            "user_id": str(item.get("user_id") or ""),
            "has_attachment": bool(attachment.get("bucket") and attachment.get("object_key")),
        }

    def _detail(self, item: dict[str, Any]) -> dict[str, Any]:
        detail = self._summary(item)
        detail.update({
            "message": str(item.get("message") or ""),
            "page_url": str(item.get("page_url") or ""),
            "status_updated_at": str(item.get("status_updated_at") or ""),
            "read_at": str(item.get("read_at") or ""),
            "resolved_at": str(item.get("resolved_at") or ""),
        })
        attachment = item.get("attachment") if isinstance(item.get("attachment"), dict) else {}
        if detail["has_attachment"]:
            detail["attachment"] = {
                "filename": str(attachment.get("filename") or "support-attachment"),
                "content_type": str(attachment.get("content_type") or "application/octet-stream"),
                "size_bytes": int(attachment.get("size_bytes") or 0),
            }
        return detail
