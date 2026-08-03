from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx import Document

from products.resume_taylor.resume_tailor.docx_export import export_resume_docx
from products.resume_taylor.resume_tailor.models import (
    ApprovedResume,
    CandidateProfile,
    ContactInfo,
    Experience,
    ResumeBullet,
    SkillSet,
    VerifiedSkills,
)


TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "products"
    / "resume_taylor"
    / "data"
    / "resume_template_professional.docx"
)


def test_docx_does_not_force_second_employer_to_a_new_page() -> None:
    long_bullet = (
        "Designed, implemented, validated, and supported a complex data workflow "
        "across multiple systems while documenting outcomes and coordinating with stakeholders. "
    )
    first_bullets = [f"{long_bullet}Result {index}." for index in range(7)]
    profile = CandidateProfile(
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
                employer="First Employer",
                location="Portland, OR",
                dates="01/2020 - Present",
                title="Data Engineer",
                bullets=[
                    ResumeBullet(id=f"EXP-001-B{index:02d}", text=bullet)
                    for index, bullet in enumerate(first_bullets, start=1)
                ],
            ),
            Experience(
                id="EXP-002",
                employer="Second Employer",
                location="Paris, France",
                dates="01/2017 - 12/2019",
                title="QA Engineer",
                bullets=[
                    ResumeBullet(
                        id="EXP-002-B01",
                        text="Performed quality assurance testing.",
                    )
                ],
            ),
        ],
    )
    approved = ApprovedResume(
        target_title="Data Engineer",
        professional_summary="Experienced engineer.",
        skills=SkillSet(),
        bullets_by_experience={
            "EXP-001": first_bullets,
            "EXP-002": ["Performed quality assurance testing."],
        },
    )

    payload = export_resume_docx(
        TEMPLATE,
        profile,
        approved,
        career_stage="mid_career",
        resume_format="technical",
        visual_design="classic",
    )
    document = Document(BytesIO(payload))
    second_employer = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.text.startswith("Second Employer")
    )

    assert second_employer.paragraph_format.page_break_before is not True
