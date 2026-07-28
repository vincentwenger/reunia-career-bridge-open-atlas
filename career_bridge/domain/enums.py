from __future__ import annotations

from enum import Enum


class ApplicationStatus(str, Enum):
    """Business lifecycle of one candidate's application to one target job."""

    DRAFT = "draft"
    CONSIDERING = "considering"
    PREPARING = "preparing"
    READY_TO_APPLY = "ready_to_apply"
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEWING = "interviewing"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


class DocumentKind(str, Enum):
    RESUME_SOURCE = "resume_source"
    RESUME_TAILORED = "resume_tailored"
    JOB_DESCRIPTION = "job_description"
    COVER_LETTER = "cover_letter"
    CAREER_EVIDENCE = "career_evidence"
    INTERVIEW_PREPARATION = "interview_preparation"
    INTERVIEW_AUDIO = "interview_audio"
    INTERVIEW_TRANSCRIPT = "interview_transcript"
    INTERVIEW_NOTES = "interview_notes"
    OTHER = "other"


class InterviewKind(str, Enum):
    MOCK = "mock"
    REAL = "real"
    COACHING = "coaching"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class PreparationStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    COMPLETED = "completed"


class EvidenceType(str, Enum):
    ACHIEVEMENT = "achievement"
    RESPONSIBILITY = "responsibility"
    SKILL = "skill"
    PROJECT = "project"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    OTHER = "other"


class EvidenceVerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    CANDIDATE_CONFIRMED = "candidate_confirmed"
    DOCUMENT_VERIFIED = "document_verified"
    REJECTED = "rejected"


class ScoreKind(str, Enum):
    JOB_FIT = "job_fit"
    RESUME_ALIGNMENT = "resume_alignment"
    RESUME_QUALITY = "resume_quality"
    INTERVIEW_READINESS = "interview_readiness"
    COMMUNICATION = "communication"
    CONTENT = "content"
    DELIVERY = "delivery"
    OVERALL = "overall"


class ImprovementArea(str, Enum):
    PROFILE = "profile"
    CAREER_BACKGROUND = "career_background"
    EVIDENCE = "evidence"
    RESUME = "resume"
    INTERVIEW_PREPARATION = "interview_preparation"
    INTERVIEW_DELIVERY = "interview_delivery"
    APPLICATION_FOLLOW_UP = "application_follow_up"
    OTHER = "other"


class ActionPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class SupportStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"
