from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from products.resume_taylor.resume_tailor.bullet_text import (
    bullet_has_multiple_complete_sentences,
    normalize_resume_bullet_terminal_punctuation,
)
from products.resume_taylor.resume_tailor.docx_export import export_resume_docx
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
from products.resume_taylor.resume_tailor.pdf_export import export_resume_pdf


TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "products"
    / "resume_taylor"
    / "data"
    / "resume_template_professional.docx"
)


def _profile_and_resume() -> tuple[CandidateProfile, ApprovedResume]:
    bullets = [
        "Delivered regulatory reports for banking clients.",
        "Designed the platform. Improved reporting accuracy.",
        "Supported regulated operations in the U.S.",
    ]
    profile = CandidateProfile(
        name="Candidate",
        contact=ContactInfo(
            location="Portland, OR",
            phone="",
            email="candidate@example.com",
        ),
        current_summary="Experienced data engineer.",
        skills=VerifiedSkills(),
        education=[
            EducationItem(
                credential="Master of Science",
                institution="Example University",
                location="Portland, OR",
                date="2020",
                detail="Emphasis in Information Systems Engineering.",
            )
        ],
        experiences=[
            Experience(
                id="EXP-001",
                employer="Example Employer",
                location="Portland, OR",
                dates="01/2020 - 12/2025",
                title="Data Engineer",
                bullets=[
                    ResumeBullet(id=f"EXP-001-B{index:02d}", text=text)
                    for index, text in enumerate(bullets, start=1)
                ],
            )
        ],
    )
    approved = ApprovedResume(
        target_title="Data Engineer",
        professional_summary="Experienced data engineer.",
        skills=SkillSet(),
        bullets_by_experience={"EXP-001": bullets},
    )
    return profile, approved


def test_single_sentence_bullet_omits_optional_terminal_period() -> None:
    assert (
        normalize_resume_bullet_terminal_punctuation(
            "Delivered regulatory reports for banking clients."
        )
        == "Delivered regulatory reports for banking clients"
    )


def test_multi_sentence_and_intrinsic_abbreviation_periods_are_preserved() -> None:
    multi = "Designed the platform. Improved reporting accuracy."
    abbreviation = "Supported regulated operations in the U.S."

    assert bullet_has_multiple_complete_sentences(multi)
    assert normalize_resume_bullet_terminal_punctuation(multi) == multi
    assert normalize_resume_bullet_terminal_punctuation(abbreviation) == abbreviation


def test_docx_export_applies_no_period_style_to_resume_bullets() -> None:
    profile, approved = _profile_and_resume()

    payload = export_resume_docx(
        TEMPLATE,
        profile,
        approved,
        career_stage="mid_career",
        resume_format="technical",
        visual_design="classic",
    )
    document = Document(BytesIO(payload))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert "• Delivered regulatory reports for banking clients\n" in text
    assert "• Delivered regulatory reports for banking clients." not in text
    assert "• Designed the platform. Improved reporting accuracy." in text
    assert "• Supported regulated operations in the U.S." in text
    assert text.endswith("• Emphasis in Information Systems Engineering")


def test_pdf_export_applies_same_bullet_punctuation_policy() -> None:
    profile, approved = _profile_and_resume()

    payload = export_resume_pdf(
        profile,
        approved,
        career_stage="mid_career",
        resume_format="technical",
        visual_design="classic",
    )
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(payload)).pages)

    assert "Delivered regulatory reports for banking clients." not in text
    assert "Delivered regulatory reports for banking clients" in text
    assert "Designed the platform. Improved reporting accuracy." in text
    assert "Supported regulated operations in the U.S." in text
    assert "Emphasis in Information Systems Engineering." not in text
    assert "Emphasis in Information Systems Engineering" in text
