from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module

class AdminAnalyticsMetricsMixin:
    """Analytics metric aggregation and alert construction."""

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
            feature=_canonical_feature(item.get("feature"))
            if metric=="feature_used" and feature and user_id: adopted[feature].add(user_id)
            if metric==_DESKTOP_USE_METRIC and user_id: adopted["desktop_client"].add(user_id)
        total=len(users)
        return [{"feature":key,"label":label,"users":len(adopted[key]),"percentage":round((len(adopted[key])/total*100) if total else 0,1)} for key,label in _FEATURE_LABELS.items()]

    def _reliability_metrics(self, events):
        definitions=[
            ("Interview processing", {"meeting_processing_succeeded"}, {"meeting_processing_failed","recording_failed"}),
            ("Document processing", {"document_processing_succeeded"}, {"document_processing_failed"}),
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


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
