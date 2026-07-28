from __future__ import annotations

from resume_tailor.web_state import WorkflowState, initial_report_fingerprint


def test_initial_report_fingerprint_ignores_tailoring_progress_and_model_state(
    profile, proposal
):
    state = WorkflowState(
        source_profile=profile,
        job_description="Axiom developer role with SQL and regulatory reporting.",
        target_title="Axiom Developer",
    )
    baseline = initial_report_fingerprint(state)

    state.workflow_stage = "draft"
    state.draft_proposal = proposal.model_copy(deep=True)
    state.provisional_proposal = proposal.model_copy(deep=True)
    state.confirmation_complete = True
    state.confirmed_profile = profile
    state.processing_mode = "balanced"
    state.custom_analysis_tailoring_model = "some-other-model"
    state.custom_analysis_tailoring_reasoning_effort = "high"

    assert initial_report_fingerprint(state) == baseline


def test_initial_report_fingerprint_changes_only_with_baseline_inputs(profile):
    state = WorkflowState(
        source_profile=profile,
        job_description="Original job description",
        target_title="Original title",
    )
    baseline = initial_report_fingerprint(state)

    state.job_description = "Changed job description"
    assert initial_report_fingerprint(state) != baseline

    state.job_description = "Original job description"
    state.target_title = "Changed title"
    assert initial_report_fingerprint(state) != baseline

    state.target_title = "Original title"
    changed_profile = profile.model_copy(deep=True)
    changed_profile.current_summary = changed_profile.current_summary + " Updated."
    state.source_profile = changed_profile
    assert initial_report_fingerprint(state) != baseline


def test_starting_draft_preserves_initial_report_snapshot(profile, analysis, proposal):
    state = WorkflowState(
        source_profile=profile,
        job_description="Job description",
        target_title="Axiom Developer",
    )
    marker = object()
    state.initial_report = marker  # Runtime storage accepts the immutable report object.
    state.initial_report_input_fingerprint = initial_report_fingerprint(state)
    state.initial_report_analysis = analysis.model_copy(deep=True)
    state.initial_report_proposal = proposal.model_copy(deep=True)

    state.clear_tailoring_results()

    assert state.initial_report is marker
    assert state.initial_report_input_fingerprint == initial_report_fingerprint(state)
    assert state.initial_report_analysis == analysis
    assert state.initial_report_proposal == proposal


def test_initial_report_fingerprint_ignores_browser_newline_conversion(profile):
    state = WorkflowState(
        source_profile=profile,
        job_description="First line\nSecond line\n",
        target_title="Axiom Developer",
    )
    baseline = initial_report_fingerprint(state)

    # HTML form submission canonicalizes textarea line endings to CRLF.
    state.job_description = "First line\r\nSecond line\r\n"

    assert initial_report_fingerprint(state) == baseline


def test_initial_report_fingerprint_ignores_transport_whitespace(profile):
    state = WorkflowState(
        source_profile=profile,
        job_description="First line  \nSecond line",
        target_title="  Axiom   Developer  ",
    )
    baseline = initial_report_fingerprint(state)

    state.job_description = "First line\r\nSecond line  "
    state.target_title = "Axiom Developer"

    assert initial_report_fingerprint(state) == baseline
