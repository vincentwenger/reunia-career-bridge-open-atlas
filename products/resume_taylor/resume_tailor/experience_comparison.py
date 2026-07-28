from __future__ import annotations

from typing import NamedTuple


class BulletInclusionComparison(NamedTuple):
    status: str
    label: str
    reference_for_diff: str
    current_for_diff: str


def classify_bullet_inclusion(
    *,
    reference_include: bool,
    current_include: bool,
    reference_text: str,
    current_text: str,
    current_label: str,
    reference_label: str = "comparison version",
    reference_present: bool = True,
    current_present: bool = True,
    rewritten_as_id: str = "",
    rewritten_text: str = "",
) -> BulletInclusionComparison:
    """Classify how a source bullet changed between two resume versions.

    Inclusion changes are deliberately separate from wording changes. This lets the UI
    show a complete source bullet as removed when it is excluded from the newer resume,
    even though its stored proposal wording remains available for restoration.
    """
    # Proposal data is repaired before this classifier runs. Keep a defensive
    # fallback that preserves the source bullet rather than exposing an internal
    # mapping failure as a user decision.
    if reference_present and not current_present:
        return BulletInclusionComparison(
            "restored_missing_included",
            "Automatically restored from source resume",
            reference_text,
            reference_text,
        )

    # A user can resolve an unexplained omission by identifying the included bullet
    # that represents the same accomplishment. Keep this separate from exclusions so
    # the comparison does not imply that the source content was intentionally removed.
    if current_present and rewritten_as_id:
        return BulletInclusionComparison(
            "rewritten",
            f"Rewritten as {rewritten_as_id}",
            reference_text,
            rewritten_text,
        )

    # A missing structured item cannot have appeared in the rendered reference resume.
    # Normalize that case before classifying inclusion changes so repaired/restored items
    # are shown as restorations rather than unchanged content.
    if not reference_present:
        reference_include = False
        reference_text = ""

    # A deterministic repair can restore a source bullet to the structured proposal.
    # Keep that restoration distinct from an ordinary addition so the UI can state
    # explicitly whether the restored bullet is included in the downloadable resume.
    if not reference_present and current_present and current_include:
        return BulletInclusionComparison(
            "restored_missing_included",
            "Restored missing bullet — included",
            "",
            current_text,
        )

    if not reference_present and current_present and not current_include:
        return BulletInclusionComparison(
            "restored_missing_excluded",
            "Restored missing bullet — not included",
            "",
            current_text,
        )

    if reference_include and not current_include:
        return BulletInclusionComparison(
            "excluded",
            f"Excluded from {current_label}",
            reference_text,
            "",
        )
    if not reference_include and current_include:
        return BulletInclusionComparison(
            "added",
            f"Added to {current_label}",
            "",
            current_text,
        )
    if not reference_include and not current_include:
        return BulletInclusionComparison(
            "excluded_unchanged",
            f"Not included in {reference_label} or {current_label}",
            reference_text,
            reference_text,
        )
    if reference_text != current_text:
        return BulletInclusionComparison(
            "modified",
            "Included and modified",
            reference_text,
            current_text,
        )
    return BulletInclusionComparison(
        "unchanged",
        "Included and unchanged",
        reference_text,
        current_text,
    )
