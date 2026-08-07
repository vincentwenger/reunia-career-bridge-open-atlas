from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import current_app, session

from meeting_assistant.repositories.action_repository import ActionRepository
from meeting_assistant.repositories.analytics_repository import AnalyticsRepository
from meeting_assistant.repositories.knowledge_repository import KnowledgeRepository
from meeting_assistant.repositories.support_repository import SupportRepository
from meeting_assistant.repositories.transcript_repository import TranscriptRepository
from meeting_assistant.repositories.user_repository import UserRepository
from meeting_assistant.utils.admin import current_session_is_admin
from meeting_assistant.utils.exceptions import ValidationError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ALLOWED_PERIODS = {7, 30, 90, 365}
_DESKTOP_DOWNLOAD_METRIC = "desktop_client_downloads"
_DESKTOP_USE_METRIC = "desktop_client_uses"

_FAILURE_METRICS = {
    "recording_failed",
    "meeting_processing_failed",
    "document_processing_failed",
    "ai_failure",
    "server_error",
}
_FAILURE_LABELS = {
    "recording_failed": "Recording failed",
    "meeting_processing_failed": "Interview processing failed",
    "document_processing_failed": "Document processing failed",
    "ai_failure": "AI request failed",
    "server_error": "Server error",
}

_ALLOWED_PRODUCT_METRICS = {
    "registration_completed",
    "feature_used",
    "recording_started",
    "recording_completed",
    "recording_uploaded",
    "recording_failed",
    "meeting_processing_started",
    "meeting_processing_succeeded",
    "meeting_processing_failed",
    "meeting_review_opened",
    "action_created",
    "action_completed",
    "document_uploaded",
    "document_processing_succeeded",
    "document_processing_failed",
    "ai_request",
    "ai_failure",
    "server_error",
}
_FEATURE_LABELS = {
    "career_bridge_overview": "Career Bridge Overview",
    "career_profile": "Career Profile",
    "baseline_resume": "Baseline Resume",
    "career_evidence_library": "Career Evidence Library",
    "job_discovery": "Job Discovery",
    "job_applications": "Job Applications",
    "resume_workflow": "Resume Workflow",
    "resume_reports": "Resume Reports",
    "application_materials": "Application Materials",
    "ai_configuration": "AI Configuration",
    "interview_preparation": "Interview Preparation",
    "mock_interview": "Mock Interview",
    "interview_review": "Interview Review",
    "career_action_plan": "Career Action Plan",
    "progress": "Progress & Outcomes",
    "admin_analytics": "Admin Analytics",
    "help_support": "Help & Support",
}
_FEATURE_ALIASES = {
    # Historical event identifiers remain readable after the Career Bridge rename.
    "meeting_preparation": "interview_preparation",
    "document_library": "career_evidence_library",
    "meeting_materials": "application_materials",
    "ai_context": "ai_configuration",
    "knowledge_search": "career_evidence_library",
    "browser_recorder": "mock_interview",
    "meeting_review": "interview_review",
    "action_center": "career_action_plan",
    "analytics": "progress",
    "career_translation": "baseline_resume",
    "support": "help_support",
}


def _canonical_feature(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    normalized = _FEATURE_ALIASES.get(normalized, normalized)
    return normalized if normalized in _FEATURE_LABELS else ""



class ActivityTrackingService:
    def __init__(self, repository: AnalyticsRepository | None = None) -> None:
        self.repository = repository or current_app.extensions["analytics_repository"]

    def record(
        self,
        payload: dict[str, Any],
        *,
        visitor_id: str,
        session_id: str,
        activity_date: str | None = None,
        country_code: str | None = None,
    ) -> None:
        """Record activity using server-issued identity and time values."""
        trusted_visitor_id = self._identifier(visitor_id, "visitor_id")
        trusted_session_id = self._identifier(session_id, "session_id")
        trusted_activity_date = activity_date or datetime.now(timezone.utc).date().isoformat()
        if not _DATE_RE.fullmatch(trusted_activity_date):
            raise ValidationError("Invalid server activity date.")

        page_path = str(payload.get("page_path") or "/").strip()
        if not page_path.startswith("/") or len(page_path) > 240:
            page_path = "/"
        page_path = page_path.split("?", 1)[0]
        feature = _canonical_feature(payload.get("feature"))

        max_seconds = int(current_app.config.get("ANALYTICS_MAX_HEARTBEAT_SECONDS", 60))
        try:
            active_seconds = int(payload.get("active_seconds") or 0)
        except (TypeError, ValueError):
            active_seconds = 0
        active_seconds = max(0, min(active_seconds, max_seconds))
        page_views = 1 if bool(payload.get("page_view")) else 0

        user_id = str(session.get("user_id") or "").strip() or None
        if (
            user_id
            and current_session_is_admin()
            and current_app.config.get("ANALYTICS_EXCLUDE_ADMIN_ACTIVITY", True)
        ):
            return

        event = {
            "session_key": f"{trusted_session_id}#{trusted_activity_date}",
            "session_id": trusted_session_id,
            "visitor_id": trusted_visitor_id,
            "activity_date": trusted_activity_date,
            "analytics_date": trusted_activity_date,
            "identity_type": "registered" if user_id else "guest",
            "user_id": user_id,
            "observed_at": int(time.time()),
            "page_path": page_path,
            "feature": feature,
            "active_seconds": active_seconds,
            "page_views": page_views,
        }
        normalized_country = str(country_code or "").strip().upper()
        if not user_id and re.fullmatch(r"[A-Z]{2}", normalized_country) and normalized_country not in {
            "XX", "ZZ"
        }:
            event["country_code"] = normalized_country
        self.repository.record_activity(event)

    @staticmethod
    def _identifier(value: Any, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not _IDENTIFIER_RE.fullmatch(normalized):
            raise ValidationError(f"Invalid {field_name}.")
        return normalized


class UsageMetricsService:
    """Stores durable, content-free product usage events."""

    def __init__(self, repository: AnalyticsRepository | None = None) -> None:
        self.repository = repository or current_app.extensions["analytics_repository"]

    def record_desktop_client_download(
        self,
        user_id: str,
        *,
        occurred_at: str | None = None,
    ) -> bool:
        """Count a desktop installer download tied to a signed-in account."""
        return self._record_counter_event(
            _DESKTOP_DOWNLOAD_METRIC,
            user_id,
            event_id=uuid.uuid4().hex,
            occurred_at=occurred_at,
            event_source="web_download",
        )

    def record_desktop_client_use(
        self,
        user_id: str,
        *,
        event_id: str | None = None,
        occurred_at: str | None = None,
    ) -> bool:
        """Count a successful desktop-client sign-in/use event."""
        return self._record_counter_event(
            _DESKTOP_USE_METRIC,
            user_id,
            event_id=event_id or uuid.uuid4().hex,
            occurred_at=occurred_at,
            event_source="desktop_authentication",
        )

    def record_ai_response(
        self,
        user_id: str,
        response: Any,
        *,
        feature: str,
        model: str,
        event_id: str | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        return self.record_ai_usage_report(
            user_id,
            getattr(response, "usage", None),
            feature=feature,
            model=model,
            event_id=event_id,
            duration_ms=duration_ms,
        )

    def record_ai_usage_report(
        self,
        user_id: str,
        usage: Any,
        *,
        feature: str,
        model: str,
        event_id: str | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        input_tokens = self._usage_value(usage, "prompt_tokens", "input_tokens")
        output_tokens = self._usage_value(usage, "completion_tokens", "output_tokens")
        cached_input_tokens = self._nested_usage_value(
            usage,
            ("prompt_tokens_details", "cached_tokens"),
            ("input_tokens_details", "cached_tokens"),
        )
        return self.record_ai_usage(
            user_id,
            feature=feature,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            event_id=event_id,
            duration_ms=duration_ms,
            usage_reported=usage is not None,
        )

    def record_ai_usage(
        self,
        user_id: str,
        *,
        feature: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
        event_id: str | None = None,
        duration_ms: int | None = None,
        usage_reported: bool = True,
    ) -> bool:
        input_tokens = max(0, int(input_tokens or 0))
        output_tokens = max(0, int(output_tokens or 0))
        cached_input_tokens = min(
            input_tokens,
            max(0, int(cached_input_tokens or 0)),
        )
        pricing = self._model_pricing(model)
        estimated_cost: float | None = None
        pricing_source = "unavailable"
        if usage_reported and pricing:
            uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
            estimated_cost = (
                uncached_input_tokens * pricing["input"]
                + cached_input_tokens * pricing["cached_input"]
                + output_tokens * pricing["output"]
            ) / 1_000_000
            pricing_source = "model"
        elif usage_reported:
            input_rate = float(
                current_app.config.get("ANALYTICS_AI_INPUT_COST_PER_MILLION", 0) or 0
            )
            output_rate = float(
                current_app.config.get("ANALYTICS_AI_OUTPUT_COST_PER_MILLION", 0) or 0
            )
            if input_rate > 0 or output_rate > 0:
                estimated_cost = (
                    input_tokens * input_rate + output_tokens * output_rate
                ) / 1_000_000
                pricing_source = "fallback"

        metadata: dict[str, Any] = {
            "feature": feature,
            "model": model,
            "request_type": "text",
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": int(duration_ms or 0),
            "usage_reported": bool(usage_reported),
            "cost_calculated": estimated_cost is not None,
            "pricing_source": pricing_source,
            "success": True,
        }
        if estimated_cost is not None:
            metadata["estimated_cost_usd"] = round(estimated_cost, 8)
        return self.record_product_event(
            "ai_request",
            user_id,
            event_id=event_id,
            metadata=metadata,
        )

    def record_transcription_usage(
        self,
        user_id: str,
        *,
        feature: str,
        model: str,
        audio_seconds: float,
        event_id: str | None = None,
        duration_ms: int | None = None,
        source: str | None = None,
    ) -> bool:
        normalized_seconds = max(0.0, float(audio_seconds or 0))
        rate = self._transcription_pricing(model)
        estimated_cost = (
            normalized_seconds / 60.0 * rate
            if rate is not None and normalized_seconds > 0
            else None
        )
        metadata: dict[str, Any] = {
            "feature": feature,
            "model": model,
            "request_type": "transcription",
            "audio_seconds": round(normalized_seconds, 3),
            "duration_ms": int(duration_ms or 0),
            "usage_reported": normalized_seconds > 0,
            "cost_calculated": estimated_cost is not None,
            "pricing_source": "model" if rate is not None else "unavailable",
            "success": True,
        }
        if source:
            metadata["audio_source"] = source
        if estimated_cost is not None:
            metadata["estimated_cost_usd"] = round(estimated_cost, 8)
        return self.record_product_event(
            "ai_request",
            user_id,
            event_id=event_id,
            metadata=metadata,
        )

    @staticmethod
    def _usage_value(usage: Any, *names: str) -> int:
        for name in names:
            value = getattr(usage, name, None) if usage is not None else None
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    @classmethod
    def _nested_usage_value(cls, usage: Any, *paths: tuple[str, str]) -> int:
        for parent_name, child_name in paths:
            parent = getattr(usage, parent_name, None) if usage is not None else None
            if parent is None and isinstance(usage, dict):
                parent = usage.get(parent_name)
            value = getattr(parent, child_name, None) if parent is not None else None
            if value is None and isinstance(parent, dict):
                value = parent.get(child_name)
            try:
                if value is not None:
                    return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    @staticmethod
    def _matching_pricing_entry(model: str, table: Any) -> Any:
        normalized_model = str(model or "").strip().lower()
        if not normalized_model or not isinstance(table, dict):
            return None
        normalized_table = {
            str(key or "").strip().lower(): value
            for key, value in table.items()
            if str(key or "").strip()
        }
        for configured_model in sorted(normalized_table, key=len, reverse=True):
            if (
                normalized_model == configured_model
                or normalized_model.startswith(f"{configured_model}-")
            ):
                return normalized_table[configured_model]
        return None

    @classmethod
    def _model_pricing(cls, model: str) -> dict[str, float] | None:
        entry = cls._matching_pricing_entry(
            model,
            current_app.config.get("ANALYTICS_AI_MODEL_PRICING", {}),
        )
        if not isinstance(entry, dict):
            return None
        try:
            input_rate = max(0.0, float(entry.get("input", 0) or 0))
            cached_input_rate = max(
                0.0,
                float(entry.get("cached_input", input_rate) or input_rate),
            )
            output_rate = max(0.0, float(entry.get("output", 0) or 0))
        except (TypeError, ValueError):
            return None
        return {
            "input": input_rate,
            "cached_input": cached_input_rate,
            "output": output_rate,
        }

    @classmethod
    def _transcription_pricing(cls, model: str) -> float | None:
        entry = cls._matching_pricing_entry(
            model,
            current_app.config.get("ANALYTICS_TRANSCRIPTION_MODEL_PRICING", {}),
        )
        try:
            return max(0.0, float(entry)) if entry is not None else None
        except (TypeError, ValueError):
            return None

    def record_product_event(
        self,
        metric: str,
        user_id: str,
        *,
        event_id: str | None = None,
        occurred_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Record a sanitized, content-free product event."""
        normalized_metric = str(metric or "").strip().lower()
        normalized_user = str(user_id or "").strip()
        if normalized_metric not in _ALLOWED_PRODUCT_METRICS or not normalized_user:
            return False

        safe_metadata: dict[str, Any] = {}
        for key, value in (metadata or {}).items():
            safe_key = str(key or "").strip()[:64]
            if not safe_key or safe_key in {"content", "prompt", "response", "transcript", "message"}:
                continue
            if isinstance(value, bool):
                safe_metadata[safe_key] = value
            elif isinstance(value, (int, float)):
                safe_metadata[safe_key] = value
            elif value is not None:
                safe_metadata[safe_key] = str(value)[:240]

        normalized_event = str(event_id or uuid.uuid4().hex).strip()
        digest = hashlib.sha256(
            f"{normalized_metric}\0{normalized_user}\0{normalized_event}".encode("utf-8")
        ).hexdigest()
        event = {
            "session_key": f"usage#{normalized_metric}#{digest}",
            "metric": normalized_metric,
            "user_id": normalized_user,
            "source_id": digest,
            **self._time_fields(occurred_at),
            "observed_at": int(time.time()),
            **safe_metadata,
        }
        return self.repository.record_usage_event(event)

    @staticmethod
    def _time_fields(occurred_at: str | None) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        timestamp = str(occurred_at or now.isoformat()).strip()
        date_value = timestamp[:10] if _DATE_RE.match(timestamp) else now.date().isoformat()
        return {"occurred_at": timestamp, "analytics_date": date_value}

    def _record_counter_event(
        self,
        metric: str,
        user_id: str,
        *,
        event_id: str,
        occurred_at: str | None,
        event_source: str,
    ) -> bool:
        normalized_user = str(user_id or "").strip()
        normalized_event = str(event_id or "").strip()
        if not normalized_user or not normalized_event:
            return False

        digest = hashlib.sha256(
            f"{metric}\0{normalized_user}\0{normalized_event}".encode("utf-8")
        ).hexdigest()
        event = {
            "session_key": f"usage#{metric}#{digest}",
            "metric": metric,
            "user_id": normalized_user,
            # Store only a one-way identifier; desktop authentication tokens are never persisted.
            "source_id": digest,
            "event_source": event_source,
            **self._time_fields(occurred_at),
            "observed_at": int(time.time()),
        }
        return self.repository.record_usage_event(event)

from .admin_analytics_helpers import AdminAnalyticsHelperMixin
from .admin_analytics_incidents import AdminAnalyticsIncidentMixin
from .admin_analytics_metrics import AdminAnalyticsMetricsMixin
from .admin_analytics_users import AdminAnalyticsUserMixin

class AdminAnalyticsService(
    AdminAnalyticsIncidentMixin,
    AdminAnalyticsUserMixin,
    AdminAnalyticsMetricsMixin,
    AdminAnalyticsHelperMixin,
):
    """Compose focused analytics capabilities behind the existing public service."""

    def __init__(
        self,
        analytics_repository: AnalyticsRepository | None = None,
        user_repository: UserRepository | None = None,
        knowledge_repository: KnowledgeRepository | None = None,
        transcript_repository: TranscriptRepository | None = None,
        action_repository: ActionRepository | None = None,
        support_repository: SupportRepository | None = None,
    ) -> None:
        self._cacheable = all(
            repository is None
            for repository in (
                analytics_repository, user_repository, knowledge_repository,
                transcript_repository, action_repository, support_repository,
            )
        )
        self.analytics_repository = (
            analytics_repository or current_app.extensions["analytics_repository"]
        )
        self.user_repository = user_repository or UserRepository()
        self.knowledge_repository = (
            knowledge_repository or current_app.extensions["knowledge_repository"]
        )
        # Transcript storage currently has no in-memory app extension. Avoid an
        # accidental AWS call in the generic testing configuration unless a test
        # explicitly supplies a repository.
        self.transcript_repository = (
            transcript_repository
            or current_app.extensions.get("admin_transcript_repository")
        )
        if self.transcript_repository is None and not current_app.testing:
            self.transcript_repository = TranscriptRepository()
        self.action_repository = action_repository or current_app.extensions.get("action_repository")
        self.support_repository = support_repository or current_app.extensions.get("support_repository")

_ADMIN_ANALYTICS_MIXINS = (
    AdminAnalyticsIncidentMixin,
    AdminAnalyticsUserMixin,
    AdminAnalyticsMetricsMixin,
    AdminAnalyticsHelperMixin,
)

from . import admin_analytics_helpers as _analytics_helpers
from . import admin_analytics_incidents as _analytics_incidents
from . import admin_analytics_metrics as _analytics_metrics
from . import admin_analytics_users as _analytics_users

for _module in (
    _analytics_incidents,
    _analytics_users,
    _analytics_metrics,
    _analytics_helpers,
):
    _module.activate(globals())
