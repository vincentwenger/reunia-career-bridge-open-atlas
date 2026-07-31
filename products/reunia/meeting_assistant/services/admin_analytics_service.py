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
from meeting_assistant.utils.feature_access import live_interview_assistance_access

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ALLOWED_PERIODS = {7, 30, 90, 365}
_LIVE_QA_METRIC = "live_qa_answers"
_DESKTOP_DOWNLOAD_METRIC = "desktop_client_downloads"
_DESKTOP_USE_METRIC = "desktop_client_uses"

_FAILURE_METRICS = {
    "recording_failed",
    "meeting_processing_failed",
    "document_processing_failed",
    "live_qa_failure",
    "ai_failure",
}
_FAILURE_LABELS = {
    "recording_failed": "Recording failed",
    "meeting_processing_failed": "Interview processing failed",
    "document_processing_failed": "Document processing failed",
    "live_qa_failure": "Live Assistance failed",
    "ai_failure": "AI request failed",
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
    "live_qa_session_started",
    "live_qa_request",
    "live_qa_failure",
    "ai_request",
    "ai_failure",
}
_FEATURE_LABELS = {
    "meeting_preparation": "Interview Preparation",
    "document_library": "Career Evidence Library",
    "meeting_materials": "Application Materials",
    "ai_context": "AI Configuration",
    "knowledge_search": "Career Evidence Search",
    "browser_recorder": "Mock Interview",
    "desktop_client": "Mock Interview Desktop Recorder",
    "live_qa": "Live Assistance",
    "meeting_review": "Interview Review",
    "action_center": "Career Action Plan",
    "analytics": "Impact & Progress",
}


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
    """Stores durable, content-free product usage events.

    Live Q&A feed records expire after the user's configured retention period, so
    lifetime answer totals must be recorded separately. The event key is
    deterministic, making retries idempotent for the same Live Q&A entry.
    """

    def __init__(self, repository: AnalyticsRepository | None = None) -> None:
        self.repository = repository or current_app.extensions["analytics_repository"]

    def record_live_qa_answer(
        self,
        user_id: str,
        entry_id: str,
        *,
        occurred_at: str | None = None,
        answer_origin: str = "ai_generated",
    ) -> bool:
        normalized_user = str(user_id or "").strip()
        normalized_entry = str(entry_id or "").strip()
        if not normalized_user or not normalized_entry:
            return False

        digest = hashlib.sha256(
            f"{normalized_user}\0{normalized_entry}".encode("utf-8")
        ).hexdigest()
        event = {
            "session_key": f"usage#{_LIVE_QA_METRIC}#{digest}",
            "metric": _LIVE_QA_METRIC,
            "user_id": normalized_user,
            "source_id": normalized_entry,
            "answer_origin": str(answer_origin or "ai_generated"),
            **self._time_fields(occurred_at),
            "observed_at": int(time.time()),
        }
        return self.repository.record_usage_event(event)

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


class AdminAnalyticsService:
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

    def dashboard(self, period: str | int | None = None) -> dict[str, Any]:
        days = self._normalize_period(period)
        cache = current_app.extensions.get("admin_analytics_cache")
        cache_key = f"dashboard:{days}"
        if self._cacheable and cache is not None:
            try:
                cached = cache.get(cache_key)
            except Exception:
                current_app.logger.exception(
                    "Could not read the Admin Analytics cache; loading live data"
                )
                cached = None
            if isinstance(cached, dict):
                return cached

        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        period_start_date = now.date() - timedelta(days=days - 1)
        period_start = period_start_date.isoformat()
        previous_period_end = (period_start_date - timedelta(days=1)).isoformat()
        previous_period_start = (period_start_date - timedelta(days=days)).isoformat()

        core_sources = {
            "activity": True,
            "users": True,
        }
        try:
            all_activity = self.analytics_repository.list_activity()
        except Exception:
            current_app.logger.exception(
                "Could not load visitor and session activity for Admin Analytics"
            )
            all_activity = []
            core_sources["activity"] = False
        period_activity = [
            item for item in all_activity
            if str(item.get("activity_date") or "") >= period_start
        ]
        try:
            users = self._load_registered_users()
        except Exception:
            current_app.logger.exception(
                "Could not load registered users for Admin Analytics"
            )
            users = []
            core_sources["users"] = False
        usage = self._load_usage_snapshot()
        usage["sources"].update(core_sources)
        all_events = usage["events"]
        period_events = [
            item for item in all_events
            if self._event_date(item) >= period_start
        ]
        previous_activity = [
            item for item in all_activity
            if previous_period_start
            <= str(item.get("activity_date") or "")
            <= previous_period_end
        ]
        previous_events = [
            item for item in all_events
            if previous_period_start
            <= self._event_date(item)
            <= previous_period_end
        ]

        period_guests = {
            str(item.get("visitor_id"))
            for item in period_activity
            if item.get("identity_type") == "guest" and item.get("visitor_id")
        }
        lifetime_guests = {
            str(item.get("visitor_id"))
            for item in all_activity
            if item.get("identity_type") == "guest" and item.get("visitor_id")
        }
        registered_user_keys = {
            self._user_key(user.get("user_id") or user.get("email"))
            for user in users
            if self._user_key(user.get("user_id") or user.get("email"))
        }
        active_registered = self._active_user_keys_for_window(
            users=users,
            all_activity=all_activity,
            usage=usage,
            start_date=period_start,
            end_date=today,
        ) & registered_user_keys
        period_registered_seconds = sum(
            self._integer(item.get("active_seconds"))
            for item in period_activity
            if item.get("user_id")
        )
        lifetime_registered_seconds = sum(
            self._integer(item.get("active_seconds"))
            for item in all_activity
            if item.get("user_id")
        )

        user_rows = self._build_user_rows(
            users, all_activity, period_start, today, usage
        )
        daily_series = self._daily_series(
            period_activity,
            period_events,
            usage,
            period_start,
            days,
        )
        guest_geography = self._guest_geography(period_activity)
        growth = self._growth_metrics(users, period_activity, all_activity, period_start)
        activation = self._activation_metrics(users, all_activity, usage)
        retention = self._retention_metrics(users, all_activity)
        funnel = self._meeting_funnel(period_events, usage, period_start)
        feature_adoption = self._feature_adoption(users, all_activity, all_events)
        reliability = self._reliability_metrics(period_events)
        previous_reliability = self._reliability_metrics(previous_events)
        document_health = self._document_health(usage, period_events, period_start)
        action_outcomes = self._action_outcomes(usage["actions"], period_start, today)
        support_health = self._support_health(usage["support_requests"], now)
        live_qa = self._live_qa_health(period_events)
        ai_usage = self._ai_usage(period_events)
        alerts = self._alerts(
            users=users,
            user_rows=user_rows,
            reliability=reliability,
            support_health=support_health,
            activation=activation,
            ai_usage=ai_usage,
        )

        previous_guests = {
            str(item.get("visitor_id"))
            for item in previous_activity
            if item.get("identity_type") == "guest" and item.get("visitor_id")
        }
        previous_active_registered = self._active_user_keys_for_window(
            users=users,
            all_activity=all_activity,
            usage=usage,
            start_date=previous_period_start,
            end_date=previous_period_end,
        ) & registered_user_keys
        previous_registered_seconds = sum(
            self._integer(item.get("active_seconds"))
            for item in previous_activity
            if item.get("user_id")
        )
        previous_registrations = sum(
            1
            for user in users
            if previous_period_start
            <= self._date_value(user.get("created_at"))
            <= previous_period_end
        )
        previous_conversion_rate = round(
            (previous_registrations / len(previous_guests) * 100)
            if previous_guests
            else 0,
            1,
        )
        comparisons = {
            "unique_guests": self._comparison(len(period_guests), len(previous_guests)),
            "active_registered_users": self._comparison(
                len(active_registered), len(previous_active_registered)
            ),
            "registered_active_seconds": self._comparison(
                period_registered_seconds, previous_registered_seconds
            ),
            "conversion_rate": self._comparison(
                growth["conversion_rate"], previous_conversion_rate
            ),
            "processing_success_rate": self._comparison(
                reliability["overall_success_rate"],
                previous_reliability["overall_success_rate"],
            ),
        }

        result = {
            "generated_at": now.isoformat(),
            "period_days": days,
            "period_start": period_start,
            "summary": {
                "unique_guests": len(period_guests),
                "lifetime_unique_guests": len(lifetime_guests),
                "registered_users": len(users),
                "active_registered_users": len(active_registered),
                "registered_active_seconds": period_registered_seconds,
                "lifetime_registered_active_seconds": lifetime_registered_seconds,
                "document_count": sum(len(items) for items in usage["documents"].values()),
                "document_total_bytes": sum(
                    self._integer(item.get("size_bytes"))
                    for items in usage["documents"].values()
                    for item in items
                ),
                "saved_meeting_count": sum(len(items) for items in usage["meetings"].values()),
                "live_qa_answer_count": sum(usage["live_qa_answers"].values()),
                "desktop_download_count": sum(usage["desktop_downloads"].values()),
                "desktop_use_count": sum(usage["desktop_uses"].values()),
                "activation_rate": activation["activation_rate"],
                "registration_conversion_rate": growth["conversion_rate"],
                "return_7_day_rate": retention["return_7_day_rate"],
                "processing_success_rate": reliability["overall_success_rate"],
            },
            "usage_sources": usage["sources"],
            "daily": daily_series,
            "guest_geography": guest_geography,
            "comparisons": comparisons,
            "growth": growth,
            "activation": activation,
            "retention": retention,
            "meeting_funnel": funnel,
            "feature_adoption": feature_adoption,
            "reliability": reliability,
            "document_health": document_health,
            "action_outcomes": action_outcomes,
            "support_health": support_health,
            "live_qa_health": live_qa,
            "ai_usage": ai_usage,
            "alerts": alerts,
            "users": user_rows,
        }
        # Do not cache a dashboard assembled without either core source. A
        # temporary DynamoDB or IAM failure should recover immediately after
        # the underlying service or permission is fixed instead of remaining
        # hidden behind the normal dashboard cache interval.
        core_sources_available = all(core_sources.values())
        if self._cacheable and cache is not None and core_sources_available:
            try:
                cache.set(
                    cache_key,
                    result,
                    int(current_app.config.get("ADMIN_ANALYTICS_CACHE_SECONDS", 60)),
                )
            except Exception:
                # Caching is an optimization. A Redis interruption must not
                # turn a successfully assembled dashboard into an HTTP 500.
                current_app.logger.exception(
                    "Could not write the Admin Analytics cache"
                )
        return result

    def incident_details(self) -> dict[str, Any]:
        """Return all recorded product failures as administrator-safe incidents."""
        users_available = True
        try:
            users = self._load_registered_users()
        except Exception:
            current_app.logger.exception(
                "Could not load registered users for Admin Analytics incidents"
            )
            users = []
            users_available = False
        user_lookup: dict[str, dict[str, Any]] = {}
        for user in users:
            user_id = str(user.get("user_id") or user.get("email") or "").strip()
            email = str(user.get("email") or user_id).strip()
            if user_id:
                user_lookup[user_id] = user
            if email:
                user_lookup.setdefault(email, user)

        try:
            events = [
                item for item in self.analytics_repository.list_usage_events()
                if str(item.get("metric") or "") in _FAILURE_METRICS
                and str(item.get("user_id") or "").strip()
            ]
            events_available = True
        except Exception:
            current_app.logger.exception("Could not load incident failure events")
            events = []
            events_available = False

        support_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        support_available = self.support_repository is not None
        if self.support_repository is not None:
            try:
                for item in self.support_repository.list_all():
                    if str(item.get("source") or "") != "browser_recorder_error":
                        continue
                    user_id = str(item.get("user_id") or item.get("email") or "").strip()
                    if user_id:
                        support_by_user[user_id].append(item)
            except Exception:
                current_app.logger.exception("Could not load incident support reports")
                support_available = False

        failure_counts: dict[str, int] = defaultdict(int)
        for event in events:
            failure_counts[str(event.get("user_id") or "").strip()] += 1

        incidents: list[dict[str, Any]] = []
        feature_values: set[str] = set()
        error_type_values: set[str] = set()
        status_values: set[str] = set()
        for event in events:
            user_id = str(event.get("user_id") or "").strip()
            user = user_lookup.get(user_id, {})
            email = str(user.get("email") or user_id).strip()
            reports = [*support_by_user.get(user_id, [])]
            if email and email != user_id:
                reports.extend(support_by_user.get(email, []))

            serialized = self._serialize_failure_event(event)
            reference_id = serialized.get("reference_id") or ""
            related_reports = self._match_incident_support_reports(
                reports,
                reference_id=reference_id,
                occurred_at=serialized.get("occurred_at") or "",
            )
            feature = self._incident_feature(serialized)
            cause = self._incident_cause(serialized)
            incident_status = self._incident_status(event, related_reports)
            incident_id = str(
                event.get("source_id")
                or event.get("session_key")
                or hashlib.sha256(
                    f"{user_id}\0{serialized.get('metric')}\0{serialized.get('occurred_at')}\0{reference_id}".encode("utf-8")
                ).hexdigest()
            )
            error_type = str(serialized.get("label") or "Recorded failure")
            feature_values.add(feature)
            error_type_values.add(error_type)
            status_values.add(incident_status)
            incidents.append({
                **serialized,
                "incident_id": incident_id,
                "user_id": user_id,
                "email": email,
                "full_name": str(user.get("full_name") or ""),
                "user_failure_count": failure_counts[user_id],
                "repeated_user": failure_counts[user_id] >= 3,
                "status": incident_status,
                "feature": feature,
                "error_type": error_type,
                "cause": cause,
                "support_reports": [
                    self._serialize_failure_support_report(item)
                    for item in related_reports
                ],
            })

        incidents.sort(
            key=lambda item: (
                str(item.get("occurred_at") or ""),
                str(item.get("email") or "").casefold(),
            ),
            reverse=True,
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "incident_count": len(incidents),
            "affected_user_count": len({item["user_id"] for item in incidents}),
            "repeated_user_count": len({
                item["user_id"] for item in incidents if item.get("repeated_user")
            }),
            "events_available": events_available,
            "support_reports_available": support_available,
            "users_available": users_available,
            "filters": {
                "features": sorted(feature_values, key=str.casefold),
                "error_types": sorted(error_type_values, key=str.casefold),
                "statuses": sorted(status_values, key=str.casefold),
            },
            "incidents": incidents,
        }

    @classmethod
    def _match_incident_support_reports(
        cls,
        reports: list[dict[str, Any]],
        *,
        reference_id: str,
        occurred_at: str,
    ) -> list[dict[str, Any]]:
        if not reports:
            return []
        reference = str(reference_id or "").strip().casefold()
        if reference:
            matched = [
                item for item in reports
                if reference in f"{item.get('subject', '')}\n{item.get('message', '')}".casefold()
            ]
            if matched:
                return sorted(
                    matched,
                    key=lambda item: str(item.get("created_at") or ""),
                    reverse=True,
                )

        incident_time = cls._parse_datetime(occurred_at)
        if incident_time:
            nearby: list[dict[str, Any]] = []
            for item in reports:
                report_time = cls._parse_datetime(item.get("created_at"))
                if report_time and abs((report_time - incident_time).total_seconds()) <= 3600:
                    nearby.append(item)
            if nearby:
                return sorted(
                    nearby,
                    key=lambda item: str(item.get("created_at") or ""),
                    reverse=True,
                )
        return []

    @staticmethod
    def _incident_feature(item: dict[str, Any]) -> str:
        explicit = str(item.get("feature") or "").strip()
        if explicit:
            return _FEATURE_LABELS.get(explicit, explicit.replace("_", " ").title())
        source = str(item.get("source") or "").strip().casefold()
        metric = str(item.get("metric") or "").strip()
        if "browser" in source or metric in {"recording_failed", "meeting_processing_failed"}:
            return "Mock Interview"
        if "desktop" in source:
            return "Mock Interview Desktop Recorder"
        if metric == "document_processing_failed":
            return "Document Library"
        if metric == "live_qa_failure":
            return "Live Assistance"
        if metric == "ai_failure":
            return "AI Assistance"
        return "Other"

    @staticmethod
    def _incident_cause(item: dict[str, Any]) -> str:
        explicit = str(
            item.get("reported_cause")
            or item.get("cause")
            or item.get("probable_cause")
            or item.get("root_cause")
            or ""
        ).strip()
        if explicit:
            return explicit

        http_status = str(item.get("http_status") or "").strip()
        text = " ".join([
            str(item.get("status_text") or ""),
            str(item.get("error_summary") or ""),
            str(item.get("stage") or ""),
        ]).casefold()
        metric = str(item.get("metric") or "")
        if http_status == "413" or "payload too large" in text or "upload limit" in text:
            return "The recording segment or request exceeded the configured upload-size limit."
        if http_status in {"401", "403"} or "unauthorized" in text or "forbidden" in text:
            return "The request was rejected because authentication or authorization was not accepted."
        if http_status in {"408", "504"} or "timeout" in text or "timed out" in text:
            return "The operation exceeded its allowed processing or network time."
        if "network" in text or "connection" in text or "fetch failed" in text:
            return "A network or service connection failed before the operation could complete."
        if metric == "recording_failed" and ("permission" in text or "microphone" in text):
            return "The browser could not access or continue using the required recording device."
        if metric == "document_processing_failed":
            return "Document ingestion or extraction did not complete; the stored telemetry does not identify a more specific cause."
        if metric == "live_qa_failure":
            return "The Live Assistance request did not complete successfully; the stored telemetry does not identify a more specific cause."
        if metric == "ai_failure":
            return "The AI request failed before a usable response was returned; the stored telemetry does not identify a more specific cause."
        return "The available telemetry does not identify a confirmed cause."

    @staticmethod
    def _incident_status(
        event: dict[str, Any],
        support_reports: list[dict[str, Any]],
    ) -> str:
        explicit = str(event.get("incident_status") or "").strip().casefold()
        if explicit in {"open", "investigating", "resolved", "ignored"}:
            return explicit
        report_statuses = {
            str(item.get("status") or "new").strip().casefold()
            for item in support_reports
        }
        if "resolved" in report_statuses:
            return "resolved"
        if "read" in report_statuses:
            return "investigating"
        return "open"

    def repeated_failure_details(self, minimum_failures: int = 3) -> dict[str, Any]:
        """Return administrator-safe failure history grouped by affected user."""
        try:
            threshold = int(minimum_failures)
        except (TypeError, ValueError):
            threshold = 3
        threshold = max(1, min(threshold, 50))

        users_available = True
        try:
            users = self._load_registered_users()
        except Exception:
            current_app.logger.exception(
                "Could not load registered users for repeated-failure analytics"
            )
            users = []
            users_available = False
        user_lookup: dict[str, dict[str, Any]] = {}
        for user in users:
            user_id = str(user.get("user_id") or user.get("email") or "").strip()
            email = str(user.get("email") or user_id).strip()
            if user_id:
                user_lookup[user_id] = user
            if email:
                user_lookup.setdefault(email, user)

        try:
            events = list(self.analytics_repository.list_usage_events())
            events_available = True
        except Exception:
            current_app.logger.exception("Could not load repeated failure details")
            events = []
            events_available = False

        failures_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            metric = str(event.get("metric") or "")
            user_id = str(event.get("user_id") or "").strip()
            if user_id and metric in _FAILURE_METRICS:
                failures_by_user[user_id].append(event)

        support_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        support_available = self.support_repository is not None
        if self.support_repository is not None:
            try:
                for item in self.support_repository.list_all():
                    if str(item.get("source") or "") != "browser_recorder_error":
                        continue
                    user_id = str(item.get("user_id") or item.get("email") or "").strip()
                    if user_id:
                        support_by_user[user_id].append(item)
            except Exception:
                current_app.logger.exception("Could not load recorder support reports for failure details")
                support_available = False

        rows: list[dict[str, Any]] = []
        for user_id, raw_failures in failures_by_user.items():
            if len(raw_failures) < threshold:
                continue
            user = user_lookup.get(user_id, {})
            email = str(user.get("email") or user_id)
            reports = support_by_user.get(user_id, [])
            if email != user_id:
                reports = [*reports, *support_by_user.get(email, [])]

            serialized_failures = [
                self._serialize_failure_event(item)
                for item in sorted(
                    raw_failures,
                    key=lambda value: str(value.get("occurred_at") or ""),
                    reverse=True,
                )
            ]
            serialized_reports = [
                self._serialize_failure_support_report(item)
                for item in sorted(
                    reports,
                    key=lambda value: str(value.get("created_at") or ""),
                    reverse=True,
                )
            ]
            rows.append({
                "user_id": user_id,
                "email": email,
                "full_name": str(user.get("full_name") or ""),
                "failure_count": len(serialized_failures),
                "latest_failure_at": serialized_failures[0].get("occurred_at") if serialized_failures else "",
                "failures": serialized_failures,
                "support_reports": serialized_reports,
            })

        rows.sort(
            key=lambda item: (
                self._integer(item.get("failure_count")),
                str(item.get("latest_failure_at") or ""),
                str(item.get("email") or "").casefold(),
            ),
            reverse=True,
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "minimum_failures": threshold,
            "affected_user_count": len(rows),
            "total_failure_count": sum(self._integer(item.get("failure_count")) for item in rows),
            "events_available": events_available,
            "support_reports_available": support_available,
            "users_available": users_available,
            "users": rows,
        }

    @staticmethod
    def _serialize_failure_event(item: dict[str, Any]) -> dict[str, Any]:
        metric = str(item.get("metric") or "")
        return {
            "metric": metric,
            "label": _FAILURE_LABELS.get(metric, metric.replace("_", " ").title() or "Failure"),
            "occurred_at": str(item.get("occurred_at") or ""),
            "source": str(item.get("source") or ""),
            "stage": str(item.get("stage") or ""),
            "http_status": str(item.get("http_status") or ""),
            "status_text": str(item.get("status_text") or ""),
            "reference_id": str(item.get("reference_id") or ""),
            "error_summary": str(item.get("error_summary") or ""),
            "reported_cause": str(
                item.get("cause")
                or item.get("probable_cause")
                or item.get("root_cause")
                or ""
            ),
            "feature": str(item.get("feature") or ""),
            "model": str(item.get("model") or ""),
            "duration_ms": AdminAnalyticsService._integer(item.get("duration_ms")),
        }

    @staticmethod
    def _serialize_failure_support_report(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": str(item.get("request_id") or ""),
            "created_at": str(item.get("created_at") or ""),
            "status": str(item.get("status") or "new"),
            "subject": str(item.get("subject") or "Mock Interview recorder error"),
            "message": str(item.get("message") or ""),
            "page_url": str(item.get("page_url") or ""),
        }

    def user_usage(self, user_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id or len(normalized_user_id) > 320:
            raise ValidationError("Invalid user ID.")

        sources = {
            "documents": True,
            "meetings": True,
            "live_qa_answers": True,
            "desktop_downloads": True,
            "desktop_uses": True,
            "recording_durations": True,
        }

        try:
            raw_files = self.knowledge_repository.list_files(normalized_user_id)
            collections = self.knowledge_repository.list_collections(normalized_user_id)
        except Exception:
            current_app.logger.exception(
                "Could not load document usage for admin user detail %s",
                normalized_user_id,
            )
            raw_files = []
            collections = []
            sources["documents"] = False

        collection_names = {
            str(item.get("collection_id") or ""): str(item.get("name") or "")
            for item in collections
        }
        documents = [
            {
                "filename": str(
                    item.get("display_name")
                    or item.get("filename")
                    or "Document"
                ),
                "extension": str(item.get("extension") or ""),
                "collection_name": collection_names.get(
                    str(item.get("collection_id") or ""),
                    "Uncategorized",
                ) or "Uncategorized",
                "size_bytes": self._integer(item.get("size_bytes")),
                "created_at": str(item.get("created_at") or ""),
            }
            for item in raw_files
        ]
        documents.sort(
            key=lambda item: (item["created_at"], item["filename"].casefold()),
            reverse=True,
        )

        meetings: list[dict[str, Any]] = []
        if self.transcript_repository is None:
            sources["meetings"] = False
        else:
            try:
                raw_meetings = self.transcript_repository.list_summaries_for_user(
                    normalized_user_id
                )
                meetings = [self._serialize_meeting(item) for item in raw_meetings]
                meetings.sort(key=lambda item: item["timestamp"], reverse=True)
            except Exception:
                current_app.logger.exception(
                    "Could not load meeting usage for admin user detail %s",
                    normalized_user_id,
                )
                sources["meetings"] = False

        try:
            product_events = self.analytics_repository.list_usage_events(
                user_id=normalized_user_id,
            )
            answer_events = [
                item for item in product_events
                if item.get("metric") == _LIVE_QA_METRIC
            ]
            desktop_download_events = [
                item for item in product_events
                if item.get("metric") == _DESKTOP_DOWNLOAD_METRIC
            ]
            desktop_use_events = [
                item for item in product_events
                if item.get("metric") == _DESKTOP_USE_METRIC
            ]
        except Exception:
            current_app.logger.exception(
                "Could not load durable product usage for admin user detail %s",
                normalized_user_id,
            )
            product_events = []
            answer_events = []
            desktop_download_events = []
            desktop_use_events = []
            sources["live_qa_answers"] = False
            sources["desktop_downloads"] = False
            sources["desktop_uses"] = False
            sources["recording_durations"] = False

        return {
            "user_id": normalized_user_id,
            "summary": {
                "document_count": len(documents),
                "document_total_bytes": sum(
                    self._integer(item.get("size_bytes")) for item in documents
                ),
                "saved_meeting_count": len(meetings),
                "live_qa_answer_count": len(answer_events),
                "desktop_download_count": len(desktop_download_events),
                "desktop_use_count": len(desktop_use_events),
                **self._recording_duration_metrics(product_events),
            },
            "documents": documents,
            "meetings": meetings,
            "sources": sources,
            "live_qa_tracking_note": (
                "Live Assistance lifetime totals are tracked from the deployment of the "
                "admin usage update onward; older expired feed entries cannot be recovered."
            ),
            "desktop_tracking_note": (
                "Desktop counters begin with this update. Downloads are assigned to a "
                "user only when the installer is downloaded while that account is signed "
                "in. A desktop use is counted after a successful desktop-client sign-in."
            ),
        }

    def _load_registered_users(self) -> list[dict[str, Any]]:
        return self.user_repository.list_all()

    def _load_usage_snapshot(self) -> dict[str, Any]:
        documents: dict[str, list[dict[str, Any]]] = defaultdict(list)
        meetings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        live_qa_answers: dict[str, int] = defaultdict(int)
        desktop_downloads: dict[str, int] = defaultdict(int)
        desktop_uses: dict[str, int] = defaultdict(int)
        actions: list[dict[str, Any]] = []
        support_requests: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        sources = {
            "documents": True, "meetings": True, "live_qa_answers": True,
            "desktop_downloads": True, "desktop_uses": True, "actions": True,
            "recording_durations": True, "support": True, "product_events": True,
        }

        try:
            for item in self.knowledge_repository.list_all_files():
                user_id = str(item.get("user_id") or "")
                if user_id:
                    documents[user_id].append(item)
        except Exception:
            current_app.logger.exception("Could not load Document Library usage for Admin Analytics")
            sources["documents"] = False

        if self.transcript_repository is None:
            sources["meetings"] = False
        else:
            try:
                for item in self.transcript_repository.list_all_summaries():
                    user_id = str(item.get("user_id") or "")
                    if user_id:
                        meetings[user_id].append(item)
            except Exception:
                current_app.logger.exception("Could not load saved-meeting usage for Admin Analytics")
                sources["meetings"] = False

        try:
            events = list(self.analytics_repository.list_usage_events())
            for item in events:
                user_id = str(item.get("user_id") or "")
                if not user_id:
                    continue
                metric = str(item.get("metric") or "")
                if metric == _LIVE_QA_METRIC:
                    live_qa_answers[user_id] += 1
                elif metric == _DESKTOP_DOWNLOAD_METRIC:
                    desktop_downloads[user_id] += 1
                elif metric == _DESKTOP_USE_METRIC:
                    desktop_uses[user_id] += 1
        except Exception:
            current_app.logger.exception("Could not load durable product usage for Admin Analytics")
            sources["live_qa_answers"] = False
            sources["desktop_downloads"] = False
            sources["desktop_uses"] = False
            sources["recording_durations"] = False
            sources["product_events"] = False

        if self.action_repository is not None:
            try:
                list_all = getattr(self.action_repository, "list_all", None)
                actions = list(list_all()) if callable(list_all) else []
            except Exception:
                current_app.logger.exception("Could not load Career Action Plan outcomes for Admin Analytics")
                sources["actions"] = False
        else:
            sources["actions"] = False

        if self.support_repository is not None:
            try:
                support_requests = list(self.support_repository.list_all())
            except Exception:
                current_app.logger.exception("Could not load support health for Admin Analytics")
                sources["support"] = False
        else:
            sources["support"] = False

        return {
            "documents": documents, "meetings": meetings,
            "live_qa_answers": live_qa_answers,
            "desktop_downloads": desktop_downloads, "desktop_uses": desktop_uses,
            "actions": actions, "support_requests": support_requests,
            "events": events, "sources": sources,
        }

    def _build_user_rows(
        self,
        users: list[dict[str, Any]],
        all_activity: list[dict[str, Any]],
        period_start: str,
        today: str,
        usage: dict[str, Any],
    ) -> list[dict[str, Any]]:
        activity_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        events_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        actions_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        documents_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        meetings_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        live_qa_answers_by_user: dict[str, int] = defaultdict(int)
        desktop_downloads_by_user: dict[str, int] = defaultdict(int)
        desktop_uses_by_user: dict[str, int] = defaultdict(int)
        for item in all_activity:
            user_key = self._user_key(item.get("user_id"))
            if user_key:
                activity_by_user[user_key].append(item)
        for item in usage.get("events", []):
            user_key = self._user_key(item.get("user_id"))
            if user_key:
                events_by_user[user_key].append(item)
        for item in usage.get("actions", []):
            user_key = self._user_key(item.get("user_id"))
            if user_key:
                actions_by_user[user_key].append(item)
        for user_id, items in usage.get("documents", {}).items():
            documents_by_user[self._user_key(user_id)].extend(items)
        for user_id, items in usage.get("meetings", {}).items():
            meetings_by_user[self._user_key(user_id)].extend(items)
        for user_id, count in usage.get("live_qa_answers", {}).items():
            live_qa_answers_by_user[self._user_key(user_id)] += self._integer(count)
        for user_id, count in usage.get("desktop_downloads", {}).items():
            desktop_downloads_by_user[self._user_key(user_id)] += self._integer(count)
        for user_id, count in usage.get("desktop_uses", {}).items():
            desktop_uses_by_user[self._user_key(user_id)] += self._integer(count)

        today_date = datetime.strptime(today, "%Y-%m-%d").date()
        rows: list[dict[str, Any]] = []
        for user in users:
            user_id = str(user.get("user_id") or user.get("email") or "")
            user_key = self._user_key(user_id)
            records = activity_by_user.get(user_key, [])
            events = events_by_user.get(user_key, [])
            period_records = [
                item for item in records
                if str(item.get("activity_date") or "") >= period_start
            ]
            documents = documents_by_user.get(user_key, [])
            meetings = meetings_by_user.get(user_key, [])
            actions = actions_by_user.get(user_key, [])
            last_active_candidates = [
                self._activity_timestamp(item) for item in records
            ] + [
                self._event_timestamp(item) for item in events
            ] + [
                self._latest_timestamp(item, "updated_at", "created_at")
                for item in documents
            ] + [
                self._latest_timestamp(item, "updated_at", "timestamp", "created_at")
                for item in meetings
            ] + [
                self._latest_timestamp(
                    item,
                    "completed_at",
                    "updated_at",
                    "created_at",
                )
                for item in actions
            ] + [
                self._timestamp_value(user.get("created_at"))
            ]
            last_active = max(last_active_candidates, default=0)
            period_has_activity = self._user_has_activity_in_window(
                user=user,
                records=records,
                events=events,
                documents=documents,
                meetings=meetings,
                actions=actions,
                start_date=period_start,
                end_date=today,
            )
            active_days = sorted({
                str(item.get("activity_date") or "") for item in records
                if item.get("activity_date")
            })
            created = self._parse_datetime(user.get("created_at"))
            returned_7 = self._returned_within(created, active_days, 7)
            returned_30 = self._returned_within(created, active_days, 30)
            review_used = any(
                self._feature_for_activity(item) == "meeting_review" for item in records
            ) or any(
                item.get("metric") == "meeting_review_opened" for item in events
            )
            activated = bool(meetings and review_used)
            done_actions = sum(1 for item in actions if str(item.get("status") or "") == "done")
            overdue_actions = sum(1 for item in actions if self._action_is_overdue(item, today))
            failure_count = sum(
                1 for item in events if str(item.get("metric") or "").endswith("_failed")
                or item.get("metric") in {"live_qa_failure", "ai_failure", "recording_failed"}
            )
            ai_events = [item for item in events if item.get("metric") == "ai_request"]
            ai_cost_summary = self._ai_cost_summary(ai_events)
            recording_duration_metrics = self._recording_duration_metrics(events)
            days_since = None
            if last_active:
                days_since = max(0, (today_date - datetime.fromtimestamp(last_active, timezone.utc).date()).days)

            rows.append({
                "user_id": user_id,
                "email": str(user.get("email") or user_id),
                "full_name": str(user.get("full_name") or ""),
                "created_at": user.get("created_at"),
                "groups": live_assistance.get("groups", []),
                "live_interview_assistance_enabled": bool(live_assistance.get("enabled")),
                "live_interview_assistance_reason": str(live_assistance.get("reason") or ""),
                "live_interview_assistance_override": live_assistance.get("override"),
                "last_active": last_active or None,
                "days_since_last_active": days_since,
                "period_has_activity": period_has_activity,
                "active_day_count": len(active_days),
                "returned_within_7_days": returned_7,
                "returned_within_30_days": returned_30,
                "activated": activated,
                "session_count": len({
                    str(item.get("session_id") or item.get("session_key") or "")
                    for item in records
                }),
                "period_active_seconds": sum(self._integer(item.get("active_seconds")) for item in period_records),
                "today_active_seconds": sum(
                    self._integer(item.get("active_seconds"))
                    for item in records if str(item.get("activity_date") or "") == today
                ),
                "lifetime_active_seconds": sum(self._integer(item.get("active_seconds")) for item in records),
                "document_count": len(documents),
                "document_total_bytes": sum(self._integer(item.get("size_bytes")) for item in documents),
                "saved_meeting_count": len(meetings),
                **recording_duration_metrics,
                "live_qa_answer_count": live_qa_answers_by_user.get(user_key, 0),
                "desktop_download_count": desktop_downloads_by_user.get(user_key, 0),
                "desktop_use_count": desktop_uses_by_user.get(user_key, 0),
                "action_count": len(actions),
                "completed_action_count": done_actions,
                "overdue_action_count": overdue_actions,
                "failure_count": failure_count,
                "ai_request_count": len(ai_events),
                "ai_priced_request_count": ai_cost_summary["priced_requests"],
                "ai_unpriced_request_count": ai_cost_summary["unpriced_requests"],
                "estimated_ai_cost_usd": round(
                    ai_cost_summary["estimated_cost_usd"],
                    6,
                ),
            })

        rows.sort(
            key=lambda row: (
                bool(row.get("period_has_activity")),
                self._integer(row.get("period_active_seconds")),
                self._integer(row.get("last_active")),
                self._integer(row.get("saved_meeting_count")),
                str(row.get("email") or "").lower(),
            ), reverse=True,
        )
        return rows

    def _daily_series(
        self,
        period_activity: list[dict[str, Any]],
        period_events: list[dict[str, Any]],
        usage: dict[str, Any],
        period_start: str,
        days: int,
    ) -> list[dict[str, Any]]:
        guest_ids: dict[str, set[str]] = defaultdict(set)
        registered_ids: dict[str, set[str]] = defaultdict(set)
        active_seconds: dict[str, int] = defaultdict(int)

        for item in period_activity:
            day = str(item.get("activity_date") or "")
            if not day:
                continue
            visitor_id = str(item.get("visitor_id") or "")
            user_id = self._user_key(item.get("user_id"))
            if item.get("identity_type") == "guest" and visitor_id:
                guest_ids[day].add(visitor_id)
            if user_id:
                registered_ids[day].add(user_id)
                active_seconds[day] += self._integer(item.get("active_seconds"))

        for item in period_events:
            day = self._event_date(item)
            user_id = self._user_key(item.get("user_id"))
            if day and user_id:
                registered_ids[day].add(user_id)

        for mapping, fields in (
            (usage.get("documents", {}), ("updated_at", "created_at")),
            (usage.get("meetings", {}), ("updated_at", "timestamp", "created_at")),
        ):
            for raw_user_id, items in mapping.items():
                user_id = self._user_key(raw_user_id)
                for item in items:
                    day = self._date_from_fields(item, *fields)
                    if day >= period_start and user_id:
                        registered_ids[day].add(user_id)

        for item in usage.get("actions", []):
            day = self._date_from_fields(
                item,
                "completed_at",
                "updated_at",
                "created_at",
            )
            user_id = self._user_key(item.get("user_id"))
            if day >= period_start and user_id:
                registered_ids[day].add(user_id)

        start = datetime.strptime(period_start, "%Y-%m-%d").date()
        return [
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                "unique_guests": len(guest_ids[(start + timedelta(days=offset)).isoformat()]),
                "active_registered_users": len(
                    registered_ids[(start + timedelta(days=offset)).isoformat()]
                ),
                "registered_active_seconds": active_seconds[
                    (start + timedelta(days=offset)).isoformat()
                ],
            }
            for offset in range(days)
        ]

    def _guest_geography(
        self,
        period_activity: list[dict[str, Any]],
    ) -> dict[str, Any]:
        guest_ids: set[str] = set()
        latest_known_country: dict[str, tuple[int, str]] = {}

        for item in period_activity:
            if item.get("identity_type") != "guest" or not item.get("visitor_id"):
                continue
            visitor_id = str(item.get("visitor_id"))
            guest_ids.add(visitor_id)
            country_code = str(item.get("country_code") or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", country_code) or country_code in {
                "XX", "ZZ"
            }:
                continue
            observed_at = self._integer(
                item.get("last_seen") or item.get("observed_at")
            )
            previous = latest_known_country.get(visitor_id)
            if previous is None or observed_at >= previous[0]:
                latest_known_country[visitor_id] = (observed_at, country_code)

        counts: dict[str, int] = defaultdict(int)
        for _, country_code in latest_known_country.values():
            counts[country_code] += 1

        located_guests = len(latest_known_country)
        total_guests = len(guest_ids)
        countries = [
            {
                "country_code": country_code,
                "guest_count": count,
                "percentage": round(
                    (count / located_guests * 100) if located_guests else 0,
                    1,
                ),
            }
            for country_code, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        return {
            "tracking_configured": bool(
                str(
                    current_app.config.get("ANALYTICS_GEO_COUNTRY_HEADER") or ""
                ).strip()
            ),
            "total_guests": total_guests,
            "located_guests": located_guests,
            "unknown_guests": max(0, total_guests - located_guests),
            "coverage_percentage": round(
                (located_guests / total_guests * 100) if total_guests else 0,
                1,
            ),
            "countries": countries,
        }

    def _growth_metrics(self, users, period_activity, all_activity, period_start):
        registrations = [u for u in users if self._date_value(u.get("created_at")) >= period_start]
        guest_visitors = {str(i.get("visitor_id")) for i in period_activity if i.get("identity_type") == "guest" and i.get("visitor_id")}
        signup_visitors = {str(i.get("visitor_id")) for i in period_activity if i.get("identity_type") == "guest" and str(i.get("last_page") or "").endswith("/login.html") and i.get("visitor_id")}
        converted_visitors = {str(i.get("visitor_id")) for i in all_activity if i.get("user_id") and i.get("visitor_id")} & guest_visitors
        denominator = len(guest_visitors)
        return {
            "unique_guests": denominator,
            "registration_page_visitors": len(signup_visitors),
            "new_registrations": len(registrations),
            "converted_visitors": len(converted_visitors),
            "conversion_rate": round((len(registrations) / denominator * 100) if denominator else 0, 1),
        }

    def _activation_metrics(self, users, all_activity, usage):
        records_by_user = defaultdict(list)
        for item in all_activity:
            if item.get("user_id"):
                records_by_user[str(item["user_id"])].append(item)
        activated = 0
        within_1 = 0
        within_7 = 0
        activation_hours = []
        for user in users:
            user_id = str(user.get("user_id") or user.get("email") or "")
            meetings = usage["meetings"].get(user_id, [])
            if not meetings:
                continue
            review_used = any(self._feature_for_activity(i) == "meeting_review" for i in records_by_user.get(user_id, [])) or any(
                e.get("user_id") == user_id and e.get("metric") == "meeting_review_opened" for e in usage["events"]
            )
            if not review_used:
                continue
            activated += 1
            created = self._parse_datetime(user.get("created_at"))
            meeting_times = [self._parse_datetime(m.get("timestamp")) for m in meetings]
            meeting_times = [m for m in meeting_times if m]
            if created and meeting_times:
                hours = max(0, (min(meeting_times) - created).total_seconds() / 3600)
                activation_hours.append(hours)
                if hours <= 24: within_1 += 1
                if hours <= 168: within_7 += 1
        total = len(users)
        return {
            "activated_users": activated, "not_activated_users": max(0, total - activated),
            "activation_rate": round((activated / total * 100) if total else 0, 1),
            "activated_within_1_day": within_1, "activated_within_7_days": within_7,
            "average_hours_to_activation": round(sum(activation_hours) / len(activation_hours), 1) if activation_hours else None,
        }

    def _retention_metrics(self, users, all_activity):
        activity = defaultdict(set)
        for item in all_activity:
            if item.get("user_id") and item.get("activity_date"):
                activity[str(item["user_id"])].add(str(item["activity_date"]))
        eligible_1 = eligible_7 = eligible_30 = returned_1 = returned_7 = returned_30 = 0
        today = datetime.now(timezone.utc).date()
        for user in users:
            created = self._parse_datetime(user.get("created_at"))
            if not created: continue
            age = (today - created.date()).days
            days = sorted(activity.get(str(user.get("user_id") or user.get("email") or ""), set()))
            if age >= 1:
                eligible_1 += 1; returned_1 += int(self._returned_within(created, days, 1, exact=True))
            if age >= 7:
                eligible_7 += 1; returned_7 += int(self._returned_within(created, days, 7))
            if age >= 30:
                eligible_30 += 1; returned_30 += int(self._returned_within(created, days, 30))
        rate=lambda n,d: round((n/d*100) if d else 0,1)
        return {
            "returned_next_day": returned_1, "eligible_next_day": eligible_1, "return_next_day_rate": rate(returned_1, eligible_1),
            "returned_within_7_days": returned_7, "eligible_7_days": eligible_7, "return_7_day_rate": rate(returned_7, eligible_7),
            "returned_within_30_days": returned_30, "eligible_30_days": eligible_30, "return_30_day_rate": rate(returned_30, eligible_30),
        }

    def _meeting_funnel(self, events, usage, period_start):
        event_counts = defaultdict(int)
        for item in events: event_counts[str(item.get("metric") or "")] += 1
        saved = sum(1 for meetings in usage["meetings"].values() for m in meetings if self._date_value(m.get("timestamp")) >= period_start)
        actions = sum(1 for a in usage["actions"] if self._date_value(a.get("created_at")) >= period_start)
        stages = [
            ("Mock interview started", event_counts["recording_started"]),
            ("Mock interview completed", event_counts["recording_completed"]),
            ("Recording uploaded", event_counts["recording_uploaded"]),
            ("Interview processing succeeded", event_counts["meeting_processing_succeeded"] or saved),
            ("Mock interview saved", saved),
            ("Interview Review opened", event_counts["meeting_review_opened"]),
            ("Career action created", actions or event_counts["action_created"]),
        ]
        result=[]
        previous=None
        for label,count in stages:
            result.append({"label":label,"count":count,"from_previous_rate": round((count/previous*100) if previous else 0,1) if previous is not None else 100.0})
            previous=count
        return result

    def _feature_adoption(self, users, all_activity, events):
        adopted=defaultdict(set)
        for item in all_activity:
            user_id=str(item.get("user_id") or "")
            feature=self._feature_for_activity(item)
            if user_id and feature: adopted[feature].add(user_id)
        for item in events:
            user_id=str(item.get("user_id") or "")
            metric=str(item.get("metric") or "")
            feature=str(item.get("feature") or "")
            if metric=="feature_used" and feature in _FEATURE_LABELS and user_id: adopted[feature].add(user_id)
            if metric==_DESKTOP_USE_METRIC and user_id: adopted["desktop_client"].add(user_id)
            if metric in {_LIVE_QA_METRIC,"live_qa_request","live_qa_session_started"} and user_id: adopted["live_qa"].add(user_id)
        total=len(users)
        return [{"feature":key,"label":label,"users":len(adopted[key]),"percentage":round((len(adopted[key])/total*100) if total else 0,1)} for key,label in _FEATURE_LABELS.items()]

    def _reliability_metrics(self, events):
        definitions=[
            ("Interview processing", {"meeting_processing_succeeded"}, {"meeting_processing_failed","recording_failed"}),
            ("Document processing", {"document_processing_succeeded"}, {"document_processing_failed"}),
            ("Live Assistance", {_LIVE_QA_METRIC}, {"live_qa_failure"}),
            ("AI requests", {"ai_request"}, {"ai_failure"}),
        ]
        rows=[]; total_success=total_failure=0
        for label,success_names,failure_names in definitions:
            success=sum(1 for e in events if e.get("metric") in success_names and e.get("success",True) is not False)
            failure=sum(1 for e in events if e.get("metric") in failure_names or (e.get("metric") in success_names and e.get("success") is False))
            durations=[self._float(e.get("duration_ms")) for e in events if e.get("metric") in success_names|failure_names and self._float(e.get("duration_ms"))>0]
            total_success+=success; total_failure+=failure
            rows.append({"operation":label,"successes":success,"failures":failure,"success_rate":round((success/(success+failure)*100) if success+failure else 0,1),"average_duration_ms":round(sum(durations)/len(durations)) if durations else None})
        return {"operations":rows,"overall_success_rate":round((total_success/(total_success+total_failure)*100) if total_success+total_failure else 0,1),"failures":total_failure}

    def _document_health(self, usage, events, period_start):
        files=[f for items in usage["documents"].values() for f in items]
        extensions=defaultdict(int)
        for item in files: extensions[str(item.get("extension") or "unknown").lower()] += 1
        return {
            "current_documents":len(files),
            "uploaded_in_period":sum(1 for f in files if self._date_value(f.get("created_at"))>=period_start),
            "processing_successes":sum(1 for e in events if e.get("metric")=="document_processing_succeeded"),
            "processing_failures":sum(1 for e in events if e.get("metric")=="document_processing_failed"),
            "file_types":[{"extension":k,"count":v} for k,v in sorted(extensions.items(),key=lambda x:(-x[1],x[0]))[:8]],
        }

    def _action_outcomes(self, actions, period_start, today):
        created=[a for a in actions if self._date_value(a.get("created_at"))>=period_start]
        done=[a for a in actions if str(a.get("status") or "")=="done"]
        completion_hours=[]
        for item in done:
            c=self._parse_datetime(item.get("created_at")); d=self._parse_datetime(item.get("completed_at") or item.get("updated_at"))
            if c and d and d>=c: completion_hours.append((d-c).total_seconds()/3600)
        return {
            "total_actions":len(actions),"created_in_period":len(created),
            "open_actions":sum(1 for a in actions if str(a.get("status") or "")!="done"),
            "completed_actions":len(done),"overdue_actions":sum(1 for a in actions if self._action_is_overdue(a,today)),
            "completion_rate":round((len(done)/len(actions)*100) if actions else 0,1),
            "average_completion_hours":round(sum(completion_hours)/len(completion_hours),1) if completion_hours else None,
            "completion_time_sample_size":len(completion_hours),
        }

    def _support_health(self, requests, now):
        new=[r for r in requests if str(r.get("status") or "new")=="new"]
        resolved=[r for r in requests if str(r.get("status") or "")=="resolved"]
        response_hours=[]; resolution_hours=[]; categories=defaultdict(int)
        for item in requests:
            created=self._parse_datetime(item.get("created_at")); read=self._parse_datetime(item.get("read_at")); done=self._parse_datetime(item.get("resolved_at"))
            if created and read: response_hours.append(max(0,(read-created).total_seconds()/3600))
            if created and done: resolution_hours.append(max(0,(done-created).total_seconds()/3600))
            categories[str(item.get("topic_label") or item.get("topic") or "Other")]+=1
        stale=sum(1 for item in new if (created:=self._parse_datetime(item.get("created_at"))) and (now-created).total_seconds()>86400)
        return {"total":len(requests),"new":len(new),"resolved":len(resolved),"unread_over_24_hours":stale,"average_first_read_hours":round(sum(response_hours)/len(response_hours),1) if response_hours else None,"average_resolution_hours":round(sum(resolution_hours)/len(resolution_hours),1) if resolution_hours else None,"categories":[{"label":k,"count":v} for k,v in sorted(categories.items(),key=lambda x:-x[1])]}

    def _live_qa_health(self, events):
        answers=sum(1 for e in events if e.get("metric")==_LIVE_QA_METRIC)
        requests=sum(1 for e in events if e.get("metric")=="live_qa_request")
        failures=sum(1 for e in events if e.get("metric")=="live_qa_failure")
        durations=[self._float(e.get("duration_ms")) for e in events if e.get("metric") in {_LIVE_QA_METRIC,"live_qa_request"} and self._float(e.get("duration_ms"))>0]
        return {"sessions":len({e.get("session_key") for e in events if e.get("metric")=="live_qa_session_started"}),"requests":requests,"answers":answers,"failures":failures,"success_rate":round((answers/(answers+failures)*100) if answers+failures else 0,1),"average_response_ms":round(sum(durations)/len(durations)) if durations else None}

    def _ai_usage(self, events):
        ai=[e for e in events if e.get("metric")=="ai_request"]
        cost_summary = self._ai_cost_summary(ai)
        return {
            "requests": len(ai),
            "priced_requests": cost_summary["priced_requests"],
            "unpriced_requests": cost_summary["unpriced_requests"],
            "input_tokens": sum(self._integer(e.get("input_tokens")) for e in ai),
            "cached_input_tokens": sum(
                self._integer(e.get("cached_input_tokens")) for e in ai
            ),
            "output_tokens": sum(self._integer(e.get("output_tokens")) for e in ai),
            "transcription_seconds": round(
                sum(self._float(e.get("audio_seconds")) for e in ai),
                1,
            ),
            "estimated_cost_usd": round(cost_summary["estimated_cost_usd"], 6),
            "failures": sum(1 for e in events if e.get("metric")=="ai_failure"),
        }

    def _ai_cost_summary(self, events):
        estimated_cost = 0.0
        priced_requests = 0
        for event in events:
            event_cost, calculated = self._ai_event_cost(event)
            if calculated:
                estimated_cost += event_cost
                priced_requests += 1
        return {
            "estimated_cost_usd": estimated_cost,
            "priced_requests": priced_requests,
            "unpriced_requests": max(0, len(events) - priced_requests),
        }

    def _ai_event_cost(self, event):
        if event.get("cost_calculated") is True:
            return self._float(event.get("estimated_cost_usd")), True

        stored_cost = self._float(event.get("estimated_cost_usd"))
        if stored_cost > 0:
            return stored_cost, True

        model = str(event.get("model") or "").strip()
        request_type = str(event.get("request_type") or "text").strip().lower()
        if request_type == "transcription" or event.get("audio_seconds") is not None:
            audio_seconds = max(0.0, self._float(event.get("audio_seconds")))
            rate = UsageMetricsService._transcription_pricing(model)
            if rate is not None and audio_seconds > 0:
                return audio_seconds / 60.0 * rate, True
            return 0.0, False

        input_tokens = max(0, self._integer(event.get("input_tokens")))
        output_tokens = max(0, self._integer(event.get("output_tokens")))
        cached_input_tokens = min(
            input_tokens,
            max(0, self._integer(event.get("cached_input_tokens"))),
        )
        if input_tokens <= 0 and output_tokens <= 0:
            return 0.0, False

        pricing = UsageMetricsService._model_pricing(model)
        if pricing:
            uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
            return (
                uncached_input_tokens * pricing["input"]
                + cached_input_tokens * pricing["cached_input"]
                + output_tokens * pricing["output"]
            ) / 1_000_000, True

        input_rate = float(
            current_app.config.get("ANALYTICS_AI_INPUT_COST_PER_MILLION", 0) or 0
        )
        output_rate = float(
            current_app.config.get("ANALYTICS_AI_OUTPUT_COST_PER_MILLION", 0) or 0
        )
        if input_rate > 0 or output_rate > 0:
            return (
                input_tokens * input_rate + output_tokens * output_rate
            ) / 1_000_000, True
        return 0.0, False

    def _alerts(self, *, users, user_rows, reliability, support_health, activation, ai_usage):
        alerts=[]
        if reliability["failures"] and reliability["overall_success_rate"]<95: alerts.append({"severity":"warning","title":"Processing reliability is below 95%","detail":f"Overall measured success rate is {reliability['overall_success_rate']}%."})
        if support_health["unread_over_24_hours"]: alerts.append({"severity":"warning","title":"Unread support messages need attention","detail":f"{support_health['unread_over_24_hours']} message(s) have been unread for more than 24 hours."})
        inactive=sum(1 for r in user_rows if r.get("saved_meeting_count",0)==0 and r.get("created_at"))
        if inactive: alerts.append({"severity":"info","title":"Registered users have not completed a mock interview","detail":f"{inactive} account(s) may need help reaching the mock interview workflow."})
        high_failures=[r for r in user_rows if self._integer(r.get("failure_count"))>=3]
        if high_failures:
            alerts.append({
                "severity": "critical",
                "title": "Users encountered repeated failures",
                "detail": f"{len(high_failures)} user(s) have at least three recorded failures.",
                "action": "view_incidents",
                "action_label": "View incidents",
            })
        if ai_usage["estimated_cost_usd"]>=10: alerts.append({"severity":"info","title":"AI cost threshold reached","detail":f"Estimated AI cost in the selected period is ${ai_usage['estimated_cost_usd']:.2f}."})
        return alerts

    @staticmethod
    def _feature_for_activity(item):
        path=str(item.get("last_page") or item.get("page_path") or "")
        if "knowledge" in path: return "meeting_preparation"
        if "meeting-recorder" in path: return "browser_recorder"
        if "live-qa" in path: return "live_qa"
        if "meeting-review" in path or "transcript" in path or "scorecard" in path: return "meeting_review"
        if "action-center" in path: return "action_center"
        if path.endswith("/analytics.html"): return "analytics"
        return ""

    @staticmethod
    def _parse_datetime(value):
        text=str(value or "").strip()
        if not text: return None
        try:
            parsed=datetime.fromisoformat(text.replace("Z","+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (ValueError,TypeError): return None

    @classmethod
    def _date_value(cls,value):
        parsed=cls._parse_datetime(value)
        return parsed.date().isoformat() if parsed else ""

    @classmethod
    def _event_date(cls,item):
        occurred_date = cls._date_value(item.get("occurred_at"))
        if occurred_date:
            return occurred_date
        analytics_date = str(item.get("analytics_date") or "")[:10]
        if _DATE_RE.fullmatch(analytics_date):
            return analytics_date
        observed_at = cls._timestamp_value(item.get("observed_at"))
        if observed_at:
            return datetime.fromtimestamp(observed_at, timezone.utc).date().isoformat()
        return str(item.get("activity_date") or "")

    @staticmethod
    def _user_key(value: Any) -> str:
        return str(value or "").strip().casefold()

    @classmethod
    def _timestamp_value(cls, value: Any) -> int:
        if value is None or value == "":
            return 0
        try:
            numeric = float(value)
            if numeric > 10_000_000_000:
                numeric /= 1000
            if numeric > 0:
                datetime.fromtimestamp(numeric, timezone.utc)
                return int(numeric)
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        parsed = cls._parse_datetime(value)
        return int(parsed.timestamp()) if parsed else 0

    @classmethod
    def _activity_timestamp(cls, item: dict[str, Any]) -> int:
        return cls._timestamp_value(item.get("last_seen")) or cls._timestamp_value(
            item.get("observed_at")
        )

    @classmethod
    def _event_timestamp(cls, item: dict[str, Any]) -> int:
        return cls._timestamp_value(item.get("occurred_at")) or cls._timestamp_value(
            item.get("observed_at")
        )

    @classmethod
    def _latest_timestamp(cls, item: dict[str, Any], *fields: str) -> int:
        return max((cls._timestamp_value(item.get(field)) for field in fields), default=0)

    @classmethod
    def _date_from_fields(cls, item: dict[str, Any], *fields: str) -> str:
        timestamp = cls._latest_timestamp(item, *fields)
        if not timestamp:
            return ""
        return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()

    @classmethod
    def _user_has_activity_in_window(
        cls,
        *,
        user: dict[str, Any],
        records: list[dict[str, Any]],
        events: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        meetings: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        start_date: str,
        end_date: str,
    ) -> bool:
        dates = [
            str(item.get("activity_date") or "") for item in records
        ] + [
            cls._event_date(item) for item in events
        ] + [
            cls._date_from_fields(item, "updated_at", "created_at")
            for item in documents
        ] + [
            cls._date_from_fields(item, "updated_at", "timestamp", "created_at")
            for item in meetings
        ] + [
            cls._date_from_fields(item, "completed_at", "updated_at", "created_at")
            for item in actions
        ] + [
            cls._date_value(user.get("created_at"))
        ]
        return any(start_date <= date_value <= end_date for date_value in dates if date_value)

    def _active_user_keys_for_window(
        self,
        *,
        users: list[dict[str, Any]],
        all_activity: list[dict[str, Any]],
        usage: dict[str, Any],
        start_date: str,
        end_date: str,
    ) -> set[str]:
        active: set[str] = set()

        for item in all_activity:
            date_value = str(item.get("activity_date") or "")
            user_key = self._user_key(item.get("user_id"))
            if user_key and start_date <= date_value <= end_date:
                active.add(user_key)

        for item in usage.get("events", []):
            date_value = self._event_date(item)
            user_key = self._user_key(item.get("user_id"))
            if user_key and start_date <= date_value <= end_date:
                active.add(user_key)

        for mapping, fields in (
            (usage.get("documents", {}), ("updated_at", "created_at")),
            (usage.get("meetings", {}), ("updated_at", "timestamp", "created_at")),
        ):
            for raw_user_id, items in mapping.items():
                user_key = self._user_key(raw_user_id)
                if not user_key:
                    continue
                if any(
                    start_date <= self._date_from_fields(item, *fields) <= end_date
                    for item in items
                    if self._date_from_fields(item, *fields)
                ):
                    active.add(user_key)

        for item in usage.get("actions", []):
            date_value = self._date_from_fields(
                item,
                "completed_at",
                "updated_at",
                "created_at",
            )
            user_key = self._user_key(item.get("user_id"))
            if user_key and start_date <= date_value <= end_date:
                active.add(user_key)

        for user in users:
            date_value = self._date_value(user.get("created_at"))
            user_key = self._user_key(user.get("user_id") or user.get("email"))
            if user_key and start_date <= date_value <= end_date:
                active.add(user_key)

        return active

    @classmethod
    def _returned_within(cls, created, activity_days, window, exact=False):
        if not created: return False
        for day in activity_days:
            try: delta=(datetime.strptime(day,"%Y-%m-%d").date()-created.date()).days
            except ValueError: continue
            if (delta==window if exact else 1<=delta<=window): return True
        return False

    @classmethod
    def _action_is_overdue(cls,item,today):
        if str(item.get("status") or "")=="done": return False
        due=cls._date_value(item.get("due_date")) or str(item.get("due_date") or "")[:10]
        return bool(due and due < today)

    @staticmethod
    def _float(value):
        try: return float(value or 0)
        except (TypeError,ValueError): return 0.0

    @classmethod
    def _recording_duration_metrics(
        cls,
        events: list[dict[str, Any]],
    ) -> dict[str, int | None]:
        durations: list[float] = []
        for event in events:
            if event.get("metric") != "recording_completed":
                continue
            duration = cls._float(event.get("duration_seconds"))
            if duration > 0 and math.isfinite(duration):
                durations.append(duration)

        if not durations:
            return {
                "recording_duration_sample_count": 0,
                "average_recording_duration_seconds": None,
                "maximum_recording_duration_seconds": None,
                "minimum_recording_duration_seconds": None,
            }

        return {
            "recording_duration_sample_count": len(durations),
            "average_recording_duration_seconds": round(sum(durations) / len(durations)),
            "maximum_recording_duration_seconds": round(max(durations)),
            "minimum_recording_duration_seconds": round(min(durations)),
        }

    @staticmethod
    def _serialize_meeting(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": str(
                item.get("meeting_name")
                or item.get("prepared_meeting_title")
                or "Unnamed Mock Interview"
            ),
            "timestamp": str(item.get("timestamp") or ""),
        }

    @staticmethod
    def _comparison(current: float | int, previous: float | int) -> dict[str, float]:
        current_value = float(current or 0)
        previous_value = float(previous or 0)
        change = current_value - previous_value
        change_percentage = (
            change / previous_value * 100 if previous_value else 0
        )
        return {
            "current": round(current_value, 1),
            "previous": round(previous_value, 1),
            "change": round(change, 1),
            "change_percentage": round(change_percentage, 1),
        }

    @staticmethod
    def _normalize_period(value: str | int | None) -> int:
        try:
            days = int(value or 7)
        except (TypeError, ValueError):
            days = 7
        return days if days in _ALLOWED_PERIODS else 7

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
