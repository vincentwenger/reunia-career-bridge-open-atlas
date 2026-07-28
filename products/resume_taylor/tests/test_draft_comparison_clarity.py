from __future__ import annotations

from resume_tailor.web_state import WorkflowState


def test_comparison_uses_three_public_resume_names(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'INITIAL_RESUME_LABEL = "Initial Resume"' in source
    assert 'JOB_ALIGNED_RESUME_LABEL = "Job-Aligned Resume"' in source
    assert 'FINAL_RESUME_LABEL = "Final Resume"' in source
    for obsolete in (
        "Generated Tailored Resume",
        "Quality-Improved Resume",
        "Finalized Resume",
        "Locked Draft — comparison only",
        "Current Final — comparison preview",
    ):
        assert obsolete not in template


def test_workflow_state_resets_current_resume_snapshots(profile, proposal):
    state = WorkflowState(
        source_profile=profile,
        workflow_stage="final",
        draft_proposal=proposal.model_copy(deep=True),
        previous_draft_proposal=proposal.model_copy(deep=True),
        final_proposal=proposal.model_copy(deep=True),
        draft_revision=2,
        previous_draft_revision=1,
    )

    state.clear_tailoring_results()

    assert state.draft_proposal is None
    assert state.previous_draft_proposal is None
    assert state.final_proposal is None
    assert state.draft_revision == 0
    assert state.previous_draft_revision is None


def test_draft_edits_track_previous_revision(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "state.previous_draft_proposal = prior.model_copy(deep=True)" in source
    assert "state.draft_revision = prior_revision + 1" in source
    assert 'redirect_args["compare"] = "previous"' in source


def test_step_three_and_four_use_previous_to_current_comparisons(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "comparison_proposal=initial_editor_proposal" in source
    assert "comparison_label=INITIAL_RESUME_LABEL" in source
    assert "current_label=JOB_ALIGNED_RESUME_LABEL" in source
    assert "job_aligned_proposal" in source
    assert "final_comparison_proposal" in source
    assert "comparison_proposal=final_comparison_proposal" in source
    assert "comparison_label=JOB_ALIGNED_RESUME_LABEL" in source
    assert "current_label=FINAL_RESUME_LABEL" in source
    final_comparison_call = source.split("final_editor_data = (", 1)[1].split(
        "deterministic_issues =", 1
    )[0]
    assert "include_comparison_reasons=True" in final_comparison_call
    assert "Job-Aligned Resume → Final Resume" in template
    assert "comparison appears only when" in template


def test_comparison_ui_remains_shared_and_collapsed(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")
    javascript = (project_root / "static" / "app.js").read_text(encoding="utf-8")

    assert "Resume comparison" in template
    assert "resume-comparison-toolbar" in template
    assert ".resume-comparison-toolbar" in styles
    assert "data-reference-diff" in template
    assert "container.dataset.reference" in javascript
    bullet_details_line = next(
        line for line in template.splitlines()
        if '<details class="bullet-editor bullet-status-' in line
    )
    assert " open" not in bullet_details_line


def test_experience_change_counts_only_highlight_nonzero_values(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'class="added-count{% if experience.added_count > 0 %} has-changes{% endif %}"' in template
    assert 'class="excluded-count{% if experience.excluded_count > 0 %} has-changes{% endif %}"' in template
    assert ".experience-change-summary .added-count.has-changes" in styles
    assert ".experience-change-summary .excluded-count.has-changes" in styles
