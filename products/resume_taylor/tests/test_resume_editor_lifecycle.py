from __future__ import annotations

from resume_tailor.web_state import WorkflowState


def test_resume_editor_is_embedded_in_four_step_workflow(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'WORKFLOW_STEP_ORDER = ("initial", "confirmation", "draft", "final")' in source
    assert 'active_tab == "editor"' not in source
    assert 'active_tab not in {"tailoring", "reports", "applications", "configuration"}' in source
    assert 'data-workflow-stage-panel="draft"' in template
    assert 'data-workflow-stage-panel="final"' in template
    assert 'data-workflow-stage-panel="quality"' not in template
    assert 'data-workflow-stage-panel="review"' not in template


def test_final_invalidation_preserves_job_aligned_report(profile, proposal):
    state = WorkflowState(source_profile=profile)
    state.updated_report_proposal_fingerprint = "draft-report"
    state.updated_report_created_at = "created"
    state.final_proposal = proposal.model_copy(deep=True)
    state.final_resume_bytes = b"final"

    state.clear_final_report()

    assert state.updated_report_proposal_fingerprint == "draft-report"
    assert state.updated_report_created_at == "created"
    assert state.final_proposal is not None
    assert state.final_resume_bytes is None
