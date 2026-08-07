from __future__ import annotations

from typing import Any

from .models import (
    BulletProposal,
    CandidateProfile,
    JobAnalysis,
    TailoringProposal,
)


# Natural, user-facing outcomes produced by the deterministic selector.
DETERMINISTIC_INCLUDE_PREFIX = "Included — strong job match."
DETERMINISTIC_TRANSFERABLE_INCLUDE_PREFIX = "Included — strong transferable evidence."
DETERMINISTIC_EXCLUDE_PREFIX = "Not included — lower priority."
DETERMINISTIC_DUPLICATE_PREFIX = "Not included — similar evidence already selected."
BULLET_MAPPING_FALLBACK_NOTE = (
    "Verified source bullet restored for deterministic job alignment."
)

# Legacy markers are retained only so persisted workflows migrate cleanly.
MISSING_SELECTION_NOTE = (
    "Selection decision missing — review required. The generated proposal did not "
    "return an explicit include or exclude decision for this source bullet. The "
    "accomplishment remains preserved in the Application Baseline and Verified "
    "Resume Evidence, but it is not automatically included in the Job-Aligned Resume."
)
_LEGACY_AUTO_RESTORE_NOTE = (
    "Automatically restored from the Verified Resume Evidence because the generated "
    "proposal did not return a selection decision for this source bullet."
)
_LEGACY_AUTO_INCLUDE_PREFIX = "Included after automatic reconciliation."
_LEGACY_AUTO_EXCLUDE_PREFIX = "Excluded after automatic reconciliation."

# Compatibility exports used by existing callers. Their values now describe the
# simpler deterministic selector rather than a missing-decision reconciliation step.
AUTO_RECONCILED_INCLUDE_PREFIX = DETERMINISTIC_INCLUDE_PREFIX
AUTO_RECONCILED_EXCLUDE_PREFIX = DETERMINISTIC_EXCLUDE_PREFIX

_DOCUMENTED_EXCLUSION_MARKERS = (
    "duplicate",
    "duplicative",
    "redundan",
    "similar evidence",
    "resume length",
    "space constraint",
    "available resume space",
    "stronger evidence",
    "more specific evidence",
    "lower priority",
    "lower relevance",
    "less relevant",
    "seniority",
    "responsibility level",
    "represented by",
    "rewritten as",
)


def is_missing_selection_decision(item: BulletProposal) -> bool:
    """Identify only legacy persisted records from the old selection algorithm."""

    note = item.evidence_note.strip()
    return note.startswith(MISSING_SELECTION_NOTE) or note.startswith(
        _LEGACY_AUTO_RESTORE_NOTE
    )


def is_auto_reconciled_inclusion(item: BulletProposal) -> bool:
    """Return whether deterministic code selected the bullet."""

    note = item.evidence_note.strip()
    return note.startswith(
        (
            DETERMINISTIC_INCLUDE_PREFIX,
            DETERMINISTIC_TRANSFERABLE_INCLUDE_PREFIX,
            _LEGACY_AUTO_INCLUDE_PREFIX,
        )
    )


def is_auto_reconciled_exclusion(item: BulletProposal) -> bool:
    """Return whether deterministic code left the bullet out."""

    note = item.evidence_note.strip()
    return note.startswith(
        (
            DETERMINISTIC_EXCLUDE_PREFIX,
            DETERMINISTIC_DUPLICATE_PREFIX,
            _LEGACY_AUTO_EXCLUDE_PREFIX,
        )
    )


def is_duplicate_selection_exclusion(item: BulletProposal) -> bool:
    return item.evidence_note.strip().startswith(DETERMINISTIC_DUPLICATE_PREFIX)


def has_documented_exclusion_rationale(item: BulletProposal) -> bool:
    """Return whether an excluded matched bullet has a concrete prioritization reason."""

    note = item.evidence_note.casefold()
    return any(marker in note for marker in _DOCUMENTED_EXCLUSION_MARKERS)


def missing_source_bullet_ids(
    profile: CandidateProfile, proposal: TailoringProposal
) -> list[str]:
    """Return source bullet IDs that have no structured analysis record."""

    proposal_ids = {item.source_bullet_id for item in proposal.bullet_proposals}
    return [
        bullet.id
        for experience in profile.experiences
        for bullet in experience.bullets
        if bullet.id not in proposal_ids
    ]


def repair_missing_bullet_proposals(
    profile: CandidateProfile, proposal: TailoringProposal
) -> TailoringProposal:
    """Ensure every source bullet has an analysis record.

    AI no longer chooses inclusion. Missing records are restored with verified source
    wording and any authoritative evidence-match links; the deterministic selector
    makes the final include/exclude decision later. Legacy missing-decision records are
    migrated away from the old review-required state.
    """

    missing_ids = set(missing_source_bullet_ids(profile, proposal))
    repaired = proposal.model_copy(deep=True)
    changed = False

    evidence_requirements_by_source: dict[str, list[str]] = {}
    for match in repaired.evidence_matches:
        if match.status == "unsupported":
            continue
        for evidence_id in match.evidence_ids:
            evidence_requirements_by_source.setdefault(evidence_id, []).append(
                match.requirement_id
            )

    for item in repaired.bullet_proposals:
        inferred_matches = list(
            dict.fromkeys(
                [
                    *item.matched_requirement_ids,
                    *evidence_requirements_by_source.get(item.source_bullet_id, []),
                ]
            )
        )
        if is_missing_selection_decision(item):
            item.include = False
            item.evidence_note = BULLET_MAPPING_FALLBACK_NOTE
            changed = True
        if item.matched_requirement_ids != inferred_matches:
            item.matched_requirement_ids = inferred_matches
            changed = True

    if missing_ids:
        source_lookup = profile.bullet_lookup()
        for source_id in missing_ids:
            repaired.bullet_proposals.append(
                BulletProposal(
                    source_bullet_id=source_id,
                    include=False,
                    proposed_text=source_lookup[source_id],
                    matched_requirement_ids=list(
                        dict.fromkeys(
                            evidence_requirements_by_source.get(source_id, [])
                        )
                    ),
                    evidence_note=BULLET_MAPPING_FALLBACK_NOTE,
                )
            )
        changed = True

    return repaired if changed else proposal


def selection_consistency_warnings(
    profile: CandidateProfile,
    analysis: JobAnalysis | None,
    proposal: TailoringProposal,
) -> list[dict[str, Any]]:
    """Warn only about a real matched-versus-unmatched selection conflict.

    Missing AI records are no longer a user-facing selection state because the model
    does not choose bullets. They are restored and scored by deterministic code.
    """

    repaired = repair_missing_bullet_proposals(profile, proposal)
    requirement_lookup = {
        requirement.id: requirement.requirement
        for requirement in (analysis.requirements if analysis is not None else [])
    }
    proposal_lookup = {
        item.source_bullet_id: item for item in repaired.bullet_proposals
    }
    warnings: list[dict[str, Any]] = []

    for experience in profile.experiences:
        items = [
            proposal_lookup[bullet.id]
            for bullet in experience.bullets
            if bullet.id in proposal_lookup
        ]
        included_without_matches = [
            item for item in items if item.include and not item.matched_requirement_ids
        ]
        excluded_with_matches = [
            item
            for item in items
            if not item.include
            and item.matched_requirement_ids
            and not has_documented_exclusion_rationale(item)
        ]
        if included_without_matches and excluded_with_matches:
            included_ids = [item.source_bullet_id for item in included_without_matches]
            excluded_ids = [item.source_bullet_id for item in excluded_with_matches]
            requirement_labels: list[str] = []
            for item in excluded_with_matches:
                for requirement_id in item.matched_requirement_ids:
                    label = requirement_lookup.get(requirement_id, requirement_id)
                    rendered = f"{requirement_id}: {label}"
                    if rendered not in requirement_labels:
                        requirement_labels.append(rendered)
            warnings.append(
                {
                    "code": "zero_match_displaces_matched",
                    "experience_id": experience.id,
                    "source_ids": [*included_ids, *excluded_ids],
                    "title": "Possible bullet-selection inconsistency",
                    "detail": (
                        f"Included bullet(s) {', '.join(included_ids)} have no recorded "
                        f"requirement match, while excluded bullet(s) {', '.join(excluded_ids)} "
                        "support "
                        + "; ".join(requirement_labels)
                        + ". Re-run Job Alignment or review the manual overrides."
                    ),
                }
            )

    return warnings
