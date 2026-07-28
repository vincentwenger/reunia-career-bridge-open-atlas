from __future__ import annotations


def test_view_model_builds_title_summary_and_skill_comparisons(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "comparison_title: str = \"\"" in app_source
    assert '"title_comparison"' in app_source
    assert '"summary_comparison"' in app_source
    assert '"skill_comparisons"' in app_source
    assert "compare_skills(reference_items" in app_source
    assert "reference_word_count" in app_source
    assert "current_sentence_count" in app_source


def test_resume_editor_renders_complete_comparison_sections(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "Profile title comparison" in template
    assert "summary_comparison.reference_html" in template
    assert "skills added" in template
    assert "skill-comparison-grid" in template
    assert "Compared as exact skill names" in template
    assert "Professional Experience" in template
