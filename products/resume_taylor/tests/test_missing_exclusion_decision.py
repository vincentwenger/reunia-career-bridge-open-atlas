from __future__ import annotations

from resume_tailor.proposal_integrity import (
    missing_source_bullet_ids,
    repair_missing_bullet_proposals,
)


def test_missing_bullet_is_automatically_restored(profile, proposal):
    incomplete = proposal.model_copy(deep=True)
    missing_id = incomplete.bullet_proposals[0].source_bullet_id
    incomplete.bullet_proposals = [
        item for item in incomplete.bullet_proposals if item.source_bullet_id != missing_id
    ]

    repaired = repair_missing_bullet_proposals(profile, incomplete)
    repaired_item = next(
        item for item in repaired.bullet_proposals if item.source_bullet_id == missing_id
    )

    assert missing_source_bullet_ids(profile, incomplete) == [missing_id]
    assert missing_source_bullet_ids(profile, repaired) == []
    assert repaired_item.include is True
    assert repaired_item.proposed_text == profile.bullet_lookup()[missing_id]
    assert "Automatically restored from the Candidate Profile" in repaired_item.evidence_note
    assert all(item.source_bullet_id != missing_id for item in incomplete.bullet_proposals)


def test_generation_and_save_paths_repair_missing_bullets(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")
    prompts = (project_root / "resume_tailor" / "prompts.py").read_text(encoding="utf-8")

    assert "repair_missing_bullet_proposals" in app_source
    assert "proposal = repair_missing_bullet_proposals(profile, proposal)" in app_source
    assert "refined = repair_missing_bullet_proposals(confirmed_profile, refined)" in app_source
    assert "proposal = repair_missing_bullet_proposals(profile, proposal)" in app_source
    assert "store_working_proposal(" in app_source
    assert "If you cannot justify an exclusion, include the original source wording" in prompts
    assert "Never omit a bullet record" in prompts


def test_internal_mapping_failure_is_not_shown_to_users(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (project_root / "static" / "app.js").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")

    for text in (
        "Missing from tailored resume",
        "Why is this missing?",
        "No omission or rewrite decision was recorded.",
        "incomplete proposal mapping",
        "Keep omitted",
        "Mark as rewritten",
        "Technical detail:",
    ):
        assert text not in template

    assert "data-keep-omitted" not in script
    assert "data-mark-rewritten" not in script
    assert "unresolved_missing" not in styles
