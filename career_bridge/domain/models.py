from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from career_bridge.domain.enums import (
    ActionStatus,
    DocumentKind,
    InterviewKind,
    JourneyStage,
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


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


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
        object.__setattr__(self, "locale", _required(self.locale, "locale"))
        object.__setattr__(self, "timezone_name", _required(self.timezone_name, "timezone_name"))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


_ALLOWED_STAGE_TRANSITIONS: dict[JourneyStage, frozenset[JourneyStage]] = {
    JourneyStage.DISCOVERY: frozenset({JourneyStage.APPLICATION, JourneyStage.CLOSED}),
    JourneyStage.APPLICATION: frozenset(
        {JourneyStage.INTERVIEW_PREP, JourneyStage.INTERVIEW, JourneyStage.CLOSED}
    ),
    JourneyStage.INTERVIEW_PREP: frozenset(
        {JourneyStage.APPLICATION, JourneyStage.INTERVIEW, JourneyStage.CLOSED}
    ),
    JourneyStage.INTERVIEW: frozenset({JourneyStage.FOLLOW_UP, JourneyStage.CLOSED}),
    JourneyStage.FOLLOW_UP: frozenset({JourneyStage.INTERVIEW, JourneyStage.CLOSED}),
    JourneyStage.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class CareerJourney:
    id: str
    user_id: str
    target_role: str
    company: str = ""
    stage: JourneyStage = JourneyStage.DISCOVERY
    job_description_document_id: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    tags: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(self, "user_id", _required(self.user_id, "user_id"))
        object.__setattr__(self, "target_role", _required(self.target_role, "target_role"))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")

    def advance_to(self, stage: JourneyStage, *, changed_at: datetime | None = None) -> "CareerJourney":
        if stage == self.stage:
            return self
        if stage not in _ALLOWED_STAGE_TRANSITIONS[self.stage]:
            raise ValueError(f"invalid journey transition: {self.stage.value} -> {stage.value}")
        return replace(self, stage=stage, updated_at=_utc(changed_at or utc_now(), "changed_at"))


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
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.sha256 and (len(self.sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in self.sha256)):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    id: str
    user_id: str
    statement: str
    source_document_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    verified: bool = True
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(self, "user_id", _required(self.user_id, "user_id"))
        object.__setattr__(self, "statement", _required(self.statement, "statement"))


@dataclass(frozen=True, slots=True)
class Score:
    id: str
    journey_id: str
    kind: ScoreKind
    value: float
    rationale: str = ""
    confidence: float | None = None
    source_refs: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(self, "journey_id", _required(self.journey_id, "journey_id"))
        if not 0.0 <= float(self.value) <= 100.0:
            raise ValueError("value must be between 0 and 100")
        object.__setattr__(self, "value", round(float(self.value), 2))
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class ResumeArtifact:
    id: str
    journey_id: str
    version: int
    source_document_id: str
    generated_document_id: str = ""
    status: ProcessingStatus = ProcessingStatus.PENDING
    evidence_ids: tuple[str, ...] = ()
    score_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(self, "journey_id", _required(self.journey_id, "journey_id"))
        object.__setattr__(self, "source_document_id", _required(self.source_document_id, "source_document_id"))
        if self.version < 1:
            raise ValueError("version must be at least 1")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


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
    session_id: str
    overall_score: float
    score_ids: tuple[str, ...] = ()
    strengths: tuple[str, ...] = ()
    improvements: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        if not 0.0 <= float(self.overall_score) <= 100.0:
            raise ValueError("overall_score must be between 0 and 100")
        object.__setattr__(self, "overall_score", round(float(self.overall_score), 2))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class InterviewSession:
    id: str
    journey_id: str
    kind: InterviewKind
    status: ProcessingStatus = ProcessingStatus.PENDING
    scheduled_at: datetime | None = None
    recording_document_ids: tuple[str, ...] = ()
    transcript_id: str = ""
    scorecard_id: str = ""
    created_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "id"))
        object.__setattr__(self, "journey_id", _required(self.journey_id, "journey_id"))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.scheduled_at is not None:
            object.__setattr__(self, "scheduled_at", _utc(self.scheduled_at, "scheduled_at"))


@dataclass(frozen=True, slots=True)
class CareerAction:
    id: str
    journey_id: str
    title: str
    owner_user_id: str
    status: ActionStatus = ActionStatus.OPEN
    due_at: datetime | None = None
    source_ref: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "journey_id", "title", "owner_user_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.due_at is not None:
            object.__setattr__(self, "due_at", _utc(self.due_at, "due_at"))


@dataclass(frozen=True, slots=True)
class SupportCase:
    id: str
    user_id: str
    category: str
    summary: str
    status: SupportStatus = SupportStatus.OPEN
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "user_id", "category", "summary"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
