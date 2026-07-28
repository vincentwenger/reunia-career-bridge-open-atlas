from __future__ import annotations

from resume_tailor.experience_comparison import classify_bullet_inclusion


def test_included_bullet_becomes_explicit_exclusion():
    result = classify_bullet_inclusion(
        reference_include=True,
        current_include=False,
        reference_text="Delivered a regulatory report.",
        current_text="Delivered a regulatory report.",
        current_label="Current Draft (v1)",
    )

    assert result.status == "excluded"
    assert result.label == "Excluded from Current Draft (v1)"
    assert result.reference_for_diff == "Delivered a regulatory report."
    assert result.current_for_diff == ""


def test_excluded_bullet_restored_in_next_draft_is_added():
    result = classify_bullet_inclusion(
        reference_include=False,
        current_include=True,
        reference_text="Resolved client issues.",
        current_text="Resolved client issues.",
        current_label="Current Draft (v2)",
    )

    assert result.status == "added"
    assert result.label == "Added to Current Draft (v2)"
    assert result.reference_for_diff == ""
    assert result.current_for_diff == "Resolved client issues."


def test_editor_exposes_excluded_bullets_and_restore_control(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    javascript = (project_root / "static" / "app.js").read_text(encoding="utf-8")

    assert "classify_bullet_inclusion" in app_source
    assert '"excluded_count": status_counts["excluded"]' in app_source
    assert "Intentionally excluded from {{ bullet.current_label }}" in template
    assert "Restore to {{ version_resume_label }}" in template
    assert "experience-change-summary" in template
    assert "bullet.inclusion_status" in template
    assert "data-restore-bullet" in template
    assert "The source accomplishment remains available in the Initial Resume" in template
    assert "document.querySelectorAll('[data-restore-bullet]')" in javascript
    assert "This bullet will return after you save the Job-Aligned Resume." in javascript


def test_missing_reference_bullet_is_restored_and_included_after_fixes():
    result = classify_bullet_inclusion(
        reference_include=True,
        current_include=True,
        reference_text="Source wording that was never rendered.",
        current_text="Source wording that was never rendered.",
        current_label="Draft after fixes (v2)",
        reference_label="Draft before fixes (v1)",
        reference_present=False,
    )

    assert result.status == "restored_missing_included"
    assert result.label == "Restored missing bullet — included"
    assert result.reference_for_diff == ""
    assert result.current_for_diff == "Source wording that was never rendered."


def test_missing_reference_bullet_restored_but_excluded_has_visible_status():
    result = classify_bullet_inclusion(
        reference_include=True,
        current_include=False,
        reference_text="Source wording that was never rendered.",
        current_text="Source wording that was never rendered.",
        current_label="Draft after fixes (v2)",
        reference_label="Draft before fixes (v1)",
        reference_present=False,
        current_present=True,
    )

    assert result.status == "restored_missing_excluded"
    assert result.label == "Restored missing bullet — not included"
    assert result.reference_for_diff == ""
    assert result.current_for_diff == "Source wording that was never rendered."


def test_bullet_missing_from_both_structured_drafts_remains_unchanged_exclusion():
    result = classify_bullet_inclusion(
        reference_include=False,
        current_include=False,
        reference_text="",
        current_text="Source wording fallback.",
        current_label="Draft after fixes (v2)",
        reference_label="Draft before fixes (v1)",
        reference_present=False,
        current_present=False,
    )

    assert result.status == "excluded_unchanged"
    assert result.label == "Not included in Draft before fixes (v1) or Draft after fixes (v2)"
    assert result.reference_for_diff == ""
    assert result.current_for_diff == ""


def test_editor_passes_reference_presence_into_comparison(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "reference_present = comparison_item is not None" in app_source
    assert "reference_present=reference_present" in app_source
    assert "current_present=item is not None" in app_source


def test_restored_missing_excluded_bullets_are_visible_and_explained(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")

    comparison_source = (project_root / "resume_tailor" / "experience_comparison.py").read_text(encoding="utf-8")
    assert "Restored missing bullet — included" in comparison_source
    assert "Restored missing bullet — not included" in comparison_source
    assert "Restored and included in" in template
    assert "included it in the Word download" in template
    assert "Restored to the structured Draft, but not included in the resume." in template
    assert "Missing from the structured Draft" in template
    assert "restored_missing_included" in template
    assert "restored_missing_excluded" in template
    assert "bullet.inclusion_status == 'excluded_unchanged' %}hidden" in template
    assert "bullet.inclusion_status == 'restored_missing_excluded' %}hidden" not in template
    assert ".bullet-status-badge.restored_missing_included" in styles
    assert ".bullet-status-badge.restored_missing_excluded" in styles
    assert ".restored-missing-alert" in styles


def test_current_structured_proposal_omission_is_defensively_restored():
    result = classify_bullet_inclusion(
        reference_include=True,
        current_include=False,
        reference_text="Tracked more than 100 software defects.",
        current_text="Tracked more than 100 software defects.",
        current_label="Generated Tailored Resume",
        reference_label="Initial Resume",
        reference_present=True,
        current_present=False,
    )

    assert result.status == "restored_missing_included"
    assert result.label == "Automatically restored from source resume"
    assert result.reference_for_diff == "Tracked more than 100 software defects."
    assert result.current_for_diff == "Tracked more than 100 software defects."


def test_missing_proposal_mapping_controls_are_removed_from_editor(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")
    javascript = (project_root / "static" / "app.js").read_text(encoding="utf-8")
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "repair_missing_bullet_proposals(profile, proposal)" in app_source
    assert "Missing from tailored resume" not in template
    assert "Why is this missing?" not in template
    assert "No omission or rewrite decision was recorded." not in template
    assert "Keep omitted" not in template
    assert "Mark as rewritten" not in template
    assert "data-keep-omitted" not in javascript
    assert "data-mark-rewritten" not in javascript
    assert ".bullet-status-badge.unresolved_missing" not in styles

def test_user_identified_rewrite_is_not_classified_as_exclusion():
    result = classify_bullet_inclusion(
        reference_include=True,
        current_include=False,
        reference_text="Led client testing and training.",
        current_text="Led client testing and training.",
        current_label="Generated Tailored Resume",
        current_present=True,
        rewritten_as_id="NAS-10",
        rewritten_text="Led end-to-end testing and trained 30 internal users.",
    )

    assert result.status == "rewritten"
    assert result.label == "Rewritten as NAS-10"
    assert result.current_for_diff == "Led end-to-end testing and trained 30 internal users."
