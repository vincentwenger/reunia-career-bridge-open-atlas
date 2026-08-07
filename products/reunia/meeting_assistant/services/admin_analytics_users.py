from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module

class AdminAnalyticsUserMixin:
    """User usage snapshots and user-row construction."""

    def user_usage(self, user_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id or len(normalized_user_id) > 320:
            raise ValidationError("Invalid user ID.")

        sources = {
            "documents": True,
            "meetings": True,
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
            desktop_download_events = []
            desktop_use_events = []
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
                "desktop_download_count": len(desktop_download_events),
                "desktop_use_count": len(desktop_use_events),
                **self._recording_duration_metrics(product_events),
            },
            "documents": documents,
            "meetings": meetings,
            "sources": sources,
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
        desktop_downloads: dict[str, int] = defaultdict(int)
        desktop_uses: dict[str, int] = defaultdict(int)
        actions: list[dict[str, Any]] = []
        support_requests: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        sources = {
            "documents": True, "meetings": True,
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
                if metric == _DESKTOP_DOWNLOAD_METRIC:
                    desktop_downloads[user_id] += 1
                elif metric == _DESKTOP_USE_METRIC:
                    desktop_uses[user_id] += 1
        except Exception:
            current_app.logger.exception("Could not load durable product usage for Admin Analytics")
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
                or item.get("metric") in {"ai_failure", "recording_failed"}
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
                "groups": list(user.get("groups") or user.get("access_groups") or ()),
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


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
