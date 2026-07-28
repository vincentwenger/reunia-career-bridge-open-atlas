from __future__ import annotations

from resume_tailor.validation import validate_proposal


def test_valid_proposal_has_no_blocking_issues(profile, analysis, proposal):
    issues = validate_proposal(profile, analysis, proposal)
    assert not [issue for issue in issues if issue.severity == "blocking"]


def test_new_number_is_blocked(profile, analysis, proposal):
    target = next(item for item in proposal.bullet_proposals if item.source_bullet_id == "NAS-01")
    target.proposed_text += " and supported 99 additional reports"
    issues = validate_proposal(profile, analysis, proposal)
    assert any(
        issue.severity == "blocking"
        and issue.source_id == "NAS-01"
        and "99" in issue.issue
        for issue in issues
    )


def test_unverified_skill_is_blocked(profile, analysis, proposal):
    proposal.skills.tools_software.append("User Defined Functions")
    issues = validate_proposal(profile, analysis, proposal)
    assert any("User Defined Functions" in issue.issue for issue in issues)


def test_wrong_bullet_count_is_blocked(profile, analysis, proposal):
    for item in proposal.bullet_proposals:
        if item.source_bullet_id.startswith("NAS-"):
            item.include = False
    issues = validate_proposal(profile, analysis, proposal)
    assert any(issue.source_id == "nasdaq" and issue.severity == "blocking" for issue in issues)


def test_summary_length_is_enforced(profile, analysis, proposal):
    proposal.professional_summary = "Experienced engineer."
    issues = validate_proposal(profile, analysis, proposal)
    assert any(issue.section == "Professional Summary" and issue.severity == "blocking" for issue in issues)


def test_adjacent_repeated_word_in_summary_is_blocking(profile, analysis, proposal):
    proposal.professional_summary = proposal.professional_summary.replace(
        "and client training", "and and client training"
    )

    issues = validate_proposal(profile, analysis, proposal)

    assert any(
        issue.severity == "blocking"
        and issue.section == "Professional Summary"
        and "'and and'" in issue.issue
        for issue in issues
    )


def test_adjacent_repeated_word_in_included_bullet_is_blocking(
    profile, analysis, proposal
):
    target = next(
        item for item in proposal.bullet_proposals if item.source_bullet_id == "NAS-01"
    )
    target.proposed_text = target.proposed_text.replace("the", "the the", 1)

    issues = validate_proposal(profile, analysis, proposal)

    assert any(
        issue.severity == "blocking"
        and issue.source_id == "NAS-01"
        and "'the the'" in issue.issue
        for issue in issues
    )
