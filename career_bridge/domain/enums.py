from __future__ import annotations

from enum import Enum


class JourneyStage(str, Enum):
    DISCOVERY = "discovery"
    APPLICATION = "application"
    INTERVIEW_PREP = "interview_prep"
    INTERVIEW = "interview"
    FOLLOW_UP = "follow_up"
    CLOSED = "closed"


class DocumentKind(str, Enum):
    RESUME_SOURCE = "resume_source"
    RESUME_GENERATED = "resume_generated"
    JOB_DESCRIPTION = "job_description"
    COVER_LETTER = "cover_letter"
    MEETING_MATERIAL = "meeting_material"
    INTERVIEW_AUDIO = "interview_audio"
    TRANSCRIPT = "transcript"
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


class ScoreKind(str, Enum):
    JOB_FIT = "job_fit"
    RESUME_ALIGNMENT = "resume_alignment"
    RESUME_QUALITY = "resume_quality"
    INTERVIEW_READINESS = "interview_readiness"
    COMMUNICATION = "communication"
    OVERALL = "overall"


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
