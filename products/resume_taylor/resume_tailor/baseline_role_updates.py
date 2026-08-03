"""Apply user-reviewed employment role fields to the reusable Baseline Resume."""

from __future__ import annotations

import re
from typing import Any

from resume_tailor.models import CandidateProfile, Experience, ResumeBullet

_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[•●▪◦‣⁃*-]|\d+[.)])\s*")


def responsibility_lines(value: Any) -> list[str]:
    """Return clean resume bullet text from the editable responsibilities field."""

    lines: list[str] = []
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = _BULLET_PREFIX_RE.sub("", raw_line).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _next_bullet_id(
    experience_id: str,
    position: int,
    used_ids: set[str],
) -> str:
    base = f"{experience_id}-EDIT-{position:02d}"
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def apply_career_role_to_profile(
    profile: CandidateProfile,
    role: dict[str, Any],
) -> bool:
    """Update the matching Baseline Resume experience from a reviewed role.

    The imported source profile remains untouched. Existing bullet IDs are retained
    by position so evidence links remain stable; newly added bullets receive unique,
    deterministic edit IDs.
    """

    source_experience_id = str(
        role.get("source_experience_id") or role.get("role_id") or ""
    ).strip()
    if not source_experience_id:
        return False

    experience = next(
        (
            item
            for item in profile.experiences
            if str(item.id or "").strip() == source_experience_id
        ),
        None,
    )
    if experience is None:
        return False

    before = experience.model_dump(mode="json")
    experience.title = str(role.get("official_title") or experience.title).strip()
    experience.employer = str(role.get("employer") or experience.employer).strip()
    experience.dates = str(role.get("dates") or "").strip()
    experience.location = str(role.get("location") or "").strip()

    requested_lines = responsibility_lines(role.get("responsibilities"))
    existing_bullets = list(experience.bullets)
    used_ids = {
        str(bullet.id)
        for item in profile.experiences
        for bullet in item.bullets
        if str(bullet.id).strip()
    }
    replacement: list[ResumeBullet] = []
    for index, text in enumerate(requested_lines, start=1):
        if index <= len(existing_bullets):
            bullet_id = existing_bullets[index - 1].id
        else:
            bullet_id = _next_bullet_id(experience.id, index, used_ids)
        replacement.append(ResumeBullet(id=bullet_id, text=text))
    experience.bullets = replacement

    return before != experience.model_dump(mode="json")


def append_manual_experience(
    profile: CandidateProfile,
    role: dict[str, Any],
) -> Experience:
    """Append a manually entered role with stable, non-import evidence IDs."""

    used_experience_ids = {str(item.id) for item in profile.experiences}
    number = 1
    experience_id = f"MAN-EXP-{number:03d}"
    while experience_id in used_experience_ids:
        number += 1
        experience_id = f"MAN-EXP-{number:03d}"
    bullets = [
        ResumeBullet(id=f"{experience_id}-BULLET-{index:02d}", text=text)
        for index, text in enumerate(
            responsibility_lines(role.get("responsibilities")), start=1
        )
    ]
    experience = Experience(
        id=experience_id,
        employer=str(role.get("employer") or "").strip(),
        location=str(role.get("location") or "").strip(),
        dates=str(role.get("dates") or "").strip(),
        title=str(role.get("official_title") or "").strip(),
        bullets=bullets,
    )
    profile.experiences.append(experience)
    return experience
