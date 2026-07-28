from __future__ import annotations

from resume_tailor.confirmation import (
    build_profile_with_candidate_answers,
    confirmation_dispositions,
    ensure_confirmed_answers_visible,
)
from resume_tailor.models import CandidateAnswer, CandidateQuestion
from resume_tailor.proposal_integrity import repair_missing_bullet_proposals


def _confirmed_profile(profile, analysis, *, placement="auto"):
    question = CandidateQuestion(
        id="Q1",
        requirement_id="R2",
        question="Describe your Python data-pipeline experience.",
        answer_type="long_text",
    )
    answer = CandidateAnswer(
        question_id="Q1",
        question=question.question,
        requirement_id="R2",
        answer_type="long_text",
        text="Built Python data pipelines for regulatory reporting transformations.",
        experience_id="nasdaq",
        placement=placement,
    )
    confirmed = build_profile_with_candidate_answers(
        profile, analysis, [question], [answer]
    )
    return confirmed, answer


def test_affirmative_answer_becomes_role_specific_source_bullet(profile, analysis):
    confirmed, _ = _confirmed_profile(profile, analysis)

    evidence = confirmed.supplemental_evidence[-1]
    assert evidence.id == "CONF-Q1"
    assert evidence.experience_id == "nasdaq"
    assert evidence.source_bullet_id == "NAS-CONF-01"
    assert evidence.placement == "auto"
    assert confirmed.experience_lookup()["nasdaq"].bullets[-1].id == "NAS-CONF-01"


def test_new_bullet_preference_forces_visible_confirmed_bullet(profile, analysis, proposal):
    confirmed, answer = _confirmed_profile(profile, analysis, placement="new_bullet")
    refined = repair_missing_bullet_proposals(confirmed, proposal)
    candidate = next(
        item for item in refined.bullet_proposals if item.source_bullet_id == "NAS-CONF-01"
    )
    candidate.include = False
    candidate.proposed_text = ""

    visible = ensure_confirmed_answers_visible(confirmed, refined)
    candidate = next(
        item for item in visible.bullet_proposals if item.source_bullet_id == "NAS-CONF-01"
    )

    assert candidate.include is True
    assert candidate.proposed_text.startswith("Built Python data pipelines")
    assert "CONF-Q1" in candidate.evidence_note
    rows = confirmation_dispositions(confirmed, visible, [answer])
    assert rows[0]["result"] == "Added new bullet NAS-CONF-01"


def test_update_existing_preference_uses_cited_existing_bullet(profile, analysis, proposal):
    confirmed, answer = _confirmed_profile(profile, analysis, placement="update_existing")
    refined = repair_missing_bullet_proposals(confirmed, proposal)
    existing = next(
        item for item in refined.bullet_proposals if item.source_bullet_id == "NAS-01"
    )
    existing.include = True
    existing.proposed_text = (
        "Built Python data pipelines for regulatory reporting transformations and delivery."
    )
    existing.evidence_note = "Supported by NAS-01, NAS-CONF-01, and CONF-Q1."

    visible = ensure_confirmed_answers_visible(confirmed, refined)
    candidate = next(
        item for item in visible.bullet_proposals if item.source_bullet_id == "NAS-CONF-01"
    )

    assert candidate.include is False
    assert "represented by NAS-01" in candidate.evidence_note
    rows = confirmation_dispositions(confirmed, visible, [answer])
    assert rows[0]["result"] == "Updated existing bullet NAS-01"


def test_auto_falls_back_to_new_visible_bullet_when_model_does_not_use_answer(
    profile, analysis, proposal
):
    confirmed, _ = _confirmed_profile(profile, analysis, placement="auto")
    refined = repair_missing_bullet_proposals(confirmed, proposal)
    candidate = next(
        item for item in refined.bullet_proposals if item.source_bullet_id == "NAS-CONF-01"
    )
    candidate.include = False

    visible = ensure_confirmed_answers_visible(confirmed, refined)

    candidate = next(
        item for item in visible.bullet_proposals if item.source_bullet_id == "NAS-CONF-01"
    )
    assert candidate.include is True


def test_confirmation_form_offers_role_and_placement_dropdowns(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "Which job was this part of?" in template
    assert 'name="experience__{{ q.id }}"' in template
    assert "Let the application decide" in template
    assert "Update the closest existing bullet" in template
    assert "Add a new bullet" in template
    assert "How your confirmed experience was used" in template


def test_validation_blocks_confirmed_answer_that_disappears(profile, analysis, proposal):
    from resume_tailor.validation import validate_proposal

    confirmed, _ = _confirmed_profile(profile, analysis, placement="auto")
    refined = repair_missing_bullet_proposals(confirmed, proposal)
    candidate = next(
        item for item in refined.bullet_proposals if item.source_bullet_id == "NAS-CONF-01"
    )
    candidate.include = False
    candidate.evidence_note = "Not selected."

    issues = validate_proposal(confirmed, analysis, refined)

    assert any(
        issue.section == "Confirmed Experience"
        and issue.severity == "blocking"
        and issue.source_id == "NAS-CONF-01"
        for issue in issues
    )


def test_deterministic_repairs_restore_hidden_confirmed_answer(profile, analysis, proposal):
    from resume_tailor.deterministic_fixes import apply_deterministic_repairs

    confirmed, _ = _confirmed_profile(profile, analysis, placement="auto")
    refined = repair_missing_bullet_proposals(confirmed, proposal)
    candidate = next(
        item for item in refined.bullet_proposals if item.source_bullet_id == "NAS-CONF-01"
    )
    candidate.include = False
    candidate.evidence_note = "Not selected."

    repaired = apply_deterministic_repairs(confirmed, analysis, refined)
    candidate = next(
        item for item in repaired.bullet_proposals if item.source_bullet_id == "NAS-CONF-01"
    )

    assert candidate.include is True
