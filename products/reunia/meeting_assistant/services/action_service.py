from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
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
_AUTOMATIC_SOURCES = {
    "resume_gap",
    "evidence_review",
    "interview_scorecard",
    "upcoming_interview",
    "application_follow_up",
    "application_next_action",
}
_ALLOWED_SOURCES = {"manual", "meeting", *_AUTOMATIC_SOURCES}
_ACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_ACTIVE_APPLICATION_STATUSES = {
    "draft",
    "considering",
    "preparing",
    "ready_to_apply",
    "applied",
    "screening",
    "interviewing",
    "offered",
}
_SOURCE_LABELS = {
    "manual": "Manual action",
    "resume_gap": "Resume gap",
    "evidence_review": "Evidence review",
    "interview_scorecard": "Interview scorecard",
    "upcoming_interview": "Upcoming interview",
    "application_follow_up": "Application follow-up",
    "application_next_action": "Application next step",
    "meeting": "Legacy interview action",
}


class ActionService:
    """Build and manage the application-linked Career Action Plan.

    Automatic actions are derived at read time from the Application Builder,
    resume workflow, evidence review, mock-interview scorecards, interview dates,
    and follow-up dates. User changes are persisted as overrides using the same
    stable action ID, while deletion of an automatic action creates a tombstone.
    """

    def __init__(
        self,
        repository: ActionRepository | None = None,
        transcript_service: TranscriptService | None = None,
        application_store: Any | None = None,
        workflow_store: Any | None = None,
    ) -> None:
        self.repository = repository or current_app.extensions["action_repository"]
        self.transcript_service = transcript_service or TranscriptService()
        self.application_store = application_store or current_app.extensions.get(
            "career_bridge_application_store"
        )
        self.workflow_store = workflow_store or current_app.extensions.get(
            "career_bridge_workflow_store"
        )

    def list_applications(self, user_id: str) -> list[dict[str, Any]]:
        records = self._application_records(user_id)
        applications = [self._public_application(record) for record in records]
        applications.sort(
            key=lambda item: (
                item.get("upcoming_event_date") or "9999-12-31",
                item.get("company") or "",
                item.get("role") or "",
            )
        )
        return applications

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        try:
            stored_items = self.repository.list_for_user(user_id)
        except (BotoCoreError, ClientError) as exc:
            raise DatabaseError("Failed to retrieve career actions.") from exc

        applications = self._application_records(user_id)
        applications_by_id = {
            str(getattr(application, "id", "") or ""): application
            for application in applications
            if getattr(application, "id", None)
        }
        derived_actions: list[dict[str, Any]] = []
        for application in applications:
            derived_actions.extend(
                self._derive_application_schedule_actions(user_id, application)
            )
            derived_actions.extend(
                self._derive_workflow_actions(user_id, application)
            )

        try:
            interview_reviews = self.transcript_service.list_for_user(user_id)
        except Exception:
            current_app.logger.exception(
                "Could not load interview reviews for the Career Action Plan"
            )
            interview_reviews = []
        derived_actions.extend(
            self._derive_interview_actions(
                user_id,
                interview_reviews,
                applications,
            )
        )
        derived_actions = self._deduplicate_derived_actions(derived_actions)

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

            effective = {**derived, **stored} if stored else derived
            application_id = str(effective.get("application_id") or "")
            application = applications_by_id.get(application_id)
            if application is not None:
                effective.update(self._application_fields(application))
            effective.pop("deleted", None)
            effective.pop("deleted_at", None)
            merged.append(self._public_action(effective))

        # Manual actions remain available. Legacy stored actions are retained so
        # an existing deployment does not silently lose user-created work.
        for action_id, stored in stored_by_id.items():
            if action_id in consumed_ids or stored.get("deleted"):
                continue
            application_id = str(
                stored.get("application_id") or stored.get("meeting_id") or ""
            )
            application = applications_by_id.get(application_id)
            # The Career Action Plan intentionally excludes legacy meeting-only
            # actions because every visible action must belong to a real job
            # application. Existing records remain in storage and are not lost.
            if application is None:
                continue
            stored_source = str(stored.get("source") or "manual")
            if stored_source != "manual" and stored_source in _AUTOMATIC_SOURCES:
                # Keep completed generated actions as closed history, but do not
                # preserve an obsolete open action after its source was resolved
                # or its underlying date/text changed.
                if str(stored.get("status") or "not_started") != "done":
                    continue
            stored.update(self._application_fields(application))
            merged.append(self._public_action(stored))

        merged.sort(key=self._sort_key)
        return merged

    def create(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        application = self._require_application(
            user_id,
            data.get("application_id") or data.get("meeting_id"),
        )
        now = _utc_now()
        requested_id = str(data.get("action_id") or data.get("id") or "").strip()
        action_id = (
            requested_id
            if requested_id.startswith("manual-")
            and _ACTION_ID_PATTERN.fullmatch(requested_id)
            else f"manual-{uuid4()}"
        )
        payload = {**data, **self._application_fields(application)}
        item = self._validated_action(
            user_id=user_id,
            action_id=action_id,
            data=payload,
            existing=None,
            source="manual",
            created_at=now,
        )
        try:
            existing = self.repository.get(user_id, action_id)
            if existing:
                item["created_at"] = str(existing.get("created_at") or item["created_at"])
                self.repository.save(item)
            else:
                self.repository.create(item)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise ValidationError("An action with this ID already exists.") from exc
            raise DatabaseError("Failed to save the career action.") from exc
        except BotoCoreError as exc:
            raise DatabaseError("Failed to save the career action.") from exc
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

        requested_application_id = data.get(
            "application_id",
            current.get("application_id") or current.get("meeting_id"),
        )
        application = self._require_application(user_id, requested_application_id)
        payload = {**data, **self._application_fields(application)}
        item = self._validated_action(
            user_id=user_id,
            action_id=action_id,
            data=payload,
            existing=current,
            source=str(current.get("source") or "manual"),
            created_at=str(current.get("created_at") or _utc_now()),
        )
        item["updated_at"] = _utc_now()
        try:
            self.repository.save(item)
        except (BotoCoreError, ClientError) as exc:
            raise DatabaseError("Failed to update the career action.") from exc
        return self._public_action(item)

    def delete(self, user_id: str, action_id: Any) -> dict[str, Any]:
        action_id = self._validate_action_id(action_id)
        current = self._find_effective_action(user_id, action_id)
        if not current:
            raise ResourceNotFoundError("Action not found.")

        try:
            if str(current.get("source") or "manual") != "manual":
                tombstone = {
                    "user_id": user_id,
                    "action_id": action_id,
                    "source": str(current.get("source") or "automatic"),
                    "application_id": str(current.get("application_id") or ""),
                    "source_reference": str(current.get("source_reference") or ""),
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
            raise DatabaseError("Failed to delete the career action.") from exc
        return {"message": "Action successfully deleted.", "action_id": action_id}

    def _find_effective_action(
        self, user_id: str, action_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.list_for_user(user_id)
                if item["action_id"] == action_id
            ),
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
        priority = str(
            data.get("priority", base.get("priority", "none")) or "none"
        ).strip().lower()
        if priority not in _ALLOWED_PRIORITIES:
            raise ValidationError(
                "priority must be none, low, medium, high, or urgent."
            )
        status = str(
            data.get("status", base.get("status", "not_started"))
            or "not_started"
        ).strip().lower()
        if status not in _ALLOWED_STATUSES:
            raise ValidationError(
                "status must be not_started, in_progress, blocked, or done."
            )

        normalized_source = source if source in _ALLOWED_SOURCES else "manual"
        application_id = _bounded_text(
            data.get(
                "application_id",
                data.get("meeting_id", base.get("application_id", base.get("meeting_id"))),
            ),
            "application_id",
            128,
            required=True,
        )
        due_date = _optional_date(
            data.get("due_date", base.get("due_date")), "due_date"
        )
        completed_at = base.get("completed_at")
        if status == "done":
            completed_at = (
                _optional_datetime(data.get("completed_at", completed_at)) or _utc_now()
            )
        else:
            completed_at = None

        return {
            "user_id": user_id,
            "action_id": action_id,
            "description": description,
            "application_id": application_id,
            "application_company": _bounded_text(
                data.get("application_company", base.get("application_company")),
                "application_company",
                300,
            ),
            "application_role": _bounded_text(
                data.get("application_role", base.get("application_role")),
                "application_role",
                300,
            ),
            "application_label": _bounded_text(
                data.get("application_label", base.get("application_label")),
                "application_label",
                620,
            ),
            "application_status": _bounded_text(
                data.get("application_status", base.get("application_status")),
                "application_status",
                80,
            ),
            "owner": owner,
            "due_date": due_date,
            "priority": priority,
            "status": status,
            "source": normalized_source,
            "source_label": _bounded_text(
                data.get(
                    "source_label",
                    base.get("source_label") or _SOURCE_LABELS.get(normalized_source, "Career action"),
                ),
                "source_label",
                160,
            ),
            "source_detail": _bounded_text(
                data.get("source_detail", base.get("source_detail")),
                "source_detail",
                2000,
            ),
            "source_reference": _bounded_text(
                data.get("source_reference", base.get("source_reference")),
                "source_reference",
                500,
            ),
            "generated": bool(
                data.get(
                    "generated",
                    base.get("generated", normalized_source != "manual"),
                )
            ),
            "link_url": _bounded_text(
                data.get("link_url", base.get("link_url")),
                "link_url",
                1000,
            ) or f"/applications/?tab=tailoring&application_id={application_id}",
            "created_at": _optional_datetime(created_at) or _utc_now(),
            "completed_at": completed_at,
        }

    def _application_records(self, user_id: str) -> list[Any]:
        if self.application_store is None:
            return []
        try:
            return list(self.application_store.list_for_owner(user_id))
        except Exception as exc:
            current_app.logger.exception(
                "Could not load applications for the Career Action Plan"
            )
            raise DatabaseError("Failed to retrieve job applications.") from exc

    def _require_application(self, user_id: str, value: Any) -> Any:
        application_id = str(value or "").strip()
        if not application_id:
            raise ValidationError("Choose a job application for this action.")
        if self.application_store is None:
            raise ValidationError("The Application Builder is unavailable.")
        try:
            application = self.application_store.get(user_id, application_id)
        except Exception as exc:
            raise DatabaseError("Failed to retrieve the selected application.") from exc
        if application is None:
            raise ValidationError("The selected job application no longer exists.")
        return application

    def _derive_application_schedule_actions(
        self, user_id: str, application: Any
    ) -> list[dict[str, Any]]:
        status = str(getattr(application, "status", "") or "").strip().lower()
        if status not in _ACTIVE_APPLICATION_STATUSES:
            return []

        application_id = str(getattr(application, "id", "") or "")
        company = str(getattr(application, "company", "") or "").strip()
        role = str(getattr(application, "role", "") or "").strip()
        target = self._target_label(role, company)
        actions: list[dict[str, Any]] = []

        follow_up_date = _optional_date(
            getattr(application, "next_follow_up_date", ""),
            "next_follow_up_date",
            strict=False,
        )
        if follow_up_date:
            actions.append(
                self._automatic_action(
                    user_id=user_id,
                    application=application,
                    source="application_follow_up",
                    reference=f"next-follow-up:{follow_up_date}",
                    description=f"Follow up with the recruiter about {target}",
                    detail=(
                        f"Generated from the application follow-up date of {follow_up_date}."
                    ),
                    due_date=follow_up_date,
                    priority=self._date_priority(follow_up_date, default="medium"),
                    link_url=f"/applications/?tab=applications&application_id={application_id}",
                )
            )

        event_date = _optional_date(
            getattr(application, "upcoming_event_date", ""),
            "upcoming_event_date",
            strict=False,
        )
        event_type = str(
            getattr(application, "upcoming_event_type", "") or ""
        ).strip().lower()
        if event_date and event_type == "interview":
            if event_date >= date.today().isoformat():
                actions.append(
                    self._automatic_action(
                        user_id=user_id,
                        application=application,
                        source="upcoming_interview",
                        reference=f"interview:{event_date}",
                        description=f"Prepare for the upcoming interview for {target}",
                        detail=(
                            f"Generated from the scheduled interview date of {event_date}. "
                            "Review the role preparation, rehearse evidence-backed answers, and prepare employer questions."
                        ),
                        due_date=event_date,
                        priority=self._date_priority(event_date, default="high"),
                        link_url=f"/applications/interview-preparation?application_id={application_id}",
                    )
                )
            elif (date.today() - date.fromisoformat(event_date)).days <= 14:
                actions.append(
                    self._automatic_action(
                        user_id=user_id,
                        application=application,
                        source="application_follow_up",
                        reference=f"interview-thank-you:{event_date}",
                        description=f"Send a thank-you message after the interview for {target}",
                        detail=(
                            f"Generated because the recorded interview date was {event_date}. "
                            "Thank the interviewer, reinforce role alignment, and mention one relevant discussion point."
                        ),
                        due_date=event_date,
                        priority="urgent",
                        link_url=f"/applications/?tab=applications&application_id={application_id}",
                    )
                )

        next_action = str(getattr(application, "next_action", "") or "").strip()
        if next_action and not self._looks_like_generated_schedule_action(next_action):
            due_date = follow_up_date or event_date
            actions.append(
                self._automatic_action(
                    user_id=user_id,
                    application=application,
                    source="application_next_action",
                    reference=f"next-action:{simple_key(next_action)}",
                    description=next_action,
                    detail="Generated from the Next action saved on this job application.",
                    due_date=due_date,
                    priority=self._date_priority(due_date, default="medium"),
                    link_url=f"/applications/?tab=applications&application_id={application_id}",
                )
            )
        return actions

    def _derive_workflow_actions(
        self, user_id: str, application: Any
    ) -> list[dict[str, Any]]:
        if self.workflow_store is None:
            return []
        application_id = str(getattr(application, "id", "") or "")
        if not application_id:
            return []
        try:
            state = self.workflow_store.get(
                f"{user_id}:application:{application_id}"
            )
        except Exception:
            current_app.logger.exception(
                "Could not load resume workflow for application %s", application_id
            )
            return []

        actions: list[dict[str, Any]] = []
        actions.extend(self._derive_resume_gap_actions(user_id, application, state))
        actions.extend(self._derive_evidence_review_actions(user_id, application, state))
        return actions

    def _derive_resume_gap_actions(
        self, user_id: str, application: Any, state: Any
    ) -> list[dict[str, Any]]:
        analysis = (
            getattr(state, "initial_report_analysis", None)
            or getattr(state, "analysis", None)
        )
        proposal = (
            getattr(state, "initial_report_proposal", None)
            or getattr(state, "initial_evidence_proposal", None)
            or getattr(state, "provisional_proposal", None)
            or getattr(state, "draft_proposal", None)
            or getattr(state, "final_proposal", None)
        )
        profile = getattr(state, "source_profile", None)
        if analysis is None or proposal is None or profile is None:
            return []
        try:
            from resume_tailor.resume_report import build_evidence_gap_report  # type: ignore

            _, rows = build_evidence_gap_report(
                profile,
                analysis,
                proposal,
                list(getattr(state, "candidate_answers", []) or []),
            )
        except Exception:
            current_app.logger.exception(
                "Could not derive resume-gap actions for application %s",
                getattr(application, "id", ""),
            )
            return []

        priority_order = {"critical": 0, "important": 1, "secondary": 2}
        unresolved = [
            row
            for row in rows
            if str(getattr(row, "evidence_status", "") or "")
            in {"partial", "unsupported", "no decision"}
        ]
        unresolved.sort(
            key=lambda row: (
                priority_order.get(str(getattr(row, "priority", "")), 3),
                float(getattr(row, "score", 0.0) or 0.0),
            )
        )
        due_date = self._application_due_date(application)
        actions: list[dict[str, Any]] = []
        for row in unresolved[:6]:
            requirement = str(getattr(row, "requirement", "") or "").strip()
            if not requirement:
                continue
            evidence_status = str(
                getattr(row, "evidence_status", "") or "unsupported"
            )
            if evidence_status == "partial":
                description = f"Add evidence for resume claim: {requirement}"
            else:
                description = f"Address resume gap: {requirement}"
            requirement_priority = str(
                getattr(row, "priority", "") or "optional"
            ).lower()
            action_priority = {
                "critical": "high",
                "important": "medium",
                "secondary": "low",
            }.get(requirement_priority, "medium")
            actions.append(
                self._automatic_action(
                    user_id=user_id,
                    application=application,
                    source="resume_gap",
                    reference=f"requirement:{getattr(row, 'requirement_id', requirement)}",
                    description=description,
                    detail=str(
                        getattr(row, "recommended_action", "")
                        or getattr(row, "rationale", "")
                        or "Confirm or add verified evidence before strengthening this resume claim."
                    ),
                    due_date=due_date,
                    priority=action_priority,
                    link_url=(
                        f"/applications/?tab=tailoring&application_id={getattr(application, 'id', '')}"
                        "#workflow-evidence-export"
                    ),
                )
            )
        return actions

    def _derive_evidence_review_actions(
        self, user_id: str, application: Any, state: Any
    ) -> list[dict[str, Any]]:
        report = (
            getattr(state, "final_report", None)
            or getattr(state, "updated_report", None)
        )
        if report is None:
            return []

        findings: list[tuple[str, str, str, float]] = []
        try:
            sections = list(report.sections())
        except Exception:
            sections = []
        for section in sections:
            section_name = str(getattr(section, "name", "") or "")
            for subsection in list(getattr(section, "subsections", []) or []):
                subsection_name = str(getattr(subsection, "name", "") or "")
                for check in list(getattr(subsection, "checks", []) or []):
                    status = str(getattr(check, "status", "") or "")
                    if status not in {"fail", "warning"}:
                        continue
                    label = str(getattr(check, "label", "") or "").strip()
                    detail = str(getattr(check, "detail", "") or "").strip()
                    evidence_context = " ".join(
                        (section_name, subsection_name, label, detail)
                    ).casefold()
                    if not any(
                        term in evidence_context
                        for term in (
                            "evidence",
                            "unsupported",
                            "unverified",
                            "verification",
                            "claim",
                            "credential",
                            "experience gap",
                        )
                    ):
                        continue
                    score = float(getattr(check, "score", lambda: 0.0)())
                    if label:
                        findings.append(
                            (f"{section_name} / {subsection_name}", label, detail, score)
                        )

        findings.sort(key=lambda item: (item[3], item[0], item[1]))
        due_date = self._application_due_date(application)
        actions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for location, label, detail, score in findings:
            key = re.sub(r"\W+", " ", label.casefold()).strip()
            if key in seen:
                continue
            seen.add(key)
            actions.append(
                self._automatic_action(
                    user_id=user_id,
                    application=application,
                    source="evidence_review",
                    reference=f"finding:{location}:{label}",
                    description=f"Resolve evidence-review finding: {label}",
                    detail=f"{location}. {detail}".strip(),
                    due_date=due_date,
                    priority="high" if score <= 20 else "medium",
                    link_url=(
                        f"/applications/?tab=tailoring&application_id={getattr(application, 'id', '')}"
                        "#workflow-evidence-export"
                    ),
                )
            )
            if len(actions) >= 5:
                break
        return actions

    def _derive_interview_actions(
        self,
        user_id: str,
        reviews: list[dict[str, Any]],
        applications: list[Any],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        sorted_reviews = sorted(
            reviews,
            key=lambda item: str(_unwrap_scalar(item.get("timestamp")) or ""),
            reverse=True,
        )
        review_count_by_application: dict[str, int] = {}

        for review in sorted_reviews:
            if not self._is_interview_review(review):
                continue
            application = self._application_for_review(review, applications)
            if application is None:
                continue
            application_id = str(getattr(application, "id", "") or "")
            review_count_by_application[application_id] = (
                review_count_by_application.get(application_id, 0) + 1
            )
            if review_count_by_application[application_id] > 3:
                continue

            meeting_id = str(_unwrap_scalar(review.get("meeting_id")) or "review")
            timestamp = str(_unwrap_scalar(review.get("timestamp")) or "")
            review_reference = f"{meeting_id}:{timestamp}"
            due_date = self._application_due_date(application)
            answer_reviews = _unwrap_list(review.get("interview_answer_reviews"))
            low_answers = []
            for answer in answer_reviews:
                answer_value = _unwrap_value(answer)
                if not isinstance(answer_value, dict):
                    continue
                score = _bounded_number(answer_value.get("score"), 100.0)
                if score >= 70:
                    continue
                practice = str(
                    _unwrap_scalar(answer_value.get("recommended_practice_action"))
                    or ""
                ).strip()
                question_number = str(
                    _unwrap_scalar(answer_value.get("question_number")) or ""
                ).strip()
                question = str(
                    _unwrap_scalar(answer_value.get("question")) or ""
                ).strip()
                if not practice:
                    answer_label = f" {question_number}" if question_number else ""
                    practice = f"Review weak interview answer{answer_label}"
                low_answers.append((score, question_number, question, practice))
            low_answers.sort(key=lambda item: item[0])
            for score, question_number, question, practice in low_answers[:3]:
                actions.append(
                    self._automatic_action(
                        user_id=user_id,
                        application=application,
                        source="interview_scorecard",
                        reference=f"{review_reference}:answer:{question_number or simple_key(question)}",
                        description=practice,
                        detail=(
                            f"Generated from interview answer {question_number or 'review'}"
                            f" with a score of {round(score)}. {question}"
                        ).strip(),
                        due_date=due_date,
                        priority="high" if score < 55 else "medium",
                        link_url=(
                            f"/interview-review?meeting={meeting_id}"
                        ),
                    )
                )

            overall_score = _bounded_number(
                _unwrap_scalar(review.get("overall_score"))
                or _unwrap_scalar(review.get("final_grade"))
                or _unwrap_scalar(
                    (_unwrap_value(review.get("interview_scorecard")) or {}).get(
                        "overall_score"
                    )
                    if isinstance(_unwrap_value(review.get("interview_scorecard")), dict)
                    else None
                ),
                100.0,
            )
            if overall_score < 70:
                interview_type = self._interview_type_label(review)
                role = str(getattr(application, "role", "") or "the role")
                company = str(getattr(application, "company", "") or "the employer")
                actions.append(
                    self._automatic_action(
                        user_id=user_id,
                        application=application,
                        source="interview_scorecard",
                        reference=f"{review_reference}:repeat",
                        description=(
                            f"Complete another {interview_type.lower()} for {role} at {company}"
                        ),
                        detail=(
                            f"The latest interview scorecard was {round(overall_score)}. "
                            "Repeat the practice after reviewing the weakest answers."
                        ),
                        due_date=due_date,
                        priority="high" if overall_score < 55 else "medium",
                        link_url=f"/mock-interview?application_id={application_id}",
                    )
                )
        return actions

    def _application_for_review(
        self, review: dict[str, Any], applications: list[Any]
    ) -> Any | None:
        explicit_id = str(
            _unwrap_scalar(review.get("career_application_id"))
            or _unwrap_scalar(review.get("application_id"))
            or ""
        ).strip()
        if explicit_id:
            return next(
                (
                    application
                    for application in applications
                    if str(getattr(application, "id", "") or "") == explicit_id
                ),
                None,
            )

        candidate_text = " ".join(
            str(_unwrap_scalar(review.get(field)) or "")
            for field in (
                "prepared_meeting_title",
                "meeting_name",
                "career_application_company",
                "career_application_role",
            )
        ).casefold()
        matches = []
        for application in applications:
            company = str(getattr(application, "company", "") or "").strip()
            role = str(getattr(application, "role", "") or "").strip()
            if company and role and company.casefold() in candidate_text and role.casefold() in candidate_text:
                matches.append(application)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _is_interview_review(review: dict[str, Any]) -> bool:
        scorecard_type = str(
            _unwrap_scalar(review.get("scorecard_type")) or ""
        ).lower()
        topics = [
            str(_unwrap_scalar(item) or "").lower()
            for item in _unwrap_list(review.get("topics"))
        ]
        return bool(
            scorecard_type == "interview"
            or _unwrap_value(review.get("interview_scorecard"))
            or any("mock interview" in topic for topic in topics)
        )

    @staticmethod
    def _interview_type_label(review: dict[str, Any]) -> str:
        topics = [
            str(_unwrap_scalar(item) or "").strip()
            for item in _unwrap_list(review.get("topics"))
        ]
        for topic in topics:
            normalized = topic.strip()
            if not normalized or normalized.casefold() == "mock interview":
                continue
            if normalized.casefold().endswith(" interview"):
                normalized = normalized[: -len(" interview")].strip()
            return f"{normalized} mock interview" if normalized else "mock interview"
        return "mock interview"

    def _automatic_action(
        self,
        *,
        user_id: str,
        application: Any,
        source: str,
        reference: str,
        description: str,
        detail: str,
        due_date: str,
        priority: str,
        link_url: str,
    ) -> dict[str, Any]:
        application_id = str(getattr(application, "id", "") or "")
        stable_reference = f"{application_id}|{source}|{reference}"
        return {
            "user_id": user_id,
            "action_id": f"auto-{source}-{_js_hash_base36(stable_reference)}",
            "description": description[:1000],
            **self._application_fields(application),
            "owner": "Me",
            "due_date": _optional_date(due_date, "due_date", strict=False),
            "priority": priority if priority in _ALLOWED_PRIORITIES else "medium",
            "status": "not_started",
            "source": source,
            "source_label": _SOURCE_LABELS[source],
            "source_detail": detail[:2000],
            "source_reference": reference[:500],
            "generated": True,
            "link_url": link_url[:1000],
            "created_at": str(getattr(application, "updated_at", "") or _utc_now()),
            "completed_at": None,
        }

    @staticmethod
    def _application_fields(application: Any) -> dict[str, Any]:
        application_id = str(getattr(application, "id", "") or "")
        company = str(getattr(application, "company", "") or "").strip()
        role = str(getattr(application, "role", "") or "").strip()
        label = ActionService._target_label(role, company)
        return {
            "application_id": application_id,
            "application_company": company,
            "application_role": role,
            "application_label": label,
            "application_status": str(
                getattr(application, "status", "") or ""
            ).strip(),
        }

    @staticmethod
    def _public_application(application: Any) -> dict[str, Any]:
        fields = ActionService._application_fields(application)
        application_id = fields["application_id"]
        return {
            "id": application_id,
            **fields,
            "company": fields["application_company"],
            "role": fields["application_role"],
            "label": fields["application_label"],
            "status": fields["application_status"],
            "status_label": str(
                getattr(application, "status_label", "")
                or fields["application_status"].replace("_", " ").title()
            ),
            "next_action": str(getattr(application, "next_action", "") or ""),
            "next_follow_up_date": str(
                getattr(application, "next_follow_up_date", "") or ""
            ),
            "upcoming_event_date": str(
                getattr(application, "upcoming_event_date", "") or ""
            ),
            "upcoming_event_type": str(
                getattr(application, "upcoming_event_type", "") or ""
            ),
            "builder_url": f"/applications/?tab=tailoring&application_id={application_id}",
            "interview_preparation_url": (
                f"/applications/interview-preparation?application_id={application_id}"
            ),
            "mock_interview_url": f"/mock-interview?application_id={application_id}",
        }

    @staticmethod
    def _target_label(role: str, company: str) -> str:
        role = role.strip() or "Role not specified"
        company = company.strip() or "Company not specified"
        return f"{role} at {company}"

    @staticmethod
    def _application_due_date(application: Any) -> str:
        event_date = _optional_date(
            getattr(application, "upcoming_event_date", ""),
            "upcoming_event_date",
            strict=False,
        )
        if event_date and str(
            getattr(application, "upcoming_event_type", "") or ""
        ).lower() == "interview":
            return event_date
        return _optional_date(
            getattr(application, "next_follow_up_date", ""),
            "next_follow_up_date",
            strict=False,
        )

    @staticmethod
    def _date_priority(value: str, *, default: str) -> str:
        normalized = _optional_date(value, "date", strict=False)
        if not normalized:
            return default
        difference = (date.fromisoformat(normalized) - date.today()).days
        if difference < 0:
            return "urgent"
        if difference <= 2:
            return "high"
        if difference <= 7:
            return "medium" if default != "high" else "high"
        return default

    @staticmethod
    def _looks_like_generated_schedule_action(value: str) -> bool:
        lowered = value.casefold()
        return lowered.startswith("follow up") or lowered.startswith("prepare for the upcoming")

    @staticmethod
    def _deduplicate_derived_actions(
        actions: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        description_keys: set[tuple[str, str]] = set()
        for action in actions:
            action_id = str(action.get("action_id") or "")
            application_id = str(action.get("application_id") or "")
            description_key = re.sub(
                r"\W+", " ", str(action.get("description") or "").casefold()
            ).strip()
            duplicate_key = (application_id, description_key)
            if not action_id or not description_key or duplicate_key in description_keys:
                continue
            description_keys.add(duplicate_key)
            by_id[action_id] = action
        return list(by_id.values())

    @staticmethod
    def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        status = str(item.get("status") or "not_started")
        due_date = str(item.get("due_date") or "")
        overdue = bool(
            due_date
            and status != "done"
            and _optional_date(due_date, "due_date", strict=False)
            and due_date < date.today().isoformat()
        )
        status_weight = {
            "blocked": 0,
            "in_progress": 1,
            "not_started": 2,
            "done": 3,
        }.get(status, 2)
        priority_weight = {
            "urgent": 0,
            "high": 1,
            "medium": 2,
            "low": 3,
            "none": 4,
        }.get(str(item.get("priority") or "none"), 4)
        return (
            0 if overdue else 1,
            status_weight,
            due_date or "9999-12-31",
            priority_weight,
            str(item.get("application_label") or ""),
            str(item.get("description") or ""),
        )

    @staticmethod
    def _public_action(item: dict[str, Any]) -> dict[str, Any]:
        result = to_json_compatible(item)
        result.pop("user_id", None)
        result.pop("deleted", None)
        result.pop("deleted_at", None)
        result["id"] = result.get("action_id")
        result.setdefault(
            "source_label",
            _SOURCE_LABELS.get(str(result.get("source") or "manual"), "Career action"),
        )
        # Backward-compatible aliases for any older frontend integration.
        result["meeting_id"] = result.get("application_id")
        result["meeting_name"] = result.get("application_label")
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_number(value: Any, default: float) -> float:
    try:
        return max(0.0, min(100.0, float(_unwrap_scalar(value))))
    except (TypeError, ValueError):
        return default


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


def _js_hash_base36(value: str) -> str:
    """Create the same stable FNV-1a identifier used by the browser client."""
    hash_value = 2166136261
    encoded = str(value or "").encode("utf-16-le", "surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        hash_value ^= code_unit
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return _base36(hash_value)


def simple_key(value: str) -> str:
    return _js_hash_base36(value)[:12]


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = digits[remainder] + result
    return result
