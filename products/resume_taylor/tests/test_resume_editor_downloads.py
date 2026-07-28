from __future__ import annotations


def test_resume_editor_downloads_only_public_workflow_versions(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "Download resume" in template
    assert "download_resume_version" in template
    assert '@app.get("/download/resume-version/<version>")' in source
    for suffix in ("Initial_Resume", "Job_Aligned_Resume"):
        assert suffix in source
    assert 'final_resume_filename(profile, title, "docx")' in source
    assert "Final_Resume" not in source
    assert "Quality_Improved_Resume" not in source
    assert "Finalized_Resume" not in source


def test_download_route_uses_correct_snapshots_and_reuses_final_bytes(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    route = source.split('def download_resume_version(version: str):', 1)[1].split(
        '@app.get("/download/source-profile")', 1
    )[0]

    assert 'version not in {"initial", "draft", "final"}' in route
    assert "build_initial_resume_proposal" in route
    assert "current.draft_proposal" in route
    assert "current.final_proposal" in route
    assert "current.final_report_proposal_fingerprint" in route
    assert "_proposal_fingerprint(proposal)" in route
    assert "current.final_resume_bytes" in route
    assert "export_resume_docx" in route


def test_final_screen_has_pdf_primary_and_word_secondary_downloads(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "Optimized and ready to export" in template
    assert "url_for('download_final_resume') }}\">Download PDF" in template
    assert "url_for('download_final_resume_word')" in template
    assert "Download Word (.docx)" in template
    assert "PDF is recommended because it preserves the selected design" in template
    assert '@app.get("/download/final-resume-word")' in source
    assert "Remaining report recommendations are advisory" in template
    assert "Final resume editing is enabled." not in template
