"""Deterministic evidence-review wording fixes used during Step 3.

Only exact, source-scoped replacements are accepted. Vague recommendations remain
manual so the application cannot broaden a claim or edit unrelated resume content.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .models import AuditIssue, TailoringProposal
from .validation import sentence_count, word_count


@dataclass(frozen=True)
class ConcreteRephraseFix:
    """A deterministic wording edit extracted from an audit recommendation."""

    field_name: str
    replacement_text: str
    original_text: str = ""
    replace_entire_field: bool = False
    source_bullet_id: str = ""
    match_at_start: bool = False


def _quoted_values(value: str) -> list[str]:
    """Return distinct straight or curly quoted values in reading order."""
    matches: list[tuple[int, str]] = []
    patterns = (
        r'"([^"\n]+)"',
        r"'([^'\n]+)'",
        r"“([^”\n]+)”",
        r"‘([^’\n]+)’",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            text = match.group(1).strip()
            if text:
                matches.append((match.start(), text))

    ordered: list[str] = []
    for _, text in sorted(matches):
        if text not in ordered:
            ordered.append(text)
    return ordered


def _without_trailing_ellipsis(value: str) -> tuple[str, bool]:
    """Return a quoted prefix and whether the auditor marked an unchanged suffix."""
    stripped = value.strip()
    for suffix in ("...", "…"):
        if stripped.endswith(suffix):
            return stripped[: -len(suffix)].rstrip(), True
    return stripped, False


def _exact_phrase_plan(
    issue: AuditIssue,
    current_text: str,
    *,
    field_name: str,
    source_bullet_id: str = "",
) -> ConcreteRephraseFix | None:
    """Extract an exact phrase or prefix replacement for one known text field."""
    fix_quotes = _quoted_values(issue.suggested_fix)
    issue_quotes = _quoted_values(issue.issue)

    pairs: list[tuple[str, str]] = []
    if len(fix_quotes) >= 2:
        pairs.extend(
            (fix_quotes[index], fix_quotes[index + 1])
            for index in range(len(fix_quotes) - 1)
        )
    elif len(fix_quotes) == 1:
        pairs.extend((original, fix_quotes[0]) for original in issue_quotes)

    for original, replacement in pairs:
        original_text, original_is_prefix = _without_trailing_ellipsis(original)
        replacement_text, replacement_is_prefix = _without_trailing_ellipsis(replacement)
        if not original_text or not replacement_text or original_text == replacement_text:
            continue
        if original_is_prefix or replacement_is_prefix:
            # A shortened audit quote is safe only when both values explicitly mark
            # the same unchanged suffix and the old prefix starts the target text.
            if (
                original_is_prefix
                and replacement_is_prefix
                and current_text.startswith(original_text)
            ):
                return ConcreteRephraseFix(
                    field_name=field_name,
                    original_text=original_text,
                    replacement_text=replacement_text,
                    source_bullet_id=source_bullet_id,
                    match_at_start=True,
                )
            continue
        if original_text in current_text:
            return ConcreteRephraseFix(
                field_name=field_name,
                original_text=original_text,
                replacement_text=replacement_text,
                source_bullet_id=source_bullet_id,
            )

    return None


def concrete_professional_summary_rephrase(
    issue: AuditIssue,
    current_summary: str,
) -> ConcreteRephraseFix | None:
    """Extract an exact safe summary replacement from an audit finding.

    Automatic editing is intentionally unavailable for advisory language such as
    "focus on relevant experience". The auditor must supply either an exact old
    phrase plus an exact replacement, or a complete quoted replacement summary.
    """
    section = " ".join(issue.section.casefold().replace("_", " ").split())
    if "summary" not in section or not issue.suggested_fix.strip():
        return None

    fix_text = issue.suggested_fix.strip()
    fix_quotes = _quoted_values(fix_text)

    phrase_plan = _exact_phrase_plan(
        issue,
        current_summary,
        field_name="professional_summary",
    )
    if phrase_plan is not None:
        return phrase_plan

    if len(fix_quotes) == 1:
        replacement = fix_quotes[0]

        # A complete quoted summary can be mechanically safe even when the
        # auditor returns only the paragraph rather than "Replace the summary
        # with ...". Require a summary-shaped value so a short quoted example
        # phrase can never replace the entire field accidentally.
        normalized_fix = " ".join(fix_text.casefold().split())
        full_summary_cues = (
            "replace the professional summary",
            "replace the summary",
            "rewrite the professional summary",
            "rewrite the summary",
            "use the following professional summary",
            "use the following summary",
            "rephrase the professional summary to",
            "rephrase the summary to",
        )
        standalone_fix = re.sub(
            r"^\s*(?:suggested fix|replacement summary|proposed summary)\s*:\s*",
            "",
            fix_text,
            flags=re.IGNORECASE,
        )
        standalone_quoted_summary = any(
            re.fullmatch(pattern, standalone_fix, flags=re.DOTALL) is not None
            for pattern in (
                r'\s*"[^"\n]+(?:\n[^"\n]+)*"\s*\.?\s*',
                r"\s*'[^'\n]+(?:\n[^'\n]+)*'\s*\.?\s*",
                r"\s*“[^”]+”\s*\.?\s*",
                r"\s*‘[^’]+’\s*\.?\s*",
            )
        )
        looks_like_complete_summary = (
            word_count(replacement) >= 30
            and sentence_count(replacement) >= 2
        )
        if (
            any(cue in normalized_fix for cue in full_summary_cues)
            or (standalone_quoted_summary and looks_like_complete_summary)
        ):
            if replacement != current_summary.strip():
                return ConcreteRephraseFix(
                    field_name="professional_summary",
                    replacement_text=replacement,
                    replace_entire_field=True,
                )

    return None


def concrete_bullet_rephrase(
    issue: AuditIssue,
    proposal: TailoringProposal,
) -> ConcreteRephraseFix | None:
    """Extract a safe exact replacement for one identified resume bullet."""
    section = " ".join(issue.section.casefold().replace("_", " ").split())
    source_id = issue.source_id.strip()
    if "bullet" not in section or not source_id or not issue.suggested_fix.strip():
        return None

    target = next(
        (item for item in proposal.bullet_proposals if item.source_bullet_id == source_id),
        None,
    )
    if target is None or not target.include:
        return None

    plan = _exact_phrase_plan(
        issue,
        target.proposed_text,
        field_name="bullet_proposals",
        source_bullet_id=source_id,
    )
    if plan is not None:
        return plan

    fix_text = issue.suggested_fix.strip()
    fix_quotes = _quoted_values(fix_text)
    normalized_fix = " ".join(fix_text.casefold().split())
    whole_bullet_cues = (
        "replace the bullet with",
        "rewrite the bullet as",
        "use the following bullet",
        "replace this bullet with",
    )
    if len(fix_quotes) == 1 and any(cue in normalized_fix for cue in whole_bullet_cues):
        replacement, abbreviated = _without_trailing_ellipsis(fix_quotes[0])
        if replacement and not abbreviated and replacement != target.proposed_text.strip():
            return ConcreteRephraseFix(
                field_name="bullet_proposals",
                replacement_text=replacement,
                replace_entire_field=True,
                source_bullet_id=source_id,
            )
    return None


def concrete_individual_audit_rephrase(
    issue: AuditIssue,
    proposal: TailoringProposal,
) -> ConcreteRephraseFix | None:
    """Return a deterministic local edit for a supported audit finding."""
    summary = concrete_professional_summary_rephrase(
        issue, proposal.professional_summary
    )
    if summary is not None:
        return summary
    return concrete_bullet_rephrase(issue, proposal)


def apply_concrete_individual_audit_rephrase(
    proposal: TailoringProposal,
    issue: AuditIssue,
) -> TailoringProposal:
    """Apply one exact summary or bullet edit without invoking the work model."""
    plan = concrete_individual_audit_rephrase(issue, proposal)
    if plan is None:
        raise ValueError(
            "This recommendation does not provide exact replacement wording for "
            "a supported resume field. Edit the resume manually to make the "
            "judgment-based change."
        )

    if plan.field_name == "professional_summary":
        return apply_concrete_professional_summary_rephrase(proposal, issue)

    updated = proposal.model_copy(deep=True)
    target = next(
        (
            item
            for item in updated.bullet_proposals
            if item.source_bullet_id == plan.source_bullet_id
        ),
        None,
    )
    if target is None:
        raise ValueError(
            "The bullet targeted by this recommendation is no longer present. "
            "Rerun the Step 3 evidence review."
        )

    if plan.replace_entire_field:
        revised_text = plan.replacement_text.strip()
    else:
        if plan.match_at_start:
            if not target.proposed_text.startswith(plan.original_text):
                raise ValueError(
                    "The exact bullet prefix targeted by this recommendation is no "
                    "longer present. Rerun the Step 3 evidence review."
                )
            revised_text = (
                plan.replacement_text
                + target.proposed_text[len(plan.original_text) :]
            ).strip()
        else:
            if plan.original_text not in target.proposed_text:
                raise ValueError(
                    "The exact wording targeted by this recommendation is no longer "
                    "present in the selected bullet. Rerun the Step 3 evidence review."
                )
            revised_text = target.proposed_text.replace(
                plan.original_text, plan.replacement_text, 1
            ).strip()

    if revised_text == target.proposed_text.strip():
        raise ValueError("The suggested fix did not change the resume.")
    target.proposed_text = revised_text
    return updated


def apply_concrete_professional_summary_rephrase(
    proposal: TailoringProposal,
    issue: AuditIssue,
) -> TailoringProposal:
    """Apply an exact summary wording edit without invoking the work model."""
    plan = concrete_professional_summary_rephrase(
        issue, proposal.professional_summary
    )
    if plan is None:
        raise ValueError(
            "This recommendation does not provide exact replacement wording. "
            "Edit the resume manually to make the judgment-based change."
        )

    updated = proposal.model_copy(deep=True)
    if plan.replace_entire_field:
        revised_summary = plan.replacement_text.strip()
    else:
        if plan.original_text not in updated.professional_summary:
            raise ValueError(
                "The exact phrase targeted by this recommendation is no longer "
                "present in the resume. Rerun the Step 3 evidence review."
            )
        revised_summary = updated.professional_summary.replace(
            plan.original_text,
            plan.replacement_text,
            1,
        ).strip()

    if revised_summary == updated.professional_summary.strip():
        raise ValueError("The suggested fix did not change the resume.")
    updated.professional_summary = revised_summary
    return updated
