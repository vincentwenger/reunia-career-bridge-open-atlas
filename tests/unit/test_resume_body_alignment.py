from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from reportlab.lib.enums import TA_LEFT, TA_RIGHT

from products.resume_taylor.resume_tailor.docx_styles import (
    STYLE_BULLET,
    STYLE_SUMMARY,
    configure_resume_document,
)
from products.resume_taylor.resume_tailor.pdf_export import _build_styles


def test_mid_career_docx_summary_and_bullets_are_left_aligned() -> None:
    document = Document()
    configure_resume_document(
        document,
        career_stage="mid_career",
        visual_design="classic",
    )

    assert (
        document.styles[STYLE_SUMMARY].paragraph_format.alignment
        == WD_ALIGN_PARAGRAPH.LEFT
    )
    assert (
        document.styles[STYLE_BULLET].paragraph_format.alignment
        == WD_ALIGN_PARAGRAPH.LEFT
    )


def test_mid_career_pdf_body_and_bullets_are_left_aligned_but_dates_stay_right() -> None:
    document = Document()
    theme = configure_resume_document(
        document,
        career_stage="mid_career",
        visual_design="classic",
    )
    styles = _build_styles(theme)

    assert styles["body"].alignment == TA_LEFT
    assert styles["bullet"].alignment == TA_LEFT
    assert styles["employer_right"].alignment == TA_RIGHT
    assert styles["education_right"].alignment == TA_RIGHT
