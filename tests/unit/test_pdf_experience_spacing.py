from __future__ import annotations

from reportlab.platypus import KeepTogether, Spacer

from products.resume_taylor.resume_tailor.docx_styles import compose_resume_theme
from products.resume_taylor.resume_tailor.models import (
    ApprovedResume,
    CandidateProfile,
    ContactInfo,
    Experience,
    ResumeBullet,
    SkillSet,
    VerifiedSkills,
)
from products.resume_taylor.resume_tailor.pdf_export import (
    EXPERIENCE_ENTRY_GAP_POINTS,
    _add_experience,
    _build_styles,
)


def _profile() -> CandidateProfile:
    return CandidateProfile(
        name="Candidate",
        contact=ContactInfo(
            location="Portland, OR",
            phone="",
            email="candidate@example.com",
        ),
        current_summary="Experienced engineer.",
        skills=VerifiedSkills(),
        education=[],
        experiences=[
            Experience(
                id="EXP-001",
                employer="Aviva",
                location="Paris, France",
                dates="08/2010 - 08/2011",
                title="IT Auditor",
                bullets=[ResumeBullet(id="EXP-001-B01", text="Audited IT controls.")],
            ),
            Experience(
                id="EXP-002",
                employer="Capgemini",
                location="Paris, France",
                dates="10/2007 - 08/2010",
                title="QA Engineer",
                bullets=[ResumeBullet(id="EXP-002-B01", text="Performed quality assurance testing.")],
            ),
        ],
    )


def test_pdf_adds_six_point_gap_before_each_subsequent_employer() -> None:
    profile = _profile()
    approved = ApprovedResume(
        target_title="Data Engineer",
        professional_summary="Experienced engineer.",
        skills=SkillSet(),
        bullets_by_experience={
            "EXP-001": ["Audited IT controls."],
            "EXP-002": ["Performed quality assurance testing."],
        },
    )
    theme = compose_resume_theme("mid_career", "classic")
    styles = _build_styles(theme)
    story: list = []

    _add_experience(
        story,
        profile,
        approved,
        theme,
        styles,
        heading="Engineering Experience",
        usable_width=500,
    )

    headings = [item for item in story if isinstance(item, KeepTogether)]
    assert len(headings) == 2
    assert not isinstance(headings[0]._content[0], Spacer)
    assert isinstance(headings[1]._content[0], Spacer)
    assert headings[1]._content[0].height == EXPERIENCE_ENTRY_GAP_POINTS == 6


def test_pdf_does_not_force_second_employer_to_a_new_page() -> None:
    profile = _profile()
    long_bullet = (
        "Designed, implemented, validated, and supported a complex data workflow "
        "across multiple systems while documenting outcomes and coordinating with stakeholders. "
    )
    first_bullets = [f"{long_bullet}Result {index}." for index in range(7)]
    approved = ApprovedResume(
        target_title="Data Engineer",
        professional_summary="Experienced engineer.",
        skills=SkillSet(),
        bullets_by_experience={
            "EXP-001": first_bullets,
            "EXP-002": ["Performed quality assurance testing."],
        },
    )
    theme = compose_resume_theme("mid_career", "classic")
    styles = _build_styles(theme)
    story: list = []

    _add_experience(
        story,
        profile,
        approved,
        theme,
        styles,
        heading="Engineering Experience",
        usable_width=500,
    )

    assert not any(item.__class__.__name__ == "PageBreak" for item in story)
    headings = [item for item in story if isinstance(item, KeepTogether)]
    assert len(headings) == 2
    assert isinstance(headings[1]._content[0], Spacer)
