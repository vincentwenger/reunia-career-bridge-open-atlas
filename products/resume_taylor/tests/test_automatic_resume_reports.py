from __future__ import annotations

from resume_tailor.web_state import WorkflowState


def test_reports_are_automatic_at_each_workflow_milestone(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")

    start_route = source.split('def start_workflow():', 1)[1].split(
        '@app.post("/reports/initial")', 1
    )[0]
    confirmation_route = source.split('def apply_confirmation():', 1)[1].split(
        '@app.post("/confirmation/reopen")', 1
    )[0]
    store_helper = source.split('def store_working_proposal(', 1)[1].split(
        'def proposal_view_data(', 1
    )[0]
    final_route = source.split('def start_final_stage():', 1)[1].split(
        '@app.post("/resume/save/<version>")', 1
    )[0]

    tailor_branch = start_route.split('elif action == "tailor":', 1)[1]
    assert "_refresh_initial_resume_report(" not in tailor_branch
    assert "without blocking the workflow" in tailor_branch
    assert "_refresh_job_aligned_resume_report(" not in confirmation_route
    assert "_refresh_job_aligned_resume_report(" not in store_helper
    assert "clear_draft_report()" in confirmation_route
    assert "clear_draft_report()" in store_helper
    assert "_store_optimized_final_export(" in final_route
    assert '@app.post("/reports/auto/<report_name>")' in source


def test_reports_tab_is_review_first_with_recovery_actions(project_root):
    template = (project_root / "templates" / "index.html").read_text(
        encoding="utf-8"
    )
    source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "Reports are generated automatically" in template
    assert "Run Initial Resume Report" not in template
    assert "Run Job-Aligned Resume Report" not in template
    assert "Rerun report" in template
    assert "Retry report" in template
    assert '@app.post("/reports/initial")' in source
    assert '@app.post("/reports/draft")' in source
    assert '@app.post("/reports/final")' in source
    javascript = (project_root / "static" / "app.js").read_text(encoding="utf-8")
    assert "data-auto-report" in template
    assert "run_automatic_report" in template
    assert "fetch(item.dataset.url" in javascript


def test_report_failure_does_not_discard_final_export(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    helper = source.split('def _store_optimized_final_export(', 1)[1].split(
        'def _build_final_report_snapshot(', 1
    )[0]

    assert "state.final_resume_bytes = resume_bytes" in helper
    assert "except (TemplateError, ValueError) as exc:" in helper
    assert "state.final_report_error = str(exc)" in helper
    assert "must not discard an otherwise valid Word export" in helper


def test_report_error_state_is_cleared_with_its_snapshot(profile):
    state = WorkflowState(source_profile=profile)
    state.updated_report_error = "draft failed"
    state.updated_report_created_at = "now"
    state.final_report_error = "final failed"
    state.initial_report_error = "initial failed"
    state.initial_report_created_at = "now"

    state.clear_draft_report()
    state.clear_final_report()
    state.clear_results()

    assert state.updated_report_error == ""
    assert state.updated_report_created_at == ""
    assert state.final_report_error == ""
    assert state.initial_report_error == ""
    assert state.initial_report_created_at == ""
