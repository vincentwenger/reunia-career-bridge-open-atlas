from __future__ import annotations


def test_resume_editor_explains_saved_vs_unsaved_versions(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "data-resume-save-button" in template
    assert "data-resume-save-status" in template
    assert "data-resume-download" in template
    assert "The generated resume is already saved" in template
    assert "Downloads use the last saved version" in template


def test_resume_editor_tracks_unsaved_changes_before_download(project_root):
    javascript = (project_root / "static" / "app.js").read_text(encoding="utf-8")

    assert "resume-editor-form" in javascript
    assert "const savedSnapshot = snapshot()" in javascript
    assert "form.dataset.dirty" in javascript
    assert "saveButton.disabled = !dirty" in javascript
    assert "Unsaved edits are not included yet" in javascript
    assert "event.preventDefault()" in javascript
