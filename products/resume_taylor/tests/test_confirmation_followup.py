from __future__ import annotations

from resume_tailor.confirmation_followup import (
    MAX_TARGETED_FOLLOW_UP_QUESTIONS,
    MAX_TARGETED_FOLLOW_UP_ROUNDS,
    audit_issue_requires_candidate_information,
    build_targeted_follow_up_questions,
    partition_targeted_follow_up_issues,
    split_post_confirmation_issues,
)
from resume_tailor.models import AuditIssue


def test_unsupported_visible_claim_requires_one_specific_candidate_follow_up(proposal):
    issue = AuditIssue(
        severity="blocking",
        section="bullet_proposals",
        source_id="NAS-01",
        issue="The bullet overstates production deployment ownership beyond the available evidence.",
        suggested_fix='Replace the wording with "Supported deployment activities."',
    )

    assert audit_issue_requires_candidate_information(issue) is True
    questions = build_targeted_follow_up_questions(
        [issue], proposal, round_number=3
    )

    assert MAX_TARGETED_FOLLOW_UP_ROUNDS == 1
    assert len(questions) == 1
    assert questions[0].id == "FQ1-1"
    assert questions[0].answer_type == "yes_no_with_details"
    assert questions[0].requirement_id == "R1"
    assert questions[0].source_id == "NAS-01"
    assert "NAS-01" in questions[0].question
    assert next(
        item.proposed_text
        for item in proposal.bullet_proposals
        if item.source_bullet_id == "NAS-01"
    ) in questions[0].question
    assert "missing factual evidence" not in questions[0].question
    assert "Choose No" in questions[0].help_text
    assert "Unsupported responsibility claim" not in questions[0].question


def test_follow_up_question_set_is_capped_and_remaining_items_are_conservative(proposal):
    issues = [
        AuditIssue(
            severity="blocking",
            section="bullet_proposals",
            source_id=f"NAS-{index:02d}",
            issue=f"Unsupported responsibility claim {index}.",
            suggested_fix=f'Remove unsupported responsibility {index}.',
        )
        for index in range(1, 8)
    ]

    selected, conservative = partition_targeted_follow_up_issues(issues)
    questions = build_targeted_follow_up_questions(
        issues, proposal, round_number=1
    )

    assert MAX_TARGETED_FOLLOW_UP_QUESTIONS == 3
    assert len(selected) == 3
    assert len(conservative) == 4
    assert len(questions) == 3


def test_writing_quality_is_auto_fixable_not_candidate_question():
    issue = AuditIssue(
        severity="warning",
        section="professional_summary",
        source_id="summary",
        issue="The phrase is awkward and repetitive.",
        suggested_fix='Replace "and and" with "and".',
    )

    candidate_needed, auto_fixable = split_post_confirmation_issues([issue])

    assert candidate_needed == []
    assert auto_fixable == [issue]


def test_evidence_metadata_alignment_is_application_fixable():
    issue = AuditIssue(
        severity="blocking",
        section="Evidence",
        source_id="R2",
        issue="The evidence status and evidence IDs are inconsistent.",
        suggested_fix="Align the evidence status with the cited source IDs.",
    )

    candidate_needed, auto_fixable = split_post_confirmation_issues([issue])

    assert candidate_needed == []
    assert auto_fixable == [issue]


def test_follow_up_ui_explains_single_final_evidence_check(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "Targeted evidence follow-up" in template
    assert "Apply final evidence answers" in template
    assert "This is the only follow-up round" in template
    assert "limited to three blocking questions" in template
    assert "no additional follow-up round will be created" in template
    assert "The related job is preselected" in template


def test_confirmation_route_caps_questions_and_later_stages_never_reopen_step_two(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "_run_post_confirmation_evidence_review" in source
    assert "build_targeted_follow_up_questions" in source
    assert "MAX_TARGETED_FOLLOW_UP_ROUNDS" in source
    assert "current.confirmation_follow_up_round" in source
    assert "allow_candidate_questions=(" in source
    assert "MAX_TARGETED_FOLLOW_UP_ROUNDS" in source
    assert "remaining uncertainty will use safer source-backed wording automatically" in source
    assert "return to Step 2 as a small targeted follow-up round" not in source



def test_candidate_fallback_uses_verified_string_source(profile, project_root):
    """The confirmation fallback must not call .text on bullet_lookup() strings."""
    from resume_tailor.profile_io import candidate_bullet_text

    source_id = "NAS-01"
    source_lookup = profile.bullet_lookup()

    assert isinstance(source_lookup[source_id], str)
    assert candidate_bullet_text(profile, source_id) == source_lookup[source_id]

    app_source = (project_root / "app.py").read_text(encoding="utf-8")
    fallback_start = app_source.index("def _fallback_candidate_issue_to_verified_source")
    fallback_end = app_source.index("def _conservatively_resolve_candidate_findings")
    fallback_source = app_source[fallback_start:fallback_end]

    assert "candidate_bullet_text(profile, source_id)" in fallback_source
    assert "source_lookup[source_id].text" not in fallback_source
