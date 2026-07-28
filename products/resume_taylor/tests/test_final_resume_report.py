from __future__ import annotations

from resume_tailor.web_state import WorkflowState


def test_draft_report_clear_preserves_final_export(profile, proposal):
    state = WorkflowState(source_profile=profile, draft_proposal=proposal.model_copy(deep=True))
    state.final_report_proposal = proposal.model_copy(deep=True)
    state.final_report_profile = profile.model_copy(deep=True)
    state.final_report_filename = "Final_Resume.docx"
    state.final_resume_bytes = b"approved export"
    state.final_resume_pdf_bytes = b"approved pdf"

    state.clear_draft_report()

    assert state.final_report_proposal is not None
    assert state.final_report_profile is not None
    assert state.final_resume_bytes == b"approved export"
    assert state.final_resume_pdf_bytes == b"approved pdf"


def test_new_tailoring_workflow_clears_old_final_snapshot(profile, analysis, proposal):
    state = WorkflowState(source_profile=profile, analysis=analysis, draft_proposal=proposal)
    state.final_report_proposal = proposal.model_copy(deep=True)
    state.final_report_profile = profile.model_copy(deep=True)
    state.final_resume_bytes = b"previous export"
    state.final_resume_pdf_bytes = b"previous pdf"
    state.final_resume_pdf_error = "previous conversion error"
    state.final_report_filename = "previous.docx"

    state.clear_tailoring_results()

    assert state.final_report is None
    assert state.final_report_proposal is None
    assert state.final_report_profile is None
    assert state.final_resume_bytes is None
    assert state.final_resume_pdf_bytes is None
    assert state.final_resume_pdf_error == ""
    assert state.final_report_filename == ""


def test_final_report_and_export_are_created_after_optimization(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "def _build_final_report_snapshot(" in source
    assert "_build_final_report_snapshot(state, profile, proposal, resume_bytes)" in source
    assert '@app.get("/download/final-resume")' in source
    assert '@app.post("/reports/final")' in source
    assert "Generated during Improve Resume Quality" in template
    assert "Initial → Job-Aligned" in template
    assert "Job-Aligned → Final" in template
