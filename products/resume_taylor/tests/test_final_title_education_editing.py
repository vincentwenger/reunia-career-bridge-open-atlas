from __future__ import annotations

from io import BytesIO

from docx import Document

from resume_tailor.docx_export import export_resume_docx
from resume_tailor.models import ApprovedResume, EducationItem
from resume_tailor.pdf_export import export_resume_pdf


def test_final_editor_exposes_job_title_and_education_inputs(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'name="target_title"' in template
    assert "Editable job title" in template
    for field in ("credential", "institution", "location", "date", "detail"):
        assert f'education__{{{{ loop.index0 }}}}__{field}' in template
    assert "Editable education entry" in template


def test_final_save_wires_title_and_education_to_refreshed_exports(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "def effective_final_resume_title" in source
    assert "def profile_with_education_from_form" in source
    assert 'request.form.get("target_title", effective_final_resume_title(current))' in source
    assert "current.confirmed_profile = edited_profile" in source
    assert "current.final_resume_title = edited_title" in source
    assert "_store_optimized_final_export(current, profile, edited)" in source
    assert "_approved_resume_from_proposal(profile, title, proposal)" in source
    assert "resume_title=title" in source


def test_edited_title_and_education_are_supported_by_word_and_pdf_exports(
    project_root, profile, proposal
):
    updated_profile = profile.model_copy(deep=True)
    updated_profile.education[0] = EducationItem(
        credential="Updated Machine Learning Certificate",
        institution="Updated University",
        location="Portland, OR",
        date="07/2026",
        detail="Final user edit.",
    )
    title = "Senior Regulatory Reporting Developer"
    proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
    bullets_by_experience = {
        experience.id: [
            proposal_lookup[bullet.id].proposed_text
            for bullet in experience.bullets
            if bullet.id in proposal_lookup and proposal_lookup[bullet.id].include
        ]
        for experience in updated_profile.experiences
    }
    approved = ApprovedResume(
        target_title=title,
        professional_summary=proposal.professional_summary,
        skills=proposal.skills,
        bullets_by_experience=bullets_by_experience,
    )

    word_bytes = export_resume_docx(
        project_root / "data" / "resume_template_professional.docx",
        updated_profile,
        approved,
    )
    word_text = "\n".join(
        paragraph.text for paragraph in Document(BytesIO(word_bytes)).paragraphs
    )
    assert title in word_text
    assert "Updated Machine Learning Certificate" in word_text

    pdf_bytes = export_resume_pdf(updated_profile, approved)
    assert pdf_bytes.startswith(b"%PDF")
    from pypdf import PdfReader

    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf_bytes)).pages
    )
    assert title in pdf_text
    assert "Updated Machine Learning Certificate" in pdf_text
