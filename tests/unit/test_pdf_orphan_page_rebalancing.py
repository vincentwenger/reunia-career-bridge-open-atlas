from __future__ import annotations

from products.resume_taylor.resume_tailor.docx_styles import compose_resume_theme
from products.resume_taylor.resume_tailor.models import (
    ApprovedResume,
    CandidateProfile,
    ContactInfo,
    EducationItem,
    Experience,
    ResumeBullet,
    SkillSet,
    VerifiedSkills,
)
from products.resume_taylor.resume_tailor.pdf_export import (
    _build_pdf_payload,
    _pdf_pagination_quality,
    _register_runtime_fonts,
    export_resume_pdf,
)


def _resume_data(bullet_count: int) -> tuple[CandidateProfile, ApprovedResume]:
    sentence = (
        "Designed and implemented regulated data-processing workflows, validated "
        "transformations, documented controls, and coordinated production releases "
        "with stakeholders."
    )
    bullets = [f"{sentence} Result {index}." for index in range(bullet_count)]
    profile = CandidateProfile(
        name="Candidate Name",
        contact=ContactInfo(
            location="Portland, OR",
            phone="",
            email="candidate@example.com",
        ),
        current_summary="",
        skills=VerifiedSkills(),
        education=[
            EducationItem(
                credential="Master of Science",
                institution="Example University",
                location="Portland, OR",
                date="09/2007",
            ),
            EducationItem(
                credential="Bachelor of Science",
                institution="Example University",
                location="Portland, OR",
                date="09/2003",
            ),
        ],
        experiences=[
            Experience(
                id="EXP-001",
                employer="Example Financial Platform",
                location="Portland, OR",
                dates="01/2015 - 05/2025",
                title="Software Engineer",
                bullets=[
                    ResumeBullet(id=f"EXP-001-B{index:02d}", text=bullet)
                    for index, bullet in enumerate(bullets, start=1)
                ],
            )
        ],
    )
    approved = ApprovedResume(
        target_title="Data Engineer",
        professional_summary=(
            "Experienced engineer delivering regulated financial data platforms, "
            "data pipelines, database upgrades, and production support."
        ),
        skills=SkillSet(),
        bullets_by_experience={"EXP-001": bullets},
    )
    return profile, approved


def _find_pdf_orphan_fixture():
    _register_runtime_fonts()
    theme = compose_resume_theme("mid_career", "corporate")
    for bullet_count in range(12, 36):
        profile, approved = _resume_data(bullet_count)
        regular = _build_pdf_payload(
            profile,
            approved,
            theme=theme,
            stage="mid_career",
            format_key="technical",
            design_key="corporate",
            resume_language="English",
            compact=False,
        )
        compact = _build_pdf_payload(
            profile,
            approved,
            theme=theme,
            stage="mid_career",
            format_key="technical",
            design_key="corporate",
            resume_language="English",
            compact=True,
        )
        regular_quality = _pdf_pagination_quality(regular, theme)
        compact_quality = _pdf_pagination_quality(compact, theme)
        if regular_quality.has_orphan_final_page and not compact_quality.has_orphan_final_page:
            return profile, approved, regular_quality, compact_quality
    raise AssertionError("Could not construct a PDF orphan-page fixture.")


def test_pdf_export_rebuilds_nearly_empty_final_page_with_compact_spacing() -> None:
    profile, approved, regular_quality, compact_quality = _find_pdf_orphan_fixture()

    payload = export_resume_pdf(
        profile,
        approved,
        career_stage="mid_career",
        resume_format="technical",
        visual_design="corporate",
    )
    final_quality = _pdf_pagination_quality(
        payload,
        compose_resume_theme("mid_career", "corporate"),
    )

    assert regular_quality.page_count == 2
    assert regular_quality.has_orphan_final_page is True
    assert compact_quality.page_count == 1
    assert final_quality.page_count == 1
    assert final_quality.has_orphan_final_page is False
