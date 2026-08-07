from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module

class AdminAnalyticsHelperMixin:
    """Date, activity, serialization, and numeric helper methods."""

    @staticmethod
    def _feature_for_activity(item):
        explicit_feature = _canonical_feature(
            item.get("last_feature") or item.get("feature")
        )
        if explicit_feature:
            return explicit_feature

        # Historical records created before explicit page identifiers are retained.
        path = str(item.get("last_page") or item.get("page_path") or "").lower()
        historical_paths = (
            (("/career-evidence-library", "/knowledge"), "career_evidence_library"),
            (("/application-materials",), "application_materials"),
            (("/career-profile",), "career_profile"),
            (("/applications/career-translation",), "baseline_resume"),
            (("/applications/job-discovery",), "job_discovery"),
            (("/applications/interview-preparation", "/interview-preparation"), "interview_preparation"),
            (("/mock-interview", "/meeting-recorder"), "mock_interview"),
            (("/interview-review", "/meeting-review"), "interview_review"),
            (("/career-action-plan", "/action-center"), "career_action_plan"),
            (("/progress", "/analytics.html"), "progress"),
            (("/admin/analytics",), "admin_analytics"),
            (("/help-support",), "help_support"),
        )
        for path_fragments, feature in historical_paths:
            if any(fragment in path for fragment in path_fragments):
                return feature
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


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
