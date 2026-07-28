"""Stable matching and deduplication for AI-generated audit findings.

Audit wording may vary between independent model runs even when the underlying
concern is unchanged. User ignore decisions therefore cannot rely on a hash of
all generated prose. This module compares the durable parts of a finding:
section, source identifier, referenced record IDs, concern family, and the
meaningful subject terms in the issue and suggested fix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import AuditIssue

_IDENTIFIER_RE = re.compile(r"\b(?:CONF-\d+|R\d+|[A-Z]{2,8}-\d+)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
_CAPITALIZED_PHRASE_RE = re.compile(r"\b[A-Z][A-Za-z0-9+#./-]*(?:\s+[A-Z][A-Za-z0-9+#./-]*)+\b")
_QUOTED_PHRASE_RE = re.compile(r"[\"“”']([^\"“”']{2,80})[\"“”']")

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "candidate", "change", "claim", "current", "directly", "do", "does",
    "for", "from", "has", "have", "in", "initial", "into", "is", "issue", "it", "its", "listed",
    "make", "may", "more", "must", "not", "of", "on", "only", "or", "profile",
    "proposal", "provide", "provided", "related", "remove", "resume", "revise", "conservatively", "follow-on",
    "several", "should", "suggested", "than", "that", "the", "their", "them",
    "these", "this", "to", "update", "use", "used", "using", "verified", "was",
    "were", "which", "with", "wording",
}


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _normalize_token(token: str) -> str:
    value = token.casefold().strip("-./")
    replacements = {
        "unsupported": "support",
        "supported": "support",
        "supporting": "support",
        "supports": "support",
        "invented": "invent",
        "inventing": "invent",
        "invention": "invent",
        "strengthened": "overstate",
        "strengthens": "overstate",
        "overstated": "overstate",
        "overstates": "overstate",
        "overstating": "overstate",
        "inconsistent": "inconsistency",
        "inconsistencies": "inconsistency",
        "duplicated": "duplicate",
        "duplicates": "duplicate",
        "repeated": "repeat",
        "repetitive": "repeat",
        "repetition": "repeat",
        "lengthy": "length",
        "longer": "length",
        "exceeds": "limit",
        "exceed": "limit",
        "exceeded": "limit",
        "missing": "missing",
        "omitted": "missing",
    }
    return replacements.get(value, value)


def _meaningful_tokens(issue: AuditIssue) -> set[str]:
    combined = f"{issue.issue} {issue.suggested_fix}"
    tokens = {
        _normalize_token(token)
        for token in _TOKEN_RE.findall(combined)
    }
    return {
        token
        for token in tokens
        if token and len(token) > 2 and token not in _STOP_WORDS
    }



def _subjects(issue: AuditIssue) -> set[str]:
    """Extract explicit named subjects that distinguish nearby concerns."""
    combined = f"{issue.issue} {issue.suggested_fix}"
    values = set(_CAPITALIZED_PHRASE_RE.findall(combined))
    values.update(_QUOTED_PHRASE_RE.findall(combined))
    normalized = {_normalize_text(value) for value in values}
    generic = {
        "candidate profile",
        "final resume",
        "professional summary",
        "suggested fix",
    }
    return {value for value in normalized if value not in generic}

def _identifiers(issue: AuditIssue) -> set[str]:
    combined = f"{issue.source_id} {issue.issue} {issue.suggested_fix}"
    return {match.upper() for match in _IDENTIFIER_RE.findall(combined)}


def _section(issue: AuditIssue) -> str:
    return _normalize_text(issue.section)


def _source_id(issue: AuditIssue) -> str:
    return _normalize_text(issue.source_id)


def audit_issue_family(issue: AuditIssue) -> str:
    """Classify the durable concern without depending on generated phrasing."""
    section = _section(issue)
    text = _normalize_text(f"{issue.issue} {issue.suggested_fix}")

    if any(term in text for term in ("duplicate", "repetition", "repetitive", "repeated")):
        return "repetition"
    if "keyword stuffing" in text or "keyword-stuffed" in text:
        return "keyword_stuffing"
    if any(term in text for term in ("awkward", "unclear", "readability", "grammar")):
        return "wording_quality"
    if any(term in text for term in ("weak relevance", "less relevant", "not relevant", "relevance")):
        return "relevance"
    if any(term in text for term in ("too long", "too short", "word count", "word limit", "sentence count", "recommended length")):
        return "length_or_count"
    if any(term in text for term in ("status", "rationale", "evidence id", "evidence_ids", "unsupported_requirements")):
        return "evidence_alignment"
    if any(
        term in text
        for term in (
            "unsupported",
            "not supported",
            "not found",
            "invent",
            "overstate",
            "strengthen",
            "stronger than",
            "beyond what",
            "cannot be verified",
            "unverified",
            "unsubstantiated",
            "not substantiated",
            "lacks evidence",
            "no evidence",
            "not documented",
            "undocumented",
        )
    ):
        if "skill" in section or "skill" in text:
            return "skill_evidence"
        if "summary" in section:
            return "summary_evidence"
        if "bullet" in section:
            return "bullet_evidence"
        if "evidence" in section or "requirement" in section:
            return "requirement_evidence"
        return "claim_evidence"
    if "skill" in section:
        return "skills"
    if "summary" in section:
        return "summary"
    if "bullet" in section:
        return "bullets"
    if "evidence" in section or "requirement" in section:
        return "evidence"
    return "generic"


def audit_issues_equivalent(left: AuditIssue, right: AuditIssue) -> bool:
    """Return True when two findings represent the same underlying concern."""
    if _section(left) != _section(right):
        return False

    left_source = _source_id(left)
    right_source = _source_id(right)
    if left_source and right_source and left_source != right_source:
        return False

    left_family = audit_issue_family(left)
    right_family = audit_issue_family(right)
    if left_family != right_family:
        return False

    # Generic findings have no safe semantic anchor. Match them only when their
    # normalized generated text is unchanged.
    if left_family == "generic":
        return (
            _normalize_text(left.issue) == _normalize_text(right.issue)
            and _normalize_text(left.suggested_fix)
            == _normalize_text(right.suggested_fix)
        )

    left_ids = _identifiers(left)
    right_ids = _identifiers(right)
    if left_ids and right_ids and left_ids.isdisjoint(right_ids):
        return False

    left_subjects = _subjects(left)
    right_subjects = _subjects(right)
    if left_subjects and right_subjects and left_subjects.isdisjoint(right_subjects):
        return False

    # A shared explicit source ID plus the same concern family is a strong,
    # durable identity even if the model completely rephrases the explanation.
    if left_source and right_source and left_source == right_source:
        return True

    left_tokens = _meaningful_tokens(left)
    right_tokens = _meaningful_tokens(right)
    if not left_tokens or not right_tokens:
        return bool(left_ids & right_ids)

    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    smaller = min(len(left_tokens), len(right_tokens))
    jaccard = overlap / union if union else 0.0
    containment = overlap / smaller if smaller else 0.0

    # Require at least one subject term beyond the broad family. The lower
    # Jaccard threshold accommodates paraphrases while containment protects
    # concise-vs-detailed restatements of the same concern.
    return overlap >= 2 and (jaccard >= 0.24 or containment >= 0.50)


def _preferred_duplicate(left: AuditIssue, right: AuditIssue) -> AuditIssue:
    """Retain the safest and most actionable form of a duplicate concern."""
    left_score = (
        1 if left.severity == "blocking" else 0,
        1 if left.suggested_fix.strip() else 0,
        len(left.issue.strip()) + len(left.suggested_fix.strip()),
    )
    right_score = (
        1 if right.severity == "blocking" else 0,
        1 if right.suggested_fix.strip() else 0,
        len(right.issue.strip()) + len(right.suggested_fix.strip()),
    )
    return right if right_score > left_score else left


def deduplicate_audit_issues(issues: Iterable[AuditIssue]) -> list[AuditIssue]:
    """Collapse semantic duplicates without discarding a stronger actionable form."""
    unique: list[AuditIssue] = []
    for issue in issues:
        match_index = next(
            (
                index
                for index, existing in enumerate(unique)
                if audit_issues_equivalent(issue, existing)
            ),
            None,
        )
        if match_index is None:
            unique.append(issue)
            continue
        unique[match_index] = _preferred_duplicate(unique[match_index], issue)
    return unique


def filter_ignored_audit_issues(
    issues: Iterable[AuditIssue],
    ignored: Iterable[AuditIssue],
) -> list[AuditIssue]:
    """Deduplicate refreshed findings and suppress stable ignored concerns."""
    ignored_list = list(ignored)
    return [
        issue
        for issue in deduplicate_audit_issues(issues)
        if not any(audit_issues_equivalent(issue, ignored_issue) for ignored_issue in ignored_list)
    ]


def audit_issue_sets_equivalent(
    left: Iterable[AuditIssue],
    right: Iterable[AuditIssue],
) -> bool:
    """Compare two active finding sets without treating paraphrasing as progress."""
    left_unique = deduplicate_audit_issues(left)
    right_unique = deduplicate_audit_issues(right)
    if len(left_unique) != len(right_unique):
        return False

    unmatched = list(right_unique)
    for issue in left_unique:
        match_index = next(
            (
                index
                for index, candidate in enumerate(unmatched)
                if audit_issues_equivalent(issue, candidate)
            ),
            None,
        )
        if match_index is None:
            return False
        unmatched.pop(match_index)
    return not unmatched
