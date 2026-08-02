from __future__ import annotations

from .models import BulletProposal, CandidateProfile, TailoringProposal


_AUTO_RESTORE_NOTE = (
    "Automatically restored from the Verified Resume Evidence because the generated "
    "proposal did not return a selection decision for this source bullet."
)


def missing_source_bullet_ids(
    profile: CandidateProfile, proposal: TailoringProposal
) -> list[str]:
    """Return source bullet IDs that have no structured proposal record."""
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
    """Restore any source bullet omitted by proposal generation.

    Every source bullet must have an explicit structured decision. When a model omits
    the record entirely, the omission is a generation defect rather than a defensible
    tailoring choice. The safe repair is to preserve the user's original experience:
    restore the source wording and include it until a later, explicit exclusion decision
    is recorded.
    """
    missing_ids = set(missing_source_bullet_ids(profile, proposal))
    if not missing_ids:
        return proposal

    repaired = proposal.model_copy(deep=True)
    for experience in profile.experiences:
        for source_bullet in experience.bullets:
            if source_bullet.id not in missing_ids:
                continue
            repaired.bullet_proposals.append(
                BulletProposal(
                    source_bullet_id=source_bullet.id,
                    include=True,
                    proposed_text=source_bullet.text,
                    matched_requirement_ids=[],
                    evidence_note=_AUTO_RESTORE_NOTE,
                )
            )
    return repaired
