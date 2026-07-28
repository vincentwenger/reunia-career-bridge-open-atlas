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
        key="career_profile",
        label="Career Profile",
        order=1,
        aggregate_fields=(
            "candidate_profile_id",
            "career_background_id",
            "resume_id",
            "evidence_library_id",
        ),
        description="Maintain professional background, accomplishments, preferences, constraints, source resume, and verified career evidence.",
    ),
    CareerNavigationSection(
        key="application_builder",
        label="Application Builder",
        order=2,
        aggregate_fields=(
            "target_job_description_id",
            "tailored_resume_version_ids",
            "current_tailored_resume_version_id",
            "status",
        ),
        description="Use one Application Workspace per target role, with its job posting, resume, company notes, recruiter messages, and tailored versions.",
    ),
    CareerNavigationSection(
        key="interview_preparation",
        label="Interview Preparation",
        order=3,
        aggregate_fields=("interview_preparation_id",),
        description="Research the company and role, retrieve verified evidence, and prepare likely interview questions and answer guidance.",
    ),
    CareerNavigationSection(
        key="mock_interview",
        label="Mock Interview",
        order=4,
        aggregate_fields=("mock_interview_session_ids",),
        description="Run and record mock interviews belonging to the selected job application; real-interview answer assistance is intentionally excluded.",
    ),
    CareerNavigationSection(
        key="interview_review",
        label="Interview Review",
        order=5,
        aggregate_fields=("mock_interview_session_ids",),
        description="Review mock-interview transcripts and score answer relevance, evidence, structure, clarity, and delivery.",
    ),
    CareerNavigationSection(
        key="career_action_plan",
        label="Career Action Plan",
        order=6,
        aggregate_fields=("improvement_action_ids",),
        description="Track resume changes, practice needs, applications, and recruiter follow-ups.",
    ),
    CareerNavigationSection(
        key="progress",
        label="Progress",
        order=7,
        aggregate_fields=("status", "status_history"),
        description="Track improvement across job applications and mock interviews.",
    ),
    CareerNavigationSection(
        key="help_support",
        label="Help & Support",
        order=8,
        aggregate_fields=(),
        description="Get product help without making support a child of the job application aggregate.",
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
