from __future__ import annotations

from io import BytesIO

import pytest

from resume_tailor.export_naming import final_resume_filename


def test_final_filename_is_concise_professional_and_space_free(profile):
    filename = final_resume_filename(
        profile,
        "Senior Axiom Developer / Regulatory Reporting",
        "pdf",
    )

    assert filename == "Vincent_Wenger_Senior_Axiom_Developer_Regulatory_Reporting_Resume.pdf"
    assert " " not in filename
    assert "final" not in filename.casefold()
    assert "professional" not in filename.casefold()
    assert len(filename.rsplit(".", 1)[0]) <= 80


def test_final_filename_transliterates_and_truncates_long_values(profile):
    profile = profile.model_copy(update={"name": "José Álvarez " + "Wenger " * 20})
    filename = final_resume_filename(profile, "Principal " + "Engineering " * 20, "docx")

    assert filename.startswith("Jose_Alvarez_")
    assert filename.endswith("_Resume.docx")
    assert " " not in filename
    assert len(filename.rsplit(".", 1)[0]) <= 80


def test_native_pdf_export_requires_no_word_or_libreoffice(profile, analysis, proposal):
    from pypdf import PdfReader

    from resume_tailor.pdf_export import export_resume_pdf
    from resume_tailor.validation import build_approved_resume

    approved = build_approved_resume(profile, analysis, proposal)

    payload = export_resume_pdf(
        profile,
        approved,
        career_stage="mid_career",
        resume_format="technical",
        visual_design="modern",
    )

    assert payload.startswith(b"%PDF-")
    assert len(payload) > 5_000
    reader = PdfReader(BytesIO(payload))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert profile.name in text
    assert analysis.target_title in text
    assert "Technical Skills" in text
    assert "Engineering Experience" in text


@pytest.mark.parametrize("career_stage", ["early_career", "mid_career", "executive"])
@pytest.mark.parametrize("resume_format", ["standard", "technical", "career_changer", "freelance"])
@pytest.mark.parametrize("visual_design", ["corporate", "modern"])
def test_native_pdf_export_supports_every_resume_combination(
    profile,
    analysis,
    proposal,
    career_stage,
    resume_format,
    visual_design,
):
    from resume_tailor.pdf_export import export_resume_pdf
    from resume_tailor.validation import build_approved_resume

    approved = build_approved_resume(profile, analysis, proposal)
    payload = export_resume_pdf(
        profile,
        approved,
        career_stage=career_stage,
        resume_format=resume_format,
        visual_design=visual_design,
    )

    assert payload.startswith(b"%PDF-")
    assert payload.rstrip().endswith(b"%%EOF")


def test_native_pdf_preserves_inline_bold_emphasis(profile, analysis, proposal):
    from pypdf import PdfReader

    from resume_tailor.pdf_export import export_resume_pdf
    from resume_tailor.validation import build_approved_resume

    approved = build_approved_resume(profile, analysis, proposal)
    payload = export_resume_pdf(
        profile,
        approved,
        career_stage="mid_career",
        resume_format="standard",
        visual_design="corporate",
    )

    rendered_runs: list[tuple[str, str]] = []
    reader = PdfReader(BytesIO(payload))
    for page in reader.pages:
        def capture_text(text, _cm, _tm, font_dict, _font_size):
            base_font = str((font_dict or {}).get("/BaseFont", ""))
            if text.strip():
                rendered_runs.append((text, base_font))

        page.extract_text(visitor_text=capture_text)

    hard_label_fonts = [font for text, font in rendered_runs if "Hard Skills:" in text]
    hard_value_fonts = [font for text, font in rendered_runs if "ETL Workflows" in text]

    assert hard_label_fonts
    assert any("Bold" in font for font in hard_label_fonts)
    assert hard_value_fonts
    assert any("Bold" not in font for font in hard_value_fonts)
