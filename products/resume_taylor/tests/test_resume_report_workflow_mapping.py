from __future__ import annotations


def test_resume_reports_explain_workflow_step_mapping(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    base = (project_root / "templates" / "base.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")

    assert "Six-step Application Builder · Three report snapshots" in template
    assert "Initial Resume" in template and "Step 1" in template
    assert "Job-Aligned Resume" in template and "Step 3" in template
    assert "Final Resume" in template and "Steps 4–6" in template
    assert "Reports are generated automatically" in template
    assert "refreshed after every saved Step 3 edit" in template
    assert "Initial → Job-Aligned" in template
    assert "Job-Aligned → Final" in template
    assert "Initial, Tailored, and Final Resume evidence and quality reports" in base
    assert ".report-version-map" in styles
    assert ".workflow-range-badge" in styles

