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
        ),
        description="Maintain professional background, accomplishments, preferences, constraints, and the reusable source resume.",
    ),
    CareerNavigationSection(
        key="career_translation",
        label="Career Translation",
        order=2,
        aggregate_fields=("career_background_id", "evidence_library_id"),
        description="Translate international titles, credentials, terminology, and transferable skills while preserving evidence boundaries.",
    ),
    CareerNavigationSection(
        key="career_evidence_library",
        label="Career Evidence Library",
        order=3,
        aggregate_fields=("evidence_library_id",),
        description="Maintain verified projects, achievements, credentials, and supporting career documents.",
    ),
    CareerNavigationSection(
        key="application_builder",
        label="Job Applications and Resume Workflow",
        order=4,
        aggregate_fields=(
            "target_job_description_id",
            "tailored_resume_version_ids",
            "current_tailored_resume_version_id",
            "status",
        ),
        description="Manage target roles and create evidence-backed tailored resume versions for each application.",
    ),
    CareerNavigationSection(
        key="application_materials",
        label="Application Materials",
        order=5,
        aggregate_fields=("target_job_description_id", "resume_id"),
        description="Store the job posting, resume, company notes, and recruiter messages for the selected application.",
    ),
    CareerNavigationSection(
        key="resume_reports",
        label="Resume Reports",
        order=6,
        aggregate_fields=(
            "tailored_resume_version_ids",
            "current_tailored_resume_version_id",
        ),
        description="Compare alignment, quality, evidence, and revision results across resume versions.",
    ),
    CareerNavigationSection(
        key="builder_configuration",
        label="AI Configuration",
        order=7,
        aggregate_fields=(),
        description="Configure the models, reasoning effort, and processing mode used by the Application Builder.",
    ),
    CareerNavigationSection(
        key="interview_preparation",
        label="Interview Preparation",
        order=8,
        aggregate_fields=("interview_preparation_id",),
        description="Prepare role-specific questions and answer guidance from the job description and verified evidence.",
    ),
    CareerNavigationSection(
        key="mock_interview",
        label="Mock Interview",
        order=9,
        aggregate_fields=("mock_interview_session_ids",),
        description="Run adaptive mock interviews that evaluate each answer and generate evidence-aware follow-up questions.",
    ),
    CareerNavigationSection(
        key="interview_review",
        label="Interview Review",
        order=10,
        aggregate_fields=("mock_interview_session_ids",),
        description="Review mock-interview transcripts and score answer relevance, evidence, structure, clarity, and delivery.",
    ),
    CareerNavigationSection(
        key="career_action_plan",
        label="Career Action Plan",
        order=11,
        aggregate_fields=("improvement_action_ids",),
        description="Track resume changes, practice needs, applications, recruiter follow-ups, and future learning actions.",
    ),
    CareerNavigationSection(
        key="progress",
        label="Impact & Progress",
        order=12,
        aggregate_fields=("status", "status_history"),
        description="Measure social-impact outcomes and improvement across job applications, resume workflows, mock interviews, and completed actions.",
    ),
    CareerNavigationSection(
        key="help_support",
        label="Help & Support",
        order=13,
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
