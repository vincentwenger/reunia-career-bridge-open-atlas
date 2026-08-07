from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module

class AdminAnalyticsIncidentMixin:
    """Dashboard orchestration and incident/repeated-failure drill-downs."""

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
                    if str(item.get("source") or "") not in {
                        "browser_recorder_error",
                        "automatic_server_error",
                    }:
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
        if metric == "ai_failure":
            return "AI Assistance"
        if metric == "server_error":
            return "Server request"
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
        if metric == "ai_failure":
            return "The AI request failed before a usable response was returned; the stored telemetry does not identify a more specific cause."
        if metric == "server_error":
            return "A server-side exception or explicit 5xx response interrupted the request. Review the sanitized technical details and related Support inbox report."
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
                    if str(item.get("source") or "") not in {
                        "browser_recorder_error",
                        "automatic_server_error",
                    }:
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
            "exception_type": str(item.get("exception_type") or ""),
            "request_method": str(item.get("request_method") or ""),
            "request_path": str(item.get("request_path") or ""),
            "endpoint": str(item.get("endpoint") or ""),
            "blueprint": str(item.get("blueprint") or ""),
            "technical_details": str(item.get("technical_details") or ""),
            "support_request_id": str(item.get("support_request_id") or ""),
        }

    @staticmethod
    def _serialize_failure_support_report(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": str(item.get("request_id") or ""),
            "created_at": str(item.get("created_at") or ""),
            "status": str(item.get("status") or "new"),
            "subject": str(item.get("subject") or "Automated technical error report"),
            "message": str(item.get("message") or ""),
            "page_url": str(item.get("page_url") or ""),
        }


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
