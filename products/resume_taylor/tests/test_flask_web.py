from __future__ import annotations

from jinja2 import Environment, FileSystemLoader


def test_project_uses_flask_instead_of_streamlit(project_root):
    requirements = (project_root / "requirements.txt").read_text(encoding="utf-8").casefold()
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "flask" in requirements
    assert "streamlit" not in requirements
    assert "from flask import" in app_source
    assert "def create_app(" in app_source


def test_flask_templates_parse(project_root):
    environment = Environment(loader=FileSystemLoader(project_root / "templates"))

    environment.get_template("base.html")
    environment.get_template("index.html")


def test_flask_ui_preserves_complete_workflow(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    base = (project_root / "templates" / "base.html").read_text(encoding="utf-8")

    labels = [
        "Initial Resume Report", "Start tailoring", "Create tailored resume",
        "Setup — Job &amp; Resume", "Confirm Your Experience", "Review Job Alignment",
        "Optimize &amp; Export", "View detailed job requirement analysis", "Additional experience confirmation",
        "Approve &amp; optimize resume", "Job-Aligned Resume → Final Resume",
        "Final Resume Report", "Rerun optimization", "Download PDF", "Download Word (.docx)",
    ]
    for label in labels:
        assert label in template
    assert "Improve Resume Quality" not in template
    assert "Finalize Resume" not in template
    assert "Verify &amp; Export Resume" not in template
    assert "Evidence verification" not in template
    assert "Resume Workflow" in base
    assert "Resume Reports" in base
    assert "Configuration" in base

def test_flask_ui_has_live_diff_tabs_and_server_state(project_root):
    javascript = (project_root / "static" / "app.js").read_text(encoding="utf-8")
    state_source = (project_root / "resume_tailor" / "web_state.py").read_text(encoding="utf-8")

    assert "wordDiff" in javascript
    assert "data-tab-target" in javascript
    assert "conditional-details" in javascript
    assert "InMemoryWorkflowStore" in state_source
    assert "Resume contents" in state_source


def test_reports_are_moved_to_dedicated_tab_with_status_cards(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "report-status-grid" in template
    assert "Generated automatically when Step 1 analyzes the job and resume" in template
    assert "Generated automatically when the Job-Aligned Resume is created" in template
    assert "Rerun report" in template
    assert "Retry report" in template
    assert "Final Resume Report" in template
    assert "Initial → Job-Aligned" in template
    assert "Job-Aligned → Final" in template
    assert "Initial → Final" in template
    assert "Improvement comparison" in template
    assert '@app.post("/reports/initial")' in app_source
    assert '@app.post("/reports/draft")' in app_source
    assert '@app.post("/reports/final")' in app_source
    assert '@app.get("/download/final-resume")' in app_source
    assert "report_view_name" in app_source


def test_nested_report_tabs_are_scoped_to_their_own_tab_container(project_root):
    javascript = (project_root / "static" / "app.js").read_text(encoding="utf-8")

    assert "item.closest('[data-tabs]') === tabs" in javascript


def test_workflow_stage_names_align_with_report_stages(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    initial = template.index("<h2>Setup — Job &amp; Resume</h2>")
    confirmation = template.index("<h2>Confirm Your Experience</h2>")
    tailored_review = template.index("<h2>Review Job Alignment</h2>")
    final = template.index("<h2>Optimize &amp; Export</h2>")
    assert initial < confirmation < tailored_review < final
    assert "<h2>Improve Resume Quality</h2>" not in template
    assert "<h2>Finalize Resume</h2>" not in template
    assert "<h2>Verify &amp; Export Resume</h2>" not in template

def test_workflow_stage_purposes_and_report_ownership_are_visible(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "Decision:</strong> Approve the Job-Aligned Resume or edit the wording before optimization." in template
    assert "Purpose:</strong> Apply safe quality improvements, choose a Word style, and download the Final Resume." in template
    for category in ["Content Quality", "Searchability", "Recruiter tips", "Formatting", "Soft skills"]:
        assert category in template
    assert "Evidence was already reviewed in Step 3 and is not reviewed again here." in template
    assert 'class="report-workflow-ownership"' in template
    assert "Primarily improved in:" in template

def test_final_stage_embeds_review_editor_and_export_on_one_page(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'id="tailored-resume"' in template
    assert 'id="final-review"' in template
    assert 'id="final-resume-editor"' in template
    assert 'id="audit-results"' not in template
    assert "Safe improvements" in template
    assert "Approve &amp; optimize" in template
    assert "Rerun optimization" in template
    assert "Download PDF" in template
    assert "Download Word (.docx)" in template
    assert "Job-Aligned Resume → Final Resume" in template
    assert "Your strongest score-safe version was kept" in template
    assert "Only changes that maintained or improved" in template
    assert 'id="quality-checks-stage"' not in template
    assert 'id="review-finalize"' not in template



def test_application_fit_decision_panel_is_shown_before_questions_and_verified_later(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "Preliminary Job Fit" in template
    assert "Verified Job Fit" in template
    assert "Calculated from the original resume before you answer any experience questions." in template
    assert "Decide whether this role deserves more time" in template
    assert "Directional interview potential" in template
    assert "Probably not worth your time" not in template  # supplied dynamically by assessment
    assert "application-fit-setup" in template
    assert "application-fit-confirmation" in template
    assert "application-fit-draft" in template
    assert 'Initial {{ "%.0f"|format(baseline_fit.score) }} → Verified' in template
    assert "def preliminary_application_fit(" in app_source
    assert "preliminary_application_fit=preliminary_fit" in app_source
    assert "current_application_fit" in app_source
    assert "fit_assessment.score" in app_source
