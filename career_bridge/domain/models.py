from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any, Mapping

from career_bridge.domain.enums import (
    ActionPriority,
    ActionStatus,
    ApplicationStatus,
    DocumentKind,
    EvidenceType,
    EvidenceVerificationStatus,
    ImprovementArea,
    InterviewKind,
    PreparationStatus,
    ProcessingStatus,
    ScoreKind,
    SupportStatus,
)

Metadata = Mapping[str, Any]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _optional(value: str) -> str:
    return str(value or "").strip()


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_required(value, "value") for value in values))


def _validate_date_range(start_date: date | None, end_date: date | None) -> None:
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date cannot be before start_date")


def _change_time(value: datetime | None, current: datetime) -> datetime:
    changed_at = _utc(value or utc_now(), "changed_at")
    if changed_at < current:
        raise ValueError("changed_at cannot be before updated_at")
    return changed_at


@dataclass(frozen=True, slots=True)
class AuthSession:
    session_id: str
    user_id: str
    issued_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "user_id", _required(self.user_id, "user_id"))
        object.__setattr__(self, "issued_at", _utc(self.issued_at, "issued_at"))
        if self.expires_at is not None:
            expires_at = _utc(self.expires_at, "expires_at")
            if expires_at <= self.issued_at:
                raise ValueError("expires_at must be after issued_at")
            object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class UserProfile:
    """Account-level preferences owned by the authentication/user module."""

    id: str
    email: str
    display_name: str = ""
    locale: str = "en"
    timezone_name: str = "UTC"
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        email = _required(self.email, "email").lower()
        if "@" not in email:
            raise ValueError("email must contain @")
        object.__setattr__(self, "email", email)
        object.__setattr__(self, "display_name", _optional(self.display_name))
        object.__setattr__(self, "locale", _required(self.locale, "locale"))
        object.__setattr__(
            self,
            "timezone_name",
            _required(self.timezone_name, "timezone_name"),
        )
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    """Career-facing identity that can be reused across many job applications."""

    id: str
    user_id: str
    full_name: str
    headline: str = ""
    location: str = ""
    email: str = ""
    phone: str = ""
    preferred_roles: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "user_id", "full_name"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "headline", _optional(self.headline))
        object.__setattr__(self, "location", _optional(self.location))
        email = _optional(self.email).lower()
        if email and "@" not in email:
            raise ValueError("email must contain @")
        object.__setattr__(self, "email", email)
        object.__setattr__(self, "phone", _optional(self.phone))
        object.__setattr__(self, "preferred_roles", _unique(self.preferred_roles))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")


@dataclass(frozen=True, slots=True)
class CareerExperience:
    id: str
    employer: str
    title: str
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    location: str = ""
    summary: str = ""
    evidence_item_ids: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "employer", "title"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        _validate_date_range(self.start_date, self.end_date)
        if self.is_current and self.end_date is not None:
            raise ValueError("a current experience cannot have an end_date")
        object.__setattr__(self, "location", _optional(self.location))
        object.__setattr__(self, "summary", _optional(self.summary))
        object.__setattr__(self, "evidence_item_ids", _unique(self.evidence_item_ids))


@dataclass(frozen=True, slots=True)
class EducationRecord:
    id: str
    institution: str
    credential: str
    field_of_study: str = ""
    start_date: date | None = None
    end_date: date | None = None
    evidence_item_ids: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "institution", "credential"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        _validate_date_range(self.start_date, self.end_date)
        object.__setattr__(self, "field_of_study", _optional(self.field_of_study))
        object.__setattr__(self, "evidence_item_ids", _unique(self.evidence_item_ids))


@dataclass(frozen=True, slots=True)
class CareerBackground:
    id: str
    candidate_profile_id: str
    professional_summary: str = ""
    experiences: tuple[CareerExperience, ...] = ()
    education: tuple[EducationRecord, ...] = ()
    skills: tuple[str, ...] = ()
    certification_names: tuple[str, ...] = ()
    source_resume_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(
            self,
            "candidate_profile_id",
            _required(self.candidate_profile_id, "candidate_profile_id"),
        )
        object.__setattr__(self, "professional_summary", _optional(self.professional_summary))
        object.__setattr__(self, "skills", _unique(self.skills))
        object.__setattr__(self, "certification_names", _unique(self.certification_names))
        object.__setattr__(self, "source_resume_ids", _unique(self.source_resume_ids))
        if len({item.id for item in self.experiences}) != len(self.experiences):
            raise ValueError("experience ids must be unique")
        if len({item.id for item in self.education}) != len(self.education):
            raise ValueError("education ids must be unique")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")


@dataclass(frozen=True, slots=True)
class CareerDocument:
    id: str
    user_id: str
    kind: DocumentKind
    filename: str
    storage_key: str
    content_type: str = "application/octet-stream"
    sha256: str = ""
    source_component: str = ""
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "user_id", "filename", "storage_key", "content_type"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "source_component", _optional(self.source_component))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.sha256 and (
            len(self.sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in self.sha256)
        ):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")


@dataclass(frozen=True, slots=True)
class Resume:
    """A reusable source resume owned by a candidate, before job tailoring."""

    id: str
    candidate_profile_id: str
    career_background_id: str
    document_id: str
    name: str = "Primary resume"
    is_primary: bool = False
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "id",
            "candidate_profile_id",
            "career_background_id",
            "document_id",
            "name",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")


@dataclass(frozen=True, slots=True)
class TargetJobDescription:
    id: str
    application_id: str
    role_title: str
    company_name: str
    document_id: str = ""
    source_url: str = ""
    location: str = ""
    employment_type: str = ""
    raw_text: str = ""
    captured_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "application_id", "role_title", "company_name"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if not _optional(self.document_id) and not _optional(self.raw_text):
            raise ValueError("target job description requires document_id or raw_text")
        for name in ("document_id", "source_url", "location", "employment_type", "raw_text"):
            object.__setattr__(self, name, _optional(getattr(self, name)))
        object.__setattr__(self, "captured_at", _utc(self.captured_at, "captured_at"))


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    id: str
    candidate_profile_id: str
    statement: str
    evidence_type: EvidenceType = EvidenceType.OTHER
    verification_status: EvidenceVerificationStatus = (
        EvidenceVerificationStatus.UNVERIFIED
    )
    source_document_ids: tuple[str, ...] = ()
    source_experience_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "candidate_profile_id", "statement"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "source_document_ids", _unique(self.source_document_ids))
        object.__setattr__(self, "source_experience_ids", _unique(self.source_experience_ids))
        object.__setattr__(self, "tags", _unique(self.tags))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")


@dataclass(frozen=True, slots=True)
class EvidenceLibrary:
    id: str
    candidate_profile_id: str
    evidence_item_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(
            self,
            "candidate_profile_id",
            _required(self.candidate_profile_id, "candidate_profile_id"),
        )
        object.__setattr__(self, "evidence_item_ids", _unique(self.evidence_item_ids))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")

    def with_item(self, evidence_item_id: str, *, changed_at: datetime | None = None) -> "EvidenceLibrary":
        item_id = _required(evidence_item_id, "evidence_item_id")
        return replace(
            self,
            evidence_item_ids=_unique((*self.evidence_item_ids, item_id)),
            updated_at=_change_time(changed_at, self.updated_at),
        )


@dataclass(frozen=True, slots=True)
class Score:
    id: str
    application_id: str
    kind: ScoreKind
    value: float
    rationale: str = ""
    confidence: float | None = None
    source_refs: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(
            self,
            "application_id",
            _required(self.application_id, "application_id"),
        )
        if not 0.0 <= float(self.value) <= 100.0:
            raise ValueError("value must be between 0 and 100")
        object.__setattr__(self, "value", round(float(self.value), 2))
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "rationale", _optional(self.rationale))
        object.__setattr__(self, "source_refs", _unique(self.source_refs))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class TailoredResumeVersion:
    id: str
    application_id: str
    version: int
    source_resume_id: str
    generated_document_id: str = ""
    status: ProcessingStatus = ProcessingStatus.PENDING
    evidence_item_ids: tuple[str, ...] = ()
    score_ids: tuple[str, ...] = ()
    change_summary: str = ""
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "application_id", "source_resume_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.version < 1:
            raise ValueError("version must be at least 1")
        object.__setattr__(self, "generated_document_id", _optional(self.generated_document_id))
        object.__setattr__(self, "evidence_item_ids", _unique(self.evidence_item_ids))
        object.__setattr__(self, "score_ids", _unique(self.score_ids))
        object.__setattr__(self, "change_summary", _optional(self.change_summary))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class InterviewQuestion:
    id: str
    preparation_id: str
    prompt: str
    category: str = "general"
    suggested_evidence_item_ids: tuple[str, ...] = ()
    notes: str = ""
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "preparation_id", "prompt", "category"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(
            self,
            "suggested_evidence_item_ids",
            _unique(self.suggested_evidence_item_ids),
        )
        object.__setattr__(self, "notes", _optional(self.notes))


@dataclass(frozen=True, slots=True)
class InterviewPreparation:
    id: str
    application_id: str
    status: PreparationStatus = PreparationStatus.NOT_STARTED
    focus_areas: tuple[str, ...] = ()
    question_ids: tuple[str, ...] = ()
    selected_evidence_item_ids: tuple[str, ...] = ()
    notes_document_id: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "application_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "focus_areas", _unique(self.focus_areas))
        object.__setattr__(self, "question_ids", _unique(self.question_ids))
        object.__setattr__(
            self,
            "selected_evidence_item_ids",
            _unique(self.selected_evidence_item_ids),
        )
        object.__setattr__(self, "notes_document_id", _optional(self.notes_document_id))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str = ""
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.start_seconds < 0 or self.end_seconds < self.start_seconds:
            raise ValueError("transcript segment timing is invalid")
        object.__setattr__(self, "text", _required(self.text, "text"))
        object.__setattr__(self, "speaker", _optional(self.speaker))
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Transcript:
    id: str
    session_id: str
    language: str
    text: str
    segments: tuple[TranscriptSegment, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "session_id", "language", "text"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class Scorecard:
    id: str
    mock_interview_session_id: str
    overall_score: float
    score_ids: tuple[str, ...] = ()
    strengths: tuple[str, ...] = ()
    improvements: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(
            self,
            "mock_interview_session_id",
            _required(self.mock_interview_session_id, "mock_interview_session_id"),
        )
        if not 0.0 <= float(self.overall_score) <= 100.0:
            raise ValueError("overall_score must be between 0 and 100")
        object.__setattr__(self, "overall_score", round(float(self.overall_score), 2))
        object.__setattr__(self, "score_ids", _unique(self.score_ids))
        object.__setattr__(self, "strengths", _unique(self.strengths))
        object.__setattr__(self, "improvements", _unique(self.improvements))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class MockInterviewSession:
    id: str
    application_id: str
    interview_preparation_id: str
    kind: InterviewKind = InterviewKind.MOCK
    status: ProcessingStatus = ProcessingStatus.PENDING
    scheduled_at: datetime | None = None
    recording_document_ids: tuple[str, ...] = ()
    transcript_id: str = ""
    scorecard_id: str = ""
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "application_id", "interview_preparation_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(
            self,
            "recording_document_ids",
            _unique(self.recording_document_ids),
        )
        object.__setattr__(self, "transcript_id", _optional(self.transcript_id))
        object.__setattr__(self, "scorecard_id", _optional(self.scorecard_id))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.scheduled_at is not None:
            object.__setattr__(self, "scheduled_at", _utc(self.scheduled_at, "scheduled_at"))


@dataclass(frozen=True, slots=True)
class ImprovementAction:
    id: str
    application_id: str
    title: str
    owner_user_id: str
    area: ImprovementArea = ImprovementArea.OTHER
    priority: ActionPriority = ActionPriority.MEDIUM
    status: ActionStatus = ActionStatus.OPEN
    description: str = ""
    due_at: datetime | None = None
    source_ref: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "application_id", "title", "owner_user_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "description", _optional(self.description))
        object.__setattr__(self, "source_ref", _optional(self.source_ref))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")
        if self.due_at is not None:
            object.__setattr__(self, "due_at", _utc(self.due_at, "due_at"))


@dataclass(frozen=True, slots=True)
class ApplicationStatusChange:
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    changed_at: datetime = field(default_factory=utc_now)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.from_status == self.to_status:
            raise ValueError("status history must represent a change")
        object.__setattr__(self, "changed_at", _utc(self.changed_at, "changed_at"))
        object.__setattr__(self, "reason", _optional(self.reason))


_ALLOWED_STATUS_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: frozenset(
        {
            ApplicationStatus.CONSIDERING,
            ApplicationStatus.PREPARING,
            ApplicationStatus.ARCHIVED,
        }
    ),
    ApplicationStatus.CONSIDERING: frozenset(
        {
            ApplicationStatus.PREPARING,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.ARCHIVED,
        }
    ),
    ApplicationStatus.PREPARING: frozenset(
        {
            ApplicationStatus.CONSIDERING,
            ApplicationStatus.READY_TO_APPLY,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.ARCHIVED,
        }
    ),
    ApplicationStatus.READY_TO_APPLY: frozenset(
        {
            ApplicationStatus.PREPARING,
            ApplicationStatus.APPLIED,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.ARCHIVED,
        }
    ),
    ApplicationStatus.APPLIED: frozenset(
        {
            ApplicationStatus.SCREENING,
            ApplicationStatus.INTERVIEWING,
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    ApplicationStatus.SCREENING: frozenset(
        {
            ApplicationStatus.INTERVIEWING,
            ApplicationStatus.OFFERED,
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    ApplicationStatus.INTERVIEWING: frozenset(
        {
            ApplicationStatus.SCREENING,
            ApplicationStatus.OFFERED,
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    ApplicationStatus.OFFERED: frozenset(
        {
            ApplicationStatus.ACCEPTED,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.REJECTED,
        }
    ),
    ApplicationStatus.ACCEPTED: frozenset({ApplicationStatus.ARCHIVED}),
    ApplicationStatus.REJECTED: frozenset({ApplicationStatus.ARCHIVED}),
    ApplicationStatus.WITHDRAWN: frozenset({ApplicationStatus.ARCHIVED}),
    ApplicationStatus.ARCHIVED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class JobApplication:
    """Aggregate root connecting the full Career Bridge workflow for one job."""

    id: str
    user_id: str
    candidate_profile_id: str
    career_background_id: str
    resume_id: str
    target_job_description_id: str
    evidence_library_id: str
    status: ApplicationStatus = ApplicationStatus.DRAFT
    tailored_resume_version_ids: tuple[str, ...] = ()
    current_tailored_resume_version_id: str = ""
    interview_preparation_id: str = ""
    mock_interview_session_ids: tuple[str, ...] = ()
    improvement_action_ids: tuple[str, ...] = ()
    status_history: tuple[ApplicationStatusChange, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    tags: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "id",
            "user_id",
            "candidate_profile_id",
            "career_background_id",
            "resume_id",
            "target_job_description_id",
            "evidence_library_id",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(
            self,
            "tailored_resume_version_ids",
            _unique(self.tailored_resume_version_ids),
        )
        object.__setattr__(
            self,
            "mock_interview_session_ids",
            _unique(self.mock_interview_session_ids),
        )
        object.__setattr__(
            self,
            "improvement_action_ids",
            _unique(self.improvement_action_ids),
        )
        object.__setattr__(self, "tags", _unique(self.tags))
        object.__setattr__(
            self,
            "current_tailored_resume_version_id",
            _optional(self.current_tailored_resume_version_id),
        )
        object.__setattr__(
            self,
            "interview_preparation_id",
            _optional(self.interview_preparation_id),
        )
        if (
            self.current_tailored_resume_version_id
            and self.current_tailored_resume_version_id
            not in self.tailored_resume_version_ids
        ):
            raise ValueError(
                "current_tailored_resume_version_id must be included in "
                "tailored_resume_version_ids"
            )
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")
        if self.status_history:
            previous_status = self.status_history[0].from_status
            previous_time = self.created_at
            for change in self.status_history:
                if change.from_status != previous_status:
                    raise ValueError("status history must be contiguous")
                if change.changed_at < previous_time:
                    raise ValueError("status history must be chronological")
                previous_status = change.to_status
                previous_time = change.changed_at
            if self.status_history[-1].to_status != self.status:
                raise ValueError("latest status history entry must match status")
            if previous_time > self.updated_at:
                raise ValueError("status history cannot be after updated_at")
        if self.mock_interview_session_ids and not self.interview_preparation_id:
            raise ValueError(
                "mock interview sessions require an interview_preparation_id"
            )

    def transition_to(
        self,
        status: ApplicationStatus,
        *,
        changed_at: datetime | None = None,
        reason: str = "",
    ) -> "JobApplication":
        if status == self.status:
            return self
        if status not in _ALLOWED_STATUS_TRANSITIONS[self.status]:
            raise ValueError(
                f"invalid application transition: {self.status.value} -> {status.value}"
            )
        when = _change_time(changed_at, self.updated_at)
        change = ApplicationStatusChange(
            from_status=self.status,
            to_status=status,
            changed_at=when,
            reason=reason,
        )
        return replace(
            self,
            status=status,
            status_history=(*self.status_history, change),
            updated_at=when,
        )

    def with_tailored_resume_version(
        self,
        version_id: str,
        *,
        make_current: bool = True,
        changed_at: datetime | None = None,
    ) -> "JobApplication":
        normalized_id = _required(version_id, "version_id")
        ids = _unique((*self.tailored_resume_version_ids, normalized_id))
        return replace(
            self,
            tailored_resume_version_ids=ids,
            current_tailored_resume_version_id=(
                normalized_id if make_current else self.current_tailored_resume_version_id
            ),
            updated_at=_change_time(changed_at, self.updated_at),
        )

    def with_interview_preparation(
        self,
        preparation_id: str,
        *,
        changed_at: datetime | None = None,
    ) -> "JobApplication":
        normalized_id = _required(preparation_id, "preparation_id")
        if (
            self.interview_preparation_id
            and normalized_id != self.interview_preparation_id
            and self.mock_interview_session_ids
        ):
            raise ValueError(
                "cannot replace interview preparation after mock sessions exist"
            )
        return replace(
            self,
            interview_preparation_id=normalized_id,
            updated_at=_change_time(changed_at, self.updated_at),
        )

    def with_mock_interview_session(
        self,
        session_id: str,
        *,
        changed_at: datetime | None = None,
    ) -> "JobApplication":
        if not self.interview_preparation_id:
            raise ValueError(
                "interview preparation must exist before adding a mock session"
            )
        normalized_id = _required(session_id, "session_id")
        return replace(
            self,
            mock_interview_session_ids=_unique(
                (*self.mock_interview_session_ids, normalized_id)
            ),
            updated_at=_change_time(changed_at, self.updated_at),
        )

    def with_improvement_action(
        self,
        action_id: str,
        *,
        changed_at: datetime | None = None,
    ) -> "JobApplication":
        normalized_id = _required(action_id, "action_id")
        return replace(
            self,
            improvement_action_ids=_unique(
                (*self.improvement_action_ids, normalized_id)
            ),
            updated_at=_change_time(changed_at, self.updated_at),
        )


@dataclass(frozen=True, slots=True)
class JobApplicationBundle:
    """Hydrated aggregate used at application boundaries and in persistence tests."""

    application: JobApplication
    candidate_profile: CandidateProfile
    career_background: CareerBackground
    resume: Resume
    target_job_description: TargetJobDescription
    evidence_library: EvidenceLibrary
    evidence_items: tuple[EvidenceItem, ...] = ()
    tailored_resume_versions: tuple[TailoredResumeVersion, ...] = ()
    interview_preparation: InterviewPreparation | None = None
    interview_questions: tuple[InterviewQuestion, ...] = ()
    mock_interview_sessions: tuple[MockInterviewSession, ...] = ()
    improvement_actions: tuple[ImprovementAction, ...] = ()

    def __post_init__(self) -> None:
        app = self.application
        if self.candidate_profile.id != app.candidate_profile_id:
            raise ValueError("candidate profile does not belong to application")
        if self.candidate_profile.user_id != app.user_id:
            raise ValueError("candidate profile user does not match application user")
        if self.career_background.id != app.career_background_id:
            raise ValueError("career background does not belong to application")
        if self.career_background.candidate_profile_id != self.candidate_profile.id:
            raise ValueError("career background does not belong to candidate profile")
        if self.resume.id != app.resume_id:
            raise ValueError("resume does not belong to application")
        if self.resume.candidate_profile_id != self.candidate_profile.id:
            raise ValueError("resume does not belong to candidate profile")
        if self.resume.career_background_id != self.career_background.id:
            raise ValueError("resume does not use the application's career background")
        if self.resume.id not in self.career_background.source_resume_ids:
            raise ValueError("career background does not reference the source resume")
        if self.target_job_description.id != app.target_job_description_id:
            raise ValueError("target job description does not belong to application")
        if self.target_job_description.application_id != app.id:
            raise ValueError("target job description application_id mismatch")
        if self.evidence_library.id != app.evidence_library_id:
            raise ValueError("evidence library does not belong to application")
        if self.evidence_library.candidate_profile_id != self.candidate_profile.id:
            raise ValueError("evidence library does not belong to candidate profile")

        evidence_ids = {item.id for item in self.evidence_items}
        if len(evidence_ids) != len(self.evidence_items):
            raise ValueError("evidence item ids must be unique")
        if evidence_ids != set(self.evidence_library.evidence_item_ids):
            raise ValueError("evidence items must exactly hydrate the evidence library")
        if any(
            item.candidate_profile_id != self.candidate_profile.id
            for item in self.evidence_items
        ):
            raise ValueError("evidence item does not belong to candidate profile")

        version_ids = {item.id for item in self.tailored_resume_versions}
        if len(version_ids) != len(self.tailored_resume_versions):
            raise ValueError("tailored resume version ids must be unique")
        if version_ids != set(app.tailored_resume_version_ids):
            raise ValueError("tailored resume versions must match application references")
        if any(item.application_id != app.id for item in self.tailored_resume_versions):
            raise ValueError("tailored resume version application_id mismatch")
        if any(item.source_resume_id != self.resume.id for item in self.tailored_resume_versions):
            raise ValueError("tailored resume version source_resume_id mismatch")
        if any(
            not set(item.evidence_item_ids).issubset(evidence_ids)
            for item in self.tailored_resume_versions
        ):
            raise ValueError("tailored resume references evidence outside the library")

        if app.interview_preparation_id:
            if self.interview_preparation is None:
                raise ValueError("application interview preparation was not hydrated")
            if self.interview_preparation.id != app.interview_preparation_id:
                raise ValueError("interview preparation id mismatch")
            if self.interview_preparation.application_id != app.id:
                raise ValueError("interview preparation application_id mismatch")
            if not set(
                self.interview_preparation.selected_evidence_item_ids
            ).issubset(evidence_ids):
                raise ValueError(
                    "interview preparation references evidence outside the library"
                )
        elif self.interview_preparation is not None:
            raise ValueError("unreferenced interview preparation was provided")

        question_ids = {item.id for item in self.interview_questions}
        if len(question_ids) != len(self.interview_questions):
            raise ValueError("interview question ids must be unique")
        expected_question_ids = (
            set(self.interview_preparation.question_ids)
            if self.interview_preparation is not None
            else set()
        )
        if question_ids != expected_question_ids:
            raise ValueError("interview questions must match preparation references")
        if self.interview_preparation is not None and any(
            item.preparation_id != self.interview_preparation.id
            for item in self.interview_questions
        ):
            raise ValueError("interview question preparation_id mismatch")
        if any(
            not set(item.suggested_evidence_item_ids).issubset(evidence_ids)
            for item in self.interview_questions
        ):
            raise ValueError("interview question references evidence outside the library")

        session_ids = {item.id for item in self.mock_interview_sessions}
        if len(session_ids) != len(self.mock_interview_sessions):
            raise ValueError("mock interview session ids must be unique")
        if session_ids != set(app.mock_interview_session_ids):
            raise ValueError("mock interview sessions must match application references")
        if any(item.application_id != app.id for item in self.mock_interview_sessions):
            raise ValueError("mock interview session application_id mismatch")
        if self.interview_preparation is not None and any(
            item.interview_preparation_id != self.interview_preparation.id
            for item in self.mock_interview_sessions
        ):
            raise ValueError("mock interview preparation reference mismatch")

        action_ids = {item.id for item in self.improvement_actions}
        if len(action_ids) != len(self.improvement_actions):
            raise ValueError("improvement action ids must be unique")
        if action_ids != set(app.improvement_action_ids):
            raise ValueError("improvement actions must match application references")
        if any(item.application_id != app.id for item in self.improvement_actions):
            raise ValueError("improvement action application_id mismatch")


@dataclass(frozen=True, slots=True)
class SupportCase:
    id: str
    user_id: str
    category: str
    summary: str
    application_id: str = ""
    status: SupportStatus = SupportStatus.OPEN
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "user_id", "category", "summary"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "application_id", _optional(self.application_id))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")
