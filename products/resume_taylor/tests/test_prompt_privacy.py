from __future__ import annotations

from resume_tailor.models import AuditIssue, CandidateAnswer
from resume_tailor.prompts import (
    build_audit_fix_prompt,
    build_audit_prompt,
    build_proposal_prompt,
    build_refinement_prompt,
)


def test_contact_details_are_excluded_from_model_prompts(profile, analysis, proposal):
    proposal_prompt = build_proposal_prompt(profile, analysis)
    audit_prompt = build_audit_prompt(profile, analysis, proposal)
    refinement_prompt = build_refinement_prompt(
        profile,
        analysis,
        proposal,
        [
            CandidateAnswer(
                question_id="Q1",
                requirement_id="R1",
                answer_type="yes_no",
                yes_no=False,
            )
        ],
    )
    fix_prompt = build_audit_fix_prompt(
        profile,
        analysis,
        proposal,
        [
            AuditIssue(
                severity="blocking",
                section="Evidence matches",
                source_id="R1",
                issue="The evidence rationale overstates the source.",
                suggested_fix="Change the status to partial and use conservative wording.",
            )
        ],
    )
    for prompt in (proposal_prompt, audit_prompt, refinement_prompt, fix_prompt):
        assert profile.contact.phone not in prompt
        assert profile.contact.email not in prompt


def test_audit_fix_prompt_contains_only_actionable_findings(profile, analysis, proposal):
    prompt = build_audit_fix_prompt(
        profile,
        analysis,
        proposal,
        [
            AuditIssue(
                severity="blocking",
                section="Evidence matches",
                source_id="R1",
                issue="The match is too strong.",
                suggested_fix="Change the status to partial.",
            ),
            AuditIssue(
                severity="warning",
                section="Tone",
                issue="A sentence is lengthy.",
                suggested_fix="",
            ),
        ],
    )

    assert "Change the status to partial" in prompt
    assert "The match is too strong" in prompt
    assert "A sentence is lengthy" not in prompt
    assert "Return the complete corrected TailoringProposal" in prompt


def test_audit_prompt_requires_exact_wording_for_rephrase_findings(
    profile, analysis, proposal
):
    prompt = build_audit_prompt(profile, analysis, proposal)

    assert "quote the exact current phrase" in prompt
    assert "complete replacement summary" in prompt
    assert 'no "Replace X with Y" wrapper is required' in prompt
    assert "Do not use vague instructions" in prompt


def test_proposal_prompt_includes_balanced_skill_targets(profile, analysis):
    from resume_tailor.prompts import build_proposal_prompt

    prompt = build_proposal_prompt(profile, analysis)

    assert "aim for 20-30 total" in prompt
    assert "Hard Skills 8-14" in prompt
    assert "Soft Skills 3-5" in prompt
    assert "Tools & Software 6-12" in prompt
    assert "Industry Knowledge 4-8" in prompt
    assert "original Candidate Profile category" in prompt
