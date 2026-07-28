from resume_tailor.evidence_fixes import (
    apply_concrete_individual_audit_rephrase,
    apply_concrete_professional_summary_rephrase,
    concrete_bullet_rephrase,
    concrete_professional_summary_rephrase,
)
from resume_tailor.models import AuditIssue
from resume_tailor.validation import validate_proposal, word_count


def test_concrete_summary_phrase_rephrase_is_extracted_and_applied(proposal):
    issue = AuditIssue(
        severity="warning",
        section="professional_summary",
        issue=(
            "The phrase 'delivering financial-services and regulatory-reporting "
            "solutions' is awkward."
        ),
        suggested_fix=(
            "Rephrase to: 'specializing in financial-services regulatory reporting "
            "solutions' for clarity."
        ),
    )

    plan = concrete_professional_summary_rephrase(
        issue, proposal.professional_summary
    )
    revised = apply_concrete_professional_summary_rephrase(proposal, issue)

    assert plan is not None
    assert plan.replace_entire_field is False
    assert "specializing in financial-services regulatory reporting solutions" in revised.professional_summary
    assert "delivering financial-services and regulatory-reporting solutions" not in revised.professional_summary
    assert revised.skills == proposal.skills
    assert revised.bullet_proposals == proposal.bullet_proposals


def test_vague_summary_recommendation_is_not_automatically_actionable(proposal):
    issue = AuditIssue(
        severity="warning",
        section="professional_summary",
        issue=(
            "The phrase 'project management in the financial industry' is slightly "
            "repetitive and could be more concise."
        ),
        suggested_fix=(
            "Rephrase to focus on direct experiences relevant to the targeted role "
            "to improve relevance."
        ),
    )

    assert concrete_professional_summary_rephrase(
        issue, proposal.professional_summary
    ) is None


def test_complete_quoted_summary_replacement_is_supported(proposal):
    replacement = (
        "Experienced software engineer specializing in regulatory reporting solutions. "
        "Builds data pipelines and AI-enabled workflows for regulated environments. "
        "Combines engineering delivery with IT audit and process improvement."
    )
    issue = AuditIssue(
        severity="warning",
        section="professional_summary",
        issue="The professional summary could be more direct.",
        suggested_fix=f'Replace the professional summary with: "{replacement}"',
    )

    plan = concrete_professional_summary_rephrase(
        issue, proposal.professional_summary
    )
    revised = apply_concrete_professional_summary_rephrase(proposal, issue)

    assert plan is not None
    assert plan.replace_entire_field is True
    assert revised.professional_summary == replacement


def test_standalone_quoted_complete_summary_is_actionable(profile, analysis, proposal):
    replacement = (
        "Experienced software engineer with over 15 years in data science and "
        "engineering, including 12 years in the financial industry, specializing "
        "in regulatory reporting solutions. Expanded expertise through a "
        "Professional Certificate in Machine Learning and Artificial Intelligence "
        "from UC Berkeley. Hands-on experience with data pipelines, feature "
        "engineering, preprocessing, model evaluation, and AI solution development."
    )
    issue = AuditIssue(
        severity="blocking",
        section="professional_summary",
        source_id="none",
        issue=(
            "Unsupported claim regarding enhancement through AI and IT audit "
            "experience in summary."
        ),
        suggested_fix=f'"{replacement}"',
    )

    plan = concrete_professional_summary_rephrase(
        issue, proposal.professional_summary
    )
    revised = apply_concrete_professional_summary_rephrase(proposal, issue)

    assert plan is not None
    assert plan.replace_entire_field is True
    assert revised.professional_summary == replacement
    assert revised.skills == proposal.skills
    assert revised.bullet_proposals == proposal.bullet_proposals
    assert not [
        item
        for item in validate_proposal(profile, analysis, revised)
        if item.severity == "blocking"
    ]


def test_short_standalone_quote_does_not_replace_entire_summary(proposal):
    issue = AuditIssue(
        severity="warning",
        section="professional_summary",
        issue="A phrase could be clearer.",
        suggested_fix='"Specializing in regulatory reporting solutions."',
    )

    assert concrete_professional_summary_rephrase(
        issue, proposal.professional_summary
    ) is None


def test_concrete_bullet_prefix_replacement_is_extracted_and_applied(proposal):
    target = next(
        item for item in proposal.bullet_proposals if item.source_bullet_id == "NAS-01"
    )
    target.proposed_text = (
        "Led the implementation of the SEC Form PF regulatory reporting solution "
        "within the Axiom team, developing and executing a delivery and testing "
        "plan, resulting in on-time compliance for the mid-June 2025 deadline"
    )
    before_other = {
        item.source_bullet_id: item.model_copy(deep=True)
        for item in proposal.bullet_proposals
        if item.source_bullet_id != "NAS-01"
    }
    issue = AuditIssue(
        severity="blocking",
        section="bullet_proposals",
        source_id="NAS-01",
        issue=(
            "The proposed bullet 'Led the implementation of the SEC Form PF "
            "regulatory reporting solution within the Axiom team...' lacks "
            "specific evidence that supports Axiom development work."
        ),
        suggested_fix=(
            "Replace 'Led the implementation of the SEC Form PF regulatory "
            "reporting solution within the Axiom team...' with 'Led the end-to-end "
            "implementation of the SEC Form PF regulatory reporting solution within "
            "the Axiom Solutions team...' to maintain direct relevance."
        ),
    )

    plan = concrete_bullet_rephrase(issue, proposal)
    revised = apply_concrete_individual_audit_rephrase(proposal, issue)
    revised_target = next(
        item for item in revised.bullet_proposals if item.source_bullet_id == "NAS-01"
    )

    assert plan is not None
    assert plan.field_name == "bullet_proposals"
    assert plan.source_bullet_id == "NAS-01"
    assert plan.match_at_start is True
    assert revised_target.proposed_text == (
        "Led the end-to-end implementation of the SEC Form PF regulatory reporting "
        "solution within the Axiom Solutions team, developing and executing a "
        "delivery and testing plan, resulting in on-time compliance for the "
        "mid-June 2025 deadline"
    )
    for source_id, original in before_other.items():
        revised_other = next(
            item for item in revised.bullet_proposals if item.source_bullet_id == source_id
        )
        assert revised_other == original


def test_bullet_replacement_requires_real_target_id(proposal):
    issue = AuditIssue(
        severity="blocking",
        section="bullet_proposals",
        source_id="R7",
        issue="The proposed bullet 'Led the implementation...' is unsupported.",
        suggested_fix=(
            "Replace 'Led the implementation...' with "
            "'Led the end-to-end implementation...'"
        ),
    )

    assert concrete_bullet_rephrase(issue, proposal) is None


def test_bullet_replacement_rejects_unmatched_abbreviated_text(proposal):
    issue = AuditIssue(
        severity="warning",
        section="bullet_proposals",
        source_id="NAS-01",
        issue="The proposed bullet 'Managed an unrelated project...' is imprecise.",
        suggested_fix=(
            "Replace 'Managed an unrelated project...' with "
            "'Led a verified project...'"
        ),
    )

    assert concrete_bullet_rephrase(issue, proposal) is None
