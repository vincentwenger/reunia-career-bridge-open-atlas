"""Career Bridge information architecture tied to the JobApplication aggregate.

The definitions are delivery-framework neutral. Product adapters may map each
section to an existing route while legacy capabilities are migrated behind the
shared ports.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from career_bridge.domain.models import JobApplication


@dataclass(frozen=True, slots=True)
class CareerNavigationSection:
    """One top-level Career Bridge workspace and its aggregate relationships."""

    key: str
    label: str
    order: int
    aggregate_fields: tuple[str, ...]
    description: str


CAREER_NAVIGATION: tuple[CareerNavigationSection, ...] = (
    CareerNavigationSection(
        key="foundation",
        label="Foundation",
        order=1,
        aggregate_fields=(
            "candidate_profile_id",
            "career_background_id",
            "resume_id",
            "evidence_library_id",
        ),
        description="Maintain the reusable Baseline Resume, Career Profile, and verified evidence used across applications.",
    ),
    CareerNavigationSection(
        key="jobs_applications",
        label="Jobs & Applications",
        order=2,
        aggregate_fields=(
            "target_job_description_id",
            "tailored_resume_version_ids",
            "current_tailored_resume_version_id",
            "status",
        ),
        description="Discover roles and manage one evidence-backed workspace for each job application.",
    ),
    CareerNavigationSection(
        key="interviews",
        label="Interviews",
        order=3,
        aggregate_fields=("interview_preparation_id", "mock_interview_session_ids"),
        description="Prepare, practice, and review application-specific interview answers.",
    ),
    CareerNavigationSection(
        key="progress",
        label="Progress",
        order=4,
        aggregate_fields=("improvement_action_ids", "status", "status_history"),
        description="Manage the Career Action Plan and track measurable application outcomes.",
    ),
)


def career_navigation() -> tuple[CareerNavigationSection, ...]:
    """Return the canonical top-level navigation in display order."""

    return CAREER_NAVIGATION


def validate_navigation_model_alignment() -> None:
    """Raise when a navigation section references a missing aggregate field."""

    available_fields = {item.name for item in fields(JobApplication)}
    invalid = {
        section.key: tuple(
            field_name
            for field_name in section.aggregate_fields
            if field_name not in available_fields
        )
        for section in CAREER_NAVIGATION
    }
    invalid = {key: values for key, values in invalid.items() if values}
    if invalid:
        raise ValueError(f"navigation references unknown JobApplication fields: {invalid}")
