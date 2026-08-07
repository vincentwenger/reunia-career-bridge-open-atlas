"""Apply user-reviewed extracted fields to the reusable Baseline Resume."""

from __future__ import annotations

from typing import Any

from resume_tailor.models import CandidateProfile, EducationItem, VerifiedSkills


BASELINE_SKILL_FIELDS = (
    "hard_skills",
    "soft_skills",
    "tools_software",
    "industry_knowledge",
    "languages",
)


def normalize_baseline_skill_values(value: Any) -> list[str]:
    """Normalize an ordered skill list without changing its evidence wording."""

    if isinstance(value, str):
        raw_values = value.splitlines()
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        item = " ".join(str(raw or "").split()).strip()
        key = item.casefold()
        if item and key not in seen:
            normalized.append(item)
            seen.add(key)
    return normalized


def apply_baseline_summary(profile: CandidateProfile, value: Any) -> bool:
    """Replace the editable professional summary while preserving the imported source."""

    summary = str(value or "").strip()
    if profile.current_summary == summary:
        return False
    profile.current_summary = summary
    return True


def apply_baseline_skills(
    profile: CandidateProfile,
    payload: dict[str, Any],
) -> bool:
    """Replace the editable extracted skill categories in the Baseline Resume."""

    updated = VerifiedSkills(
        **{
            field: normalize_baseline_skill_values(payload.get(field))
            for field in BASELINE_SKILL_FIELDS
        }
    )
    if profile.skills.model_dump(mode="json") == updated.model_dump(mode="json"):
        return False
    profile.skills = updated
    return True


def apply_baseline_education(
    profile: CandidateProfile,
    education_index: int,
    payload: dict[str, Any],
) -> bool:
    """Update one education record in place so its evidence position remains stable."""

    if education_index < 0 or education_index >= len(profile.education):
        raise IndexError("Education record not found.")

    current = profile.education[education_index]
    updated = EducationItem(
        credential=str(payload.get("credential") or "").strip(),
        institution=str(payload.get("institution") or "").strip(),
        location=str(payload.get("location") or "").strip(),
        date=str(payload.get("date") or "").strip(),
        detail=str(payload.get("detail") or "").strip(),
    )
    if current.model_dump(mode="json") == updated.model_dump(mode="json"):
        return False
    profile.education[education_index] = updated
    return True


def append_baseline_education(
    profile: CandidateProfile,
    payload: dict[str, Any],
) -> int:
    """Append one manually entered education record and return its index."""

    item = EducationItem(
        credential=str(payload.get("credential") or "").strip(),
        institution=str(payload.get("institution") or "").strip(),
        location=str(payload.get("location") or "").strip(),
        date=str(payload.get("date") or "").strip(),
        detail=str(payload.get("detail") or "").strip(),
    )
    profile.education.append(item)
    return len(profile.education) - 1


def remove_baseline_education(
    profile: CandidateProfile,
    education_index: int,
) -> EducationItem:
    """Remove one education record from the reusable Baseline Resume."""

    if education_index < 0 or education_index >= len(profile.education):
        raise IndexError("Education record not found.")
    return profile.education.pop(education_index)
