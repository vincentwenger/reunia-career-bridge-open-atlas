"""Manual Baseline Resume creation and conservative import merging."""

from __future__ import annotations

from typing import Iterable

from resume_tailor.models import CandidateProfile, EducationItem, Experience, ResumeBullet, VerifiedSkills


def _key(*values: str) -> tuple[str, ...]:
    return tuple(" ".join(str(value or "").casefold().split()) for value in values)


def _union(values: Iterable[str], additions: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*values, *additions]:
        text = " ".join(str(value or "").split()).strip()
        normalized = text.casefold()
        if text and normalized not in seen:
            result.append(text)
            seen.add(normalized)
    return result


def merge_candidate_profiles(imported: CandidateProfile, manual: CandidateProfile) -> CandidateProfile:
    """Merge an imported profile into manual facts without overwriting user-entered facts.

    Manual wording is kept first. Distinct imported education, roles, skills, and
    bullets are appended for review. The imported contact block is preferred because
    the manual Baseline Resume editor does not currently collect contact fields.
    """

    merged = imported.model_copy(deep=True)
    if manual.name.strip():
        merged.name = manual.name.strip()
    if manual.current_summary.strip():
        merged.current_summary = manual.current_summary.strip()

    merged.skills = VerifiedSkills(
        hard_skills=_union(manual.skills.hard_skills, imported.skills.hard_skills),
        soft_skills=_union(manual.skills.soft_skills, imported.skills.soft_skills),
        tools_software=_union(manual.skills.tools_software, imported.skills.tools_software),
        industry_knowledge=_union(manual.skills.industry_knowledge, imported.skills.industry_knowledge),
        languages=_union(manual.skills.languages, imported.skills.languages),
    )

    education: list[EducationItem] = []
    education_keys: set[tuple[str, ...]] = set()
    for item in [*manual.education, *imported.education]:
        key = _key(item.credential, item.institution, item.date)
        if key in education_keys:
            continue
        education.append(item.model_copy(deep=True))
        education_keys.add(key)
    merged.education = education

    experiences: list[Experience] = []
    experience_by_key: dict[tuple[str, ...], Experience] = {}
    used_experience_ids: set[str] = set()
    used_bullet_ids: set[str] = set()

    def unique_experience_id(preferred: str) -> str:
        base = preferred.strip() or "EXP"
        candidate = base
        suffix = 2
        while candidate in used_experience_ids:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used_experience_ids.add(candidate)
        return candidate

    def unique_bullet_id(preferred: str, experience_id: str, position: int) -> str:
        base = preferred.strip() or f"{experience_id}-BULLET-{position:02d}"
        candidate = base
        suffix = 2
        while candidate in used_bullet_ids:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used_bullet_ids.add(candidate)
        return candidate

    for source in [*manual.experiences, *imported.experiences]:
        key = _key(source.title, source.employer, source.dates)
        existing = experience_by_key.get(key)
        if existing is None:
            experience_id = unique_experience_id(source.id)
            bullets = [
                ResumeBullet(
                    id=unique_bullet_id(bullet.id, experience_id, index),
                    text=bullet.text,
                )
                for index, bullet in enumerate(source.bullets, start=1)
                if bullet.text.strip()
            ]
            copied = Experience(
                id=experience_id,
                employer=source.employer,
                location=source.location,
                dates=source.dates,
                title=source.title,
                bullets=bullets,
            )
            experiences.append(copied)
            experience_by_key[key] = copied
            continue

        existing.location = existing.location or source.location
        bullet_texts = {" ".join(item.text.casefold().split()) for item in existing.bullets}
        for bullet in source.bullets:
            normalized = " ".join(bullet.text.casefold().split())
            if not normalized or normalized in bullet_texts:
                continue
            existing.bullets.append(
                ResumeBullet(
                    id=unique_bullet_id(
                        bullet.id,
                        existing.id,
                        len(existing.bullets) + 1,
                    ),
                    text=bullet.text,
                )
            )
            bullet_texts.add(normalized)

    merged.experiences = experiences
    existing_evidence_ids = {item.id for item in merged.supplemental_evidence}
    for item in manual.supplemental_evidence:
        if item.id not in existing_evidence_ids:
            merged.supplemental_evidence.append(item.model_copy(deep=True))
            existing_evidence_ids.add(item.id)
    return CandidateProfile.model_validate(merged.model_dump(mode="json"))
