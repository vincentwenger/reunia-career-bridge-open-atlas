"""Shared Application Builder workflow and dashboard projections.

The imported Resume Taylor product remains the delivery adapter. These definitions
keep its six-step workflow and multi-application dashboard aligned with the shared
``JobApplication`` aggregate without making the core depend on Flask or SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

from career_bridge.domain.enums import ApplicationStatus
from career_bridge.domain.models import JobApplication


@dataclass(frozen=True, slots=True)
class ApplicationBuilderStep:
    key: str
    order: int
    label: str
    aggregate_relationships: tuple[str, ...]
    description: str


APPLICATION_BUILDER_STEPS: tuple[ApplicationBuilderStep, ...] = (
    ApplicationBuilderStep(
        key="setup",
        order=1,
        label="Career and Job Setup",
        aggregate_relationships=(
            "candidate_profile_id",
            "career_background_id",
            "resume_id",
            "target_job_description_id",
        ),
        description="Select the candidate background and source resume, then capture the target company, role, and job description.",
    ),
    ApplicationBuilderStep(
        key="confirmation",
        order=2,
        label="Confirm Relevant Experience",
        aggregate_relationships=("evidence_library_id",),
        description="Confirm which verified career evidence may be used for this application.",
    ),
    ApplicationBuilderStep(
        key="review",
        order=3,
        label="Review Tailored Resume",
        aggregate_relationships=("tailored_resume_version_ids",),
        description="Review the first tailored resume version and every evidence-backed change.",
    ),
    ApplicationBuilderStep(
        key="quality",
        order=4,
        label="Improve Resume Quality",
        aggregate_relationships=("tailored_resume_version_ids",),
        description="Apply score-protected quality improvements without inventing experience.",
    ),
    ApplicationBuilderStep(
        key="finalize",
        order=5,
        label="Finalize Resume",
        aggregate_relationships=("current_tailored_resume_version_id",),
        description="Choose the final format and visual presentation for the current tailored version.",
    ),
    ApplicationBuilderStep(
        key="evidence_export",
        order=6,
        label="Evidence Review and Export",
        aggregate_relationships=(
            "current_tailored_resume_version_id",
            "evidence_library_id",
        ),
        description="Run the final evidence review and export the approved resume.",
    ),
)


@dataclass(frozen=True, slots=True)
class ApplicationDashboardItem:
    """Read model shown above the Application Builder workflow."""

    application_id: str
    company: str
    job_title: str
    status: ApplicationStatus
    resume_version: str
    interview_readiness: float | None
    next_action: str
    upcoming_at: datetime | None = None
    upcoming_kind: str = ""

    def __post_init__(self) -> None:
        for name in ("application_id", "company", "job_title", "resume_version"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if not isinstance(self.status, ApplicationStatus):
            try:
                object.__setattr__(self, "status", ApplicationStatus(str(self.status)))
            except ValueError as exc:
                raise ValueError("status must be a valid ApplicationStatus") from exc
        if self.interview_readiness is not None and not 0 <= float(self.interview_readiness) <= 100:
            raise ValueError("interview_readiness must be between 0 and 100")
        if self.upcoming_at is not None and (
            self.upcoming_at.tzinfo is None or self.upcoming_at.utcoffset() is None
        ):
            raise ValueError("upcoming_at must be timezone-aware")
        if self.upcoming_at is not None and not self.upcoming_kind.strip():
            raise ValueError("upcoming_kind is required when upcoming_at is set")


def application_builder_steps() -> tuple[ApplicationBuilderStep, ...]:
    return APPLICATION_BUILDER_STEPS


def validate_application_builder_model_alignment() -> None:
    """Raise when a workflow step references a missing JobApplication field."""

    available_fields = {item.name for item in fields(JobApplication)}
    invalid = {
        step.key: tuple(
            relationship
            for relationship in step.aggregate_relationships
            if relationship not in available_fields
        )
        for step in APPLICATION_BUILDER_STEPS
    }
    invalid = {key: values for key, values in invalid.items() if values}
    if invalid:
        raise ValueError(
            f"Application Builder references unknown JobApplication fields: {invalid}"
        )
