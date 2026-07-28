from datetime import datetime, timezone

import pytest

from career_bridge.domain.enums import ApplicationStatus
from career_bridge.presentation.application_builder import (
    ApplicationDashboardItem,
    application_builder_steps,
    validate_application_builder_model_alignment,
)


def test_application_builder_has_canonical_six_steps():
    steps = application_builder_steps()
    assert [step.order for step in steps] == [1, 2, 3, 4, 5, 6]
    assert [step.label for step in steps] == [
        "Career and Job Setup",
        "Confirm Relevant Experience",
        "Review Tailored Resume",
        "Improve Resume Quality",
        "Finalize Resume",
        "Evidence Review and Export",
    ]


def test_dashboard_projection_validates_readiness_and_timezone():
    item = ApplicationDashboardItem(
        application_id="application-1",
        company="Example Bank",
        job_title="Senior Engineer",
        status=ApplicationStatus.INTERVIEWING,
        resume_version="Final Resume v2",
        interview_readiness=84.5,
        next_action="Practice behavioral questions",
        upcoming_at=datetime(2026, 8, 3, 17, 0, tzinfo=timezone.utc),
        upcoming_kind="interview",
    )
    assert item.interview_readiness == 84.5

    with pytest.raises(ValueError):
        ApplicationDashboardItem(
            application_id="application-1",
            company="Example Bank",
            job_title="Senior Engineer",
            status=ApplicationStatus.DRAFT,
            resume_version="Initial Resume",
            interview_readiness=101,
            next_action="Complete setup",
        )


def test_application_builder_relationships_match_aggregate():
    validate_application_builder_model_alignment()
