from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Iterable
from urllib.parse import urlparse

APPLICATION_STATUS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("draft", "Draft"),
    ("considering", "Considering"),
    ("preparing", "Preparing"),
    ("ready_to_apply", "Ready to apply"),
    ("applied", "Applied"),
    ("screening", "Screening"),
    ("interviewing", "Interviewing"),
    ("offered", "Offered"),
    ("accepted", "Accepted"),
    ("rejected", "Rejected"),
    ("withdrawn", "Withdrawn"),
    ("archived", "Archived"),
)
_APPLICATION_STATUS_VALUES = {value for value, _ in APPLICATION_STATUS_OPTIONS}
_APPLICATION_STATUS_ALIASES = {
    "planned": "draft",
    "interview": "interviewing",
    "offer": "offered",
}

RESUME_VERSION_OPTIONS: tuple[str, ...] = (
    "Not started",
    "Initial Resume",
    "Tailored Resume",
    "Final Resume",
    "External resume",
)

UPCOMING_EVENT_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "No upcoming event"),
    ("application_deadline", "Application deadline"),
    ("interview", "Interview"),
    ("follow_up", "Follow-up"),
)

INTERVIEW_AUDIENCE_SUGGESTIONS: tuple[str, ...] = (
    "Recruiter or talent acquisition",
    "Hiring manager",
    "Technical interviewer",
    "Interview panel",
    "Future teammates or peers",
    "Executive or senior leadership",
    "Mixed or not yet known",
)

APPLICATION_BUILDER_STEP_LABELS: dict[str, str] = {
    "setup": "Career and Job Setup",
    "confirmation": "Confirm Relevant Experience",
    "review": "Review Tailored Resume",
    "quality": "Improve Resume Quality",
    "finalize": "Finalize Resume",
    "evidence_export": "Evidence Review and Export",
}

APPLICATION_BUILDER_NEXT_ACTIONS: dict[str, str] = {
    "setup": "Complete Career and Job Setup",
    "confirmation": "Confirm relevant experience",
    "review": "Review the tailored resume",
    "quality": "Improve resume quality",
    "finalize": "Finalize the resume",
    "evidence_export": "Review evidence and export the resume",
}


@dataclass(frozen=True)
class ApplicationRecord:
    id: str
    owner_id: str
    company: str
    role: str
    job_url: str
    interview_audience: str
    application_date: str
    status: str
    resume_version: str
    resume_style: str
    alignment_score: float | None
    overall_score: float | None
    interview_readiness: float | None
    screening_received: bool
    interview_received: bool
    offer_received: bool
    notes: str
    next_action: str
    next_follow_up_date: str
    upcoming_event_date: str
    upcoming_event_type: str
    job_description: str
    workflow_step: str
    created_at: str
    updated_at: str
    resume_filename: str
    resume_bytes: bytes | None
    resume_fingerprint: str
    resume_docx_key: str = ""
    resume_pdf_key: str = ""
    resume_pdf_filename: str = ""
    original_resume_key: str = ""
    source_job_id: str = ""

    @property
    def status_label(self) -> str:
        return dict(APPLICATION_STATUS_OPTIONS).get(
            normalize_application_status(self.status), self.status.replace("_", " ").title()
        )

    @property
    def has_resume_snapshot(self) -> bool:
        return bool(self.resume_bytes or self.resume_docx_key)

    @property
    def interview_readiness_label(self) -> str:
        if self.interview_readiness is None:
            return "Not assessed"
        return f"{self.interview_readiness:.0f}%"

    @property
    def next_action_label(self) -> str:
        if self.next_action.strip():
            return self.next_action.strip()
        status_defaults = {
            "ready_to_apply": "Submit the application",
            "applied": "Track employer response",
            "screening": "Prepare for the screening conversation",
            "interviewing": "Practice for the next interview",
            "offered": "Review the offer",
            "accepted": "Complete onboarding steps",
            "rejected": "Capture lessons and archive when ready",
            "withdrawn": "Archive the application",
            "archived": "No action required",
        }
        status_action = status_defaults.get(normalize_application_status(self.status))
        if status_action:
            return status_action
        return APPLICATION_BUILDER_NEXT_ACTIONS.get(
            self.workflow_step, "Continue this application"
        )

    @property
    def workflow_step_label(self) -> str:
        return APPLICATION_BUILDER_STEP_LABELS.get(
            self.workflow_step, "Career and Job Setup"
        )

    @property
    def upcoming_event_label(self) -> str:
        if not self.upcoming_event_date:
            return "None scheduled"
        event_label = (
            dict(UPCOMING_EVENT_TYPE_OPTIONS).get(self.upcoming_event_type)
            if self.upcoming_event_type
            else None
        ) or "Upcoming milestone"
        return f"{event_label} · {self.upcoming_event_date}"


@dataclass(frozen=True)
class ResumeFindingsRecord:
    application_id: str
    owner_id: str
    snapshot_json: str
    fingerprint: str
    created_at: str
    updated_at: str

    def payload(self) -> dict[str, object]:
        try:
            value = json.loads(self.snapshot_json)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class InterviewPreparationRecord:
    application_id: str
    owner_id: str
    content_json: str
    job_description_fingerprint: str
    evidence_fingerprint: str
    evidence_source_label: str
    evidence_snapshot_json: str
    resume_findings_fingerprint: str
    resume_findings_snapshot_json: str
    model_name: str
    created_at: str
    updated_at: str

    def payload(self) -> dict[str, object]:
        try:
            value = json.loads(self.content_json)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ApplicationMetrics:
    tracked: int
    submitted: int
    screening_count: int
    interview_count: int
    offer_count: int
    screening_rate: float
    interview_rate: float
    offer_rate: float
    average_interview_alignment: float | None
    follow_ups_due: int
    ready_for_interview: int


def normalize_application_status(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    normalized = _APPLICATION_STATUS_ALIASES.get(normalized, normalized)
    return normalized if normalized in _APPLICATION_STATUS_VALUES else "draft"


def normalize_application_builder_step(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    return normalized if normalized in APPLICATION_BUILDER_STEP_LABELS else "setup"


def normalize_iso_date(value: str | None, *, default_today: bool = False) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return date.today().isoformat() if default_today else ""
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return date.today().isoformat() if default_today else ""


def normalize_interview_audience(value: str | None) -> str:
    return " ".join(str(value or "").split())[:200]


def normalize_job_url(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate


def normalize_optional_score(value: float | int | str | None) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return round(max(0.0, min(100.0, float(value))), 1)
    except (TypeError, ValueError):
        return None


def infer_outcomes(status: str) -> tuple[bool, bool, bool]:
    normalized = normalize_application_status(status)
    if normalized in {"offered", "accepted"}:
        return True, True, True
    if normalized == "interviewing":
        return True, True, False
    if normalized == "screening":
        return True, False, False
    return False, False, False


def build_application_metrics(records: Iterable[ApplicationRecord]) -> ApplicationMetrics:
    items = list(records)
    submitted_items = [
        item
        for item in items
        if normalize_application_status(item.status)
        not in {"draft", "considering", "preparing", "ready_to_apply", "archived"}
    ]
    denominator = len(submitted_items)
    screening_count = sum(item.screening_received for item in submitted_items)
    interview_count = sum(item.interview_received for item in submitted_items)
    offer_count = sum(item.offer_received for item in submitted_items)
    interviewed_scores = [
        item.alignment_score
        for item in submitted_items
        if item.interview_received and item.alignment_score is not None
    ]
    today = date.today().isoformat()
    follow_ups_due = sum(
        bool(item.next_follow_up_date)
        and item.next_follow_up_date <= today
        and normalize_application_status(item.status)
        not in {"rejected", "withdrawn", "archived", "accepted"}
        for item in items
    )
    ready_for_interview = sum(
        item.interview_readiness is not None and item.interview_readiness >= 70
        for item in items
    )

    def rate(count: int) -> float:
        return round((count / denominator) * 100, 1) if denominator else 0.0

    return ApplicationMetrics(
        tracked=len(items),
        submitted=denominator,
        screening_count=screening_count,
        interview_count=interview_count,
        offer_count=offer_count,
        screening_rate=rate(screening_count),
        interview_rate=rate(interview_count),
        offer_rate=rate(offer_count),
        average_interview_alignment=(
            round(sum(interviewed_scores) / len(interviewed_scores), 1)
            if interviewed_scores
            else None
        ),
        follow_ups_due=follow_ups_due,
        ready_for_interview=ready_for_interview,
    )
