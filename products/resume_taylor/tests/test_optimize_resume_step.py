from __future__ import annotations


def test_step_four_route_never_calls_the_evidence_audit(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    route = source.split('def start_final_stage():', 1)[1].split(
        '@app.post("/resume/save/<version>")', 1
    )[0]

    assert "_run_and_store_final_audit(" not in route
    assert "_run_post_confirmation_evidence_review(" not in route
    assert "_store_optimized_final_export(" in route
    assert "final_optimization_recommendations(" in route
    assert "report_after" in route


def test_step_four_export_helper_builds_report_and_word_bytes(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    helper = source.split('def _store_optimized_final_export(', 1)[1].split(
        'def _build_final_report_snapshot(', 1
    )[0]

    assert "effective_final_resume_title(state)" in helper
    assert "_approved_resume_from_proposal(profile, title, proposal)" in helper
    assert "export_resume_docx(" in helper
    assert "state.final_resume_bytes = resume_bytes" in helper
    assert "state.final_proposal = proposal" in helper
    assert "_build_final_report_snapshot(" in helper
    assert 'capture_workflow_step_snapshot(' in helper
