from __future__ import annotations


def test_guided_workflow_model_covers_four_stage_states(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "def build_guided_workflow(" in app_source
    for status in ["not_started", "in_progress", "completed", "needs_attention"]:
        assert f'"{status}"' in app_source
    for title in ['"Setup — Job & Resume"', '"Confirm Your Experience"', '"Review Job Alignment"', '"Optimize & Export"']:
        assert title in app_source
    workflow = app_source.split("def build_guided_workflow(", 1)[1].split("def final_audit_blockers", 1)[0]
    assert '"Improve Resume Quality"' not in workflow
    assert '"Finalize Resume"' not in workflow

def test_guided_workflow_is_rendered_on_tailoring_page(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")

    assert "Guided resume flow" in template
    assert "workflow-stepper" in template
    assert "{{ step.number }}" in template
    assert "{{ step.status_label }}" in template
    assert "aria-current" in template
    assert ".guided-workflow" in styles
    assert ".workflow-step-needs_attention" in styles


def test_tailoring_page_defaults_to_current_four_step_stage(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'active_guided_stage = guided_stage_for_state(current)' in app_source
    assert 'request.args.get("stage", active_guided_stage)' in app_source
    assert 'if selected_workflow_stage in {"quality", "review"}' not in app_source
    assert 'active_tab == "editor"' not in app_source
    for stage in ["initial", "confirmation", "draft", "final"]:
        assert f'data-workflow-stage-panel="{stage}"' in template
    assert 'data-workflow-stage-panel="quality"' not in template
    assert 'data-workflow-stage-panel="review"' not in template


def test_step_three_has_one_consolidated_primary_action(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    draft_start = template.index('data-workflow-stage-panel="draft"')
    final_start = template.index('data-workflow-stage-panel="final"')
    draft_panel = template[draft_start:final_start]
    assert "Review Job Alignment" in draft_panel
    assert "Approve &amp; optimize resume" in template
    assert "Optimize &amp; Export" in draft_panel
    assert "start_final_stage" in template
    assert "Verify resume evidence" not in draft_panel


def test_setup_analysis_modal_shows_plain_language_progress(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    setup_start = template.index('id="setup-workflow-form"')
    setup_end = template.index('</form>', setup_start)
    setup_form = template[setup_start:setup_end]

    for label in [
        "Reading the target job requirements",
        "Comparing the role with your original resume",
        "Identifying high-value experience to confirm",
        "Preparing Confirm Your Experience",
    ]:
        assert label in setup_form

    assert 'data-loading-step-timings="2500,8500,17500"' in setup_form
    assert "data-loading-reassurance-delayed=" in setup_form
    assert "data-loading-reassurance-extended=" in setup_form


def test_step_three_optimization_modal_shows_plain_language_progress(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    base = (project_root / "templates" / "base.html").read_text(encoding="utf-8")
    script = (project_root / "static" / "app.js").read_text(encoding="utf-8")

    assert "data-loading-steps=" in template
    assert "Saving your approved resume" in template
    assert "Testing improvements against the job-alignment score" in template
    assert "Preparing the final resume export" in template
    assert 'id="loading-progress"' in base
    assert 'id="loading-elapsed"' in base
    assert "startLoadingProgress" in script
    assert "Still working." in script


def test_confirmation_progress_modal_covers_initial_and_follow_up_processing(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (project_root / "static" / "app.js").read_text(encoding="utf-8")

    for label in [
        "Applying your confirmed answers",
        "Updating the job-aligned wording",
        "Checking every statement against evidence",
        "Applying your final answers",
        "Rechecking the affected resume content",
        "Replacing remaining uncertainty safely",
        "Preparing Review Job Alignment",
    ]:
        assert label in template

    assert 'data-loading-step-timings="2500,9000,18000"' in template
    assert "data-loading-reassurance-delayed=" in template
    assert "data-loading-reassurance-extended=" in template
    assert "startLoadingProgress(submitter, form)" in script
    assert "loadingData(primarySource, fallbackSource" in script
    assert "'loadingReassuranceDelayed'" in script
    assert "'loadingReassuranceExtended'" in script


def test_consolidated_step_runs_score_guarded_report_optimization(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    route = app_source.split('def start_final_stage():', 1)[1].split('@app.post("/resume/save/<version>")', 1)[0]
    assert "report_before = _build_optimization_report(" in route
    assert "report_issues = final_optimization_actionable_issues(" in route
    assert route.count("optimizer.apply_suggested_fixes(") == 1
    assert "for batch in" not in route
    assert "final_optimization_score_guard(" in route
    assert "apply_all_until_valid(" in route
    assert "_run_and_store_final_audit(" not in route
    assert "_store_optimized_final_export(" in route
    assert 'current.workflow_stage = "final"' in route

def test_final_step_compares_job_aligned_to_final_only_when_changed(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "final_comparison_proposal = (" in app_source
    assert "_proposal_json(final_reference_proposal)" in app_source
    assert "!= _proposal_json(final_proposal)" in app_source
    assert "comparison_proposal=final_comparison_proposal" in app_source
    assert "comparison_label=JOB_ALIGNED_RESUME_LABEL" in app_source
    assert "current_label=FINAL_RESUME_LABEL" in app_source
    assert "Job-Aligned Resume → Final Resume" in template
    assert "comparison appears only when" in template

def test_final_step_shows_report_categories_and_all_findings(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")
    optimization_source = (project_root / "resume_tailor" / "optimization.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")

    for category in ["Content Quality", "Searchability", "Recruiter tips", "Formatting", "Soft skills"]:
        assert category in template
        assert category in optimization_source
    assert "final_optimization_recommendations" in app_source
    assert "optimization_remaining" in template
    assert "never block download" in template
    assert 'id="audit-results"' not in template
    assert ".optimization-category-grid" in styles

def test_four_steps_share_stage_header_and_action_pattern(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")

    assert template.count('class="card workflow-stage-intro"') >= 4
    assert template.count('workflow-action-bar') >= 3
    assert 'aria-label="Setup next step"' in template
    assert 'aria-label="Confirmation next step"' in template
    assert 'aria-label="Final resume actions"' in template
    assert '.workflow-action-bar' in styles
    assert 'position: sticky;' in styles



def test_working_proposal_matches_confirmation_and_review_lifecycle(project_root):
    import ast

    app_source = (project_root / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(app_source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "working_proposal_for_stage"
    )
    isolated_module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            helper,
        ],
        type_ignores=[],
    )
    namespace = {}
    exec(
        compile(
            ast.fix_missing_locations(isolated_module),
            filename="working_proposal_for_stage",
            mode="exec",
        ),
        namespace,
    )
    select_proposal = namespace["working_proposal_for_stage"]

    class State:
        workflow_stage = "draft"
        confirmation_complete = False
        provisional_proposal = object()
        draft_proposal = None
        final_proposal = None

    state = State()
    assert select_proposal(state) is state.provisional_proposal

    state.confirmation_complete = True
    state.draft_proposal = object()
    assert select_proposal(state) is state.draft_proposal

    state.workflow_stage = "final"
    state.final_proposal = object()
    assert select_proposal(state) is state.final_proposal


def test_pre_question_fit_uses_preliminary_baseline_in_confirmation(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    confirmation_start = template.index('data-workflow-stage-panel="confirmation"')
    draft_start = template.index('data-workflow-stage-panel="draft"')
    confirmation_panel = template[confirmation_start:draft_start]
    assert "application_fit_panel(preliminary_application_fit" in confirmation_panel
    assert "decision_checkpoint" in template

    helper = app_source.split("def preliminary_application_fit(", 1)[1].split(
        "def current_application_fit(", 1
    )[0]
    assert "state.source_profile" in helper
    assert "candidate_answers" not in helper
    assert "state.confirmed_profile" not in helper
    assert "confirmation_complete=False" in helper
