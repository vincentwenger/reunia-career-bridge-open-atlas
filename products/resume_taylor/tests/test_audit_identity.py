from __future__ import annotations

from resume_tailor.audit_identity import (
    audit_issue_sets_equivalent,
    audit_issues_equivalent,
    deduplicate_audit_issues,
    filter_ignored_audit_issues,
)
from resume_tailor.models import AuditIssue


def issue(
    section: str,
    text: str,
    fix: str,
    *,
    source_id: str = "",
) -> AuditIssue:
    return AuditIssue(
        severity="blocking",
        section=section,
        source_id=source_id,
        issue=text,
        suggested_fix=fix,
    )


def test_reworded_ignored_skill_finding_remains_suppressed():
    ignored = issue(
        "skills",
        "Release Planning and API Development are not supported by the verified candidate profile.",
        "Remove the unsupported skills.",
    )
    refreshed = issue(
        "Skills",
        "Several selected skills, including API Development and Release Planning, cannot be verified in the source evidence.",
        "Keep only profile-supported skills.",
    )

    assert audit_issues_equivalent(ignored, refreshed)
    assert filter_ignored_audit_issues([refreshed], [ignored]) == []


def test_distinct_named_skill_findings_are_not_suppressed_together():
    ignored = issue(
        "skills",
        "Release Planning is unsupported by the candidate profile.",
        "Remove Release Planning.",
    )
    distinct = issue(
        "skills",
        "API Development is unsupported by the candidate profile.",
        "Remove API Development.",
    )

    assert not audit_issues_equivalent(ignored, distinct)
    assert filter_ignored_audit_issues([distinct], [ignored]) == [distinct]


def test_reworded_evidence_finding_with_same_source_id_is_stable():
    ignored = issue(
        "Evidence matches",
        "The rationale overstates the available source evidence.",
        "Downgrade the match to partial and use conservative wording.",
        source_id="R1",
    )
    refreshed = issue(
        "evidence_matches",
        "The R1 evidence status is stronger than the documented support.",
        "Use partial status and align the rationale with the evidence.",
        source_id="R1",
    )

    assert audit_issues_equivalent(ignored, refreshed)


def test_rephrased_duplicates_are_collapsed_before_counting():
    first = issue(
        "professional_summary",
        "The summary strengthens the candidate's leadership scope beyond the profile.",
        "Qualify the leadership wording to match the source evidence.",
    )
    duplicate = issue(
        "Professional Summary",
        "Leadership scope in the summary is overstated compared with documented evidence.",
        "Use narrower leadership language supported by the profile.",
    )

    assert len(deduplicate_audit_issues([first, duplicate])) == 1
    assert audit_issue_sets_equivalent([first], [duplicate])


def test_generic_follow_on_findings_are_not_treated_as_the_same_issue():
    first = issue(
        "professional_summary",
        "Follow-on issue 1",
        "Revise conservatively.",
    )
    second = issue(
        "professional_summary",
        "Follow-on issue 2",
        "Revise conservatively.",
    )

    assert not audit_issues_equivalent(first, second)


def test_duplicate_collapse_keeps_blocking_actionable_version():
    warning = AuditIssue(
        severity="warning",
        section="skills",
        issue="Release Planning is not supported by the candidate profile.",
        suggested_fix="",
    )
    blocking = AuditIssue(
        severity="blocking",
        section="Skills",
        issue="Release Planning cannot be verified in the profile evidence.",
        suggested_fix="Remove Release Planning.",
    )

    result = deduplicate_audit_issues([warning, blocking])
    assert len(result) == 1
    assert result[0].severity == "blocking"
    assert result[0].suggested_fix == "Remove Release Planning."
