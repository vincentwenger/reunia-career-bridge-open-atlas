from __future__ import annotations

import pytest

from resume_tailor.models import AuditIssue, EvidenceMatch, JobRequirement, ProposalAudit
from resume_tailor.validation import (
    reconcile_audit_with_deterministic_rules,
    sentence_count,
    word_count,
)


def test_valid_summary_removes_false_ai_length_finding(proposal):
    assert 50 <= word_count(proposal.professional_summary) <= 80

    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="professional_summary",
                issue="Professional summary exceeds the recommended length of 50 to 80 words.",
                suggested_fix="Trim the professional summary to fit within the 50 to 80 word limit.",
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(audit, proposal)

    assert reconciled.issues == []
    assert reconciled.verified_strengths == [
        "Professional summary structure is within the required limits: "
        f"{word_count(proposal.professional_summary)} words and "
        f"{sentence_count(proposal.professional_summary)} sentences."
    ]


def test_valid_summary_removes_false_ai_sentence_count_finding(proposal):
    assert sentence_count(proposal.professional_summary) in (3, 4)

    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="Professional Summary",
                issue="The professional summary does not use 3 or 4 sentences.",
                suggested_fix="Rewrite it as three or four sentences.",
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(audit, proposal)

    assert reconciled.issues == []


def test_non_measurable_summary_finding_is_preserved(proposal):
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="Professional Summary",
                issue="The summary claims leadership scope that is not supported by the profile.",
                suggested_fix="Remove or qualify the unsupported leadership claim.",
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(audit, proposal)

    assert reconciled.issues == audit.issues
    assert reconciled.verified_strengths == []


def test_invalid_length_finding_is_preserved(proposal):
    proposal.professional_summary = "Too short."
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="professional_summary",
                issue="Professional summary is below the 50 to 80 word range.",
                suggested_fix="Expand the summary to at least 50 words.",
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(audit, proposal)

    assert reconciled.issues == audit.issues
    assert reconciled.verified_strengths == []


def test_final_audit_route_reconciles_ai_findings_before_normalization(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "reconcile_audit_with_deterministic_rules(" in app_source
    assert "word_count(comparison_summary)" in app_source
    assert "sentence_count(proposal.professional_summary)" in app_source
    assert "def _run_reconciled_evidence_audit(" in app_source
    assert "audit_ai.audit_proposal(profile, analysis, proposal)" in app_source


def test_valid_role_counts_remove_false_ai_bullet_count_finding(profile, proposal):
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="bullet_proposals",
                issue=(
                    "The proposal has bullet counts that do not align with recommendations "
                    "for the most recent role (6-7) and second role (3-4)."
                ),
                suggested_fix=(
                    "Remove or combine less relevant bullet points in accordance with the "
                    "recommended count."
                ),
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(audit, proposal, profile)

    assert reconciled.issues == []
    assert reconciled.verified_strengths == [
        "Professional experience bullet counts are within the required ranges: "
        "Most recent role (Nasdaq): 7 included bullets, required 6-7; "
        "Second role (Aviva): 4 included bullets, required 3-4."
    ]


def test_invalid_role_count_finding_is_preserved(profile, proposal):
    first_role_ids = {bullet.id for bullet in profile.experiences[0].bullets[:2]}
    for proposal_item in proposal.bullet_proposals:
        if proposal_item.source_bullet_id in first_role_ids:
            proposal_item.include = False
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="bullet_proposals",
                issue="The most recent role does not have the recommended bullet count of 6-7.",
                suggested_fix="Select more relevant bullets.",
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(audit, proposal, profile)

    assert reconciled.issues == audit.issues
    assert reconciled.verified_strengths == []


def test_false_bullet_word_count_finding_is_removed(profile, proposal):
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="warning",
                section="bullet_proposals",
                issue="One bullet has a word count above 55 words.",
                suggested_fix="Shorten the bullet.",
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(audit, proposal, profile)

    assert reconciled.issues == []
    assert reconciled.verified_strengths == [
        "Objective resume constraints were rechecked against the current Final draft "
        "and any contradicted AI findings were removed."
    ]


def test_valid_sixteen_verified_skills_remove_combined_false_finding(
    profile, analysis, proposal
):
    proposal.skills.hard_skills.extend(
        ["Release Planning", "API Development", "Requirements Gathering", "Risk Assessment"]
    )
    assert proposal.skills.total_count() == 16

    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="skills",
                issue=(
                    "Total counted skills exceed 16 as enforced by the count limit, "
                    "with several skills listed not found in the verified candidate profile "
                    "(e.g., Release Planning, API Development)."
                ),
                suggested_fix=(
                    "Limit the number of skills to no more than 16, prioritizing those "
                    "that are directly relevant and supported in the candidate profile."
                ),
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(
        audit, proposal, profile, analysis
    )

    assert reconciled.issues == []
    assert reconciled.verified_strengths == [
        "Skills were verified deterministically: 16 selected (maximum 30), all selected "
        "skills are present in the verified candidate profile, and no duplicates are present."
    ]


def test_genuine_unverified_skill_uses_exact_deterministic_finding(
    profile, analysis, proposal
):
    proposal.skills.hard_skills.append("Invented Platform Administration")
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="skills",
                issue="One selected skill is unsupported by the candidate profile.",
                suggested_fix="Remove the unsupported skill.",
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(
        audit, proposal, profile, analysis
    )

    assert len(reconciled.issues) == 1
    assert reconciled.issues[0].section == "Skills"
    assert reconciled.issues[0].source_id == "Invented Platform Administration"
    assert reconciled.issues[0].issue == (
        "'Invented Platform Administration' is not in the candidate's verified skills."
    )


@pytest.mark.parametrize(
    "section,issue,suggested_fix",
    [
        (
            "skills",
            "The proposal contains duplicate skills.",
            "Remove duplicate skills.",
        ),
        (
            "bullet_proposals",
            "The proposal is missing source bullet proposals.",
            "Return one proposal for every source bullet.",
        ),
        (
            "bullet_proposals",
            "A proposed bullet is empty.",
            "Restore the source bullet.",
        ),
        (
            "bullet_proposals",
            "A bullet introduces new numbers not present in its source bullet.",
            "Use only numbers present in the source bullet.",
        ),
        (
            "bullet_proposals",
            "A bullet references unknown job requirement IDs.",
            "Use requirement IDs from the job analysis.",
        ),
        (
            "evidence_matrix",
            "The evidence matrix is missing evidence decisions for job requirements.",
            "Return one evidence decision for every job requirement.",
        ),
    ],
)
def test_other_valid_objective_rules_remove_false_ai_findings(
    profile, analysis, proposal, section, issue, suggested_fix
):
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section=section,
                issue=issue,
                suggested_fix=suggested_fix,
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(
        audit, proposal, profile, analysis
    )

    assert reconciled.issues == []


def test_semantic_evidence_finding_is_still_preserved(profile, analysis, proposal):
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="professional_summary",
                issue=(
                    "The summary strengthens the candidate's leadership scope beyond "
                    "what is documented in the profile."
                ),
                suggested_fix="Qualify the leadership wording to match the source evidence.",
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(
        audit, proposal, profile, analysis
    )

    assert reconciled.issues == audit.issues


def test_bullet_finding_with_requirement_id_is_discarded(
    profile, analysis, proposal
):
    analysis.requirements.append(
        JobRequirement(
            id="R7",
            category="responsibility",
            priority="important",
            requirement="Develop software for U.S. Federal Reserve Bank reports",
            keywords=["software development", "U.S. Federal Reserve Bank reports"],
        )
    )
    proposal.evidence_matches.append(
        EvidenceMatch(
            requirement_id="R7",
            status="unsupported",
            evidence_ids=[],
            rationale="The candidate profile does not document this report scope.",
        )
    )
    proposal.unsupported_requirements.append(
        "R7: Develop software for U.S. Federal Reserve Bank reports"
    )
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="warning",
                section="bullet_proposals",
                source_id="R7",
                issue=(
                    "Lacks specific evidence of software development for U.S. "
                    "Federal Reserve Bank reports."
                ),
                suggested_fix=(
                    "Remove any references to software development for U.S. "
                    "Federal Reserve Bank reports."
                ),
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(
        audit, proposal, profile, analysis
    )

    assert reconciled.issues == []


def test_removal_finding_for_absent_named_claim_is_discarded(
    profile, analysis, proposal
):
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="warning",
                section="professional_summary",
                issue="The resume does not support Federal Reserve Bank report development.",
                suggested_fix=(
                    "Remove any references to software development for U.S. "
                    "Federal Reserve Bank reports."
                ),
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(
        audit, proposal, profile, analysis
    )

    assert reconciled.issues == []


def test_removal_finding_is_preserved_when_named_claim_is_visible(
    profile, analysis, proposal
):
    target = next(
        item
        for item in proposal.bullet_proposals
        if item.source_bullet_id == "NAS-01"
    )
    target.proposed_text += (
        " Developed software for U.S. Federal Reserve Bank reports."
    )
    audit = ProposalAudit(
        passed=False,
        issues=[
            AuditIssue(
                severity="blocking",
                section="bullet_proposals",
                source_id="NAS-01",
                issue="The bullet adds unsupported Federal Reserve Bank report scope.",
                suggested_fix=(
                    "Remove any references to software development for U.S. "
                    "Federal Reserve Bank reports."
                ),
            )
        ],
        verified_strengths=[],
    )

    reconciled = reconcile_audit_with_deterministic_rules(
        audit, proposal, profile, analysis
    )

    assert reconciled.issues == audit.issues


def test_audit_prompt_declares_deterministic_results_as_source_of_truth(
    profile, analysis, proposal
):
    from resume_tailor.prompts import build_audit_prompt

    prompt = build_audit_prompt(profile, analysis, proposal)

    assert "DETERMINISTIC RESULTS — SOURCE OF TRUTH" in prompt
    assert '"skills_total": 12' in prompt
    assert '"professional_summary_words": 50' in prompt
    assert "do not recount or challenge summary words or sentences" in " ".join(prompt.casefold().split())
    assert "Report only the semantic finding" in prompt
    normalized_prompt = " ".join(prompt.split())
    assert "Never use a job requirement ID such as R7" in normalized_prompt
    assert "is not itself a resume defect" in prompt
    assert "confirm that the named claim or wording is present" in prompt
