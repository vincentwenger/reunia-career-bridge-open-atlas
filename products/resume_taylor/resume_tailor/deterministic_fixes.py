from __future__ import annotations

import re

from .bullet_text import has_bullet_structure_artifacts, normalize_resume_bullet_text
from .models import (
    AuditIssue,
    BulletProposal,
    CandidateProfile,
    EvidenceMatch,
    JobAnalysis,
    SkillSet,
    TailoringProposal,
)
from .confirmation import ensure_confirmed_answers_visible
from .skill_rules import balance_skill_categories
from .validation import (
    normalize,
    numeric_tokens,
    remove_adjacent_repeated_words,
    sentence_count,
    validate_proposal,
    word_count,
)

_PRIORITY_WEIGHT = {"critical": 3, "important": 2, "secondary": 1}
_STATUS_WEIGHT = {"supported": 3, "partial": 2, "unsupported": 1}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = normalize(value)
        if value.strip() and key and key not in seen:
            result.append(value.strip())
            seen.add(key)
    return result


def _summary_is_valid(text: str) -> bool:
    return 50 <= word_count(text) <= 80 and sentence_count(text) in (3, 4)


def _sentence_parts(text: str) -> list[str]:
    return [
        part.strip().rstrip(".!?")
        for part in re.split(r"(?<=[.!?])\s+", text.strip())
        if part.strip()
    ]


def _ensure_period(text: str) -> str:
    text = text.strip().rstrip(".!?")
    return f"{text}." if text else ""


def _build_safe_summary(profile: CandidateProfile, preferred: str) -> str:
    """Return a 3-4 sentence, 50-80 word summary using only profile-backed facts."""
    cleaned_preferred = remove_adjacent_repeated_words(preferred).strip()
    cleaned_source_summary = remove_adjacent_repeated_words(
        profile.current_summary
    ).strip()
    for candidate in (cleaned_preferred, cleaned_source_summary):
        if _summary_is_valid(candidate):
            return candidate

    source_sentences = _sentence_parts(cleaned_source_summary)
    preferred_sentences = _sentence_parts(cleaned_preferred)
    sentences: list[str] = []
    seen: set[str] = set()
    for item in preferred_sentences + source_sentences:
        key = normalize(item)
        if key and key not in seen:
            sentences.append(item)
            seen.add(key)
        if len(sentences) == 4:
            break

    employers = [experience.employer for experience in profile.experiences if experience.employer]
    titles = [experience.title for experience in profile.experiences if experience.title]
    skills = _dedupe(profile.all_verified_skills())
    education = profile.education[0] if profile.education else None

    fallbacks = []
    if skills:
        fallbacks.append("Verified skills include " + ", ".join(skills[:8]))
    if employers:
        role_text = ", ".join(titles[:3]) if titles else "professional roles"
        fallbacks.append(
            f"Professional experience includes {role_text} at {', '.join(employers[:3])}"
        )
    if education:
        fallbacks.append(
            f"Education includes {education.credential} from {education.institution}"
        )
    fallbacks.append("The resume documents experience, skills, and accomplishments from the candidate profile")

    for item in fallbacks:
        if len(sentences) >= 3:
            break
        key = normalize(item)
        if key and key not in seen:
            sentences.append(item)
            seen.add(key)

    sentences = sentences[:4]
    summary = " ".join(_ensure_period(item) for item in sentences if item)

    # If the summary is short, expand only with verified skills and documented employers.
    if word_count(summary) < 50 and skills:
        extra = "Additional verified capabilities include " + ", ".join(skills[8:16] or skills[:8])
        if len(sentences) < 4:
            sentences.append(extra)
        else:
            sentences[-1] = sentences[-1].rstrip(".!?") + "; " + extra[0].lower() + extra[1:]
        summary = " ".join(_ensure_period(item) for item in sentences[:4] if item)

    if word_count(summary) < 50 and employers:
        extra = "The documented work history spans " + ", ".join(employers)
        if len(sentences) < 4:
            sentences.append(extra)
        else:
            sentences[-1] = sentences[-1].rstrip(".!?") + "; " + extra[0].lower() + extra[1:]
        summary = " ".join(_ensure_period(item) for item in sentences[:4] if item)

    # Keep at most 80 words while retaining 3 sentences. Truncate only the final sentence.
    parts = _sentence_parts(summary)
    while len(parts) < 3:
        parts.append("The candidate profile provides verified professional evidence")
    parts = parts[:4]
    while sum(word_count(_ensure_period(part)) for part in parts) > 80 and parts:
        last_words = parts[-1].split()
        if len(last_words) > 6:
            parts[-1] = " ".join(last_words[:-1])
        elif len(parts) == 4:
            parts.pop()
        else:
            break
    summary = " ".join(_ensure_period(item) for item in parts)

    # Last-resort padding with verified skill names. This remains profile-backed.
    if word_count(summary) < 50:
        padding = skills or employers or ["documented professional experience"]
        words = summary.rstrip(".").split()
        index = 0
        while len(words) < 50:
            words.extend(str(padding[index % len(padding)]).split())
            index += 1
        words = words[:50]
        # Preserve 3 sentences by appending the padding to the final sentence.
        summary_parts = _sentence_parts(summary)
        prefix = " ".join(_ensure_period(item) for item in summary_parts[:-1])
        used = word_count(prefix)
        final_words = words[used:]
        summary = (prefix + " " + " ".join(final_words).rstrip(".") + ".").strip()

    return summary


def _clean_skills(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> SkillSet:
    """Clean, reclassify, and balance skills using verified profile categories."""

    normalized = proposal.skills.model_copy(deep=True)
    for field in (
        "hard_skills",
        "soft_skills",
        "tools_software",
        "industry_knowledge",
    ):
        setattr(
            normalized,
            field,
            [
                remove_adjacent_repeated_words(skill).strip()
                for skill in getattr(normalized, field)
                if remove_adjacent_repeated_words(skill).strip()
            ],
        )
    return balance_skill_categories(profile, analysis, normalized)


def _requirement_score(ids: list[str], analysis: JobAnalysis) -> int:
    lookup = {item.id: item for item in analysis.requirements}
    return sum(_PRIORITY_WEIGHT[lookup[item].priority] for item in set(ids) if item in lookup)


def _clean_bullets(
    profile: CandidateProfile, analysis: JobAnalysis, proposal: TailoringProposal
) -> list[BulletProposal]:
    source_lookup = profile.bullet_lookup()
    confirmed_source_ids = {
        evidence.source_bullet_id
        for evidence in profile.supplemental_evidence
        if evidence.source == "candidate_confirmation" and evidence.source_bullet_id
    }
    requirement_ids = {item.id for item in analysis.requirements}
    first_by_id: dict[str, BulletProposal] = {}
    for item in proposal.bullet_proposals:
        if item.source_bullet_id in source_lookup and item.source_bullet_id not in first_by_id:
            first_by_id[item.source_bullet_id] = item

    result: list[BulletProposal] = []
    restored_ids: set[str] = set()
    supplemental_numbers = {
        token
        for evidence in profile.supplemental_evidence
        for token in numeric_tokens(evidence.statement)
    }
    for experience in profile.experiences:
        for source in experience.bullets:
            existing = first_by_id.get(source.id)
            source_text = remove_adjacent_repeated_words(source.text).strip()
            source_is_confirmed = source.id in confirmed_source_ids
            if source_is_confirmed or has_bullet_structure_artifacts(source_text):
                source_text = normalize_resume_bullet_text(
                    source_text, max_words=35 if source_is_confirmed else 55
                )

            if existing is None:
                restored_ids.add(source.id)
                result.append(
                    BulletProposal(
                        source_bullet_id=source.id,
                        include=True,
                        proposed_text=source_text,
                        matched_requirement_ids=[],
                        evidence_note=f"Directly supported by source bullet {source.id}.",
                    )
                )
                continue

            text = remove_adjacent_repeated_words(existing.proposed_text).strip()
            if source_is_confirmed or has_bullet_structure_artifacts(text):
                text = normalize_resume_bullet_text(
                    text, max_words=35 if source_is_confirmed else 55
                )
            source_numbers = numeric_tokens(source.text)
            new_numbers = numeric_tokens(text) - source_numbers - supplemental_numbers
            if not text or new_numbers or word_count(text) > 55:
                text = source_text

            matched = [
                requirement_id
                for requirement_id in _dedupe(existing.matched_requirement_ids)
                if requirement_id in requirement_ids
            ]
            result.append(
                existing.model_copy(
                    update={
                        "proposed_text": text,
                        "matched_requirement_ids": matched,
                        "evidence_note": existing.evidence_note.strip()
                        or f"Directly supported by source bullet {source.id}.",
                    }
                )
            )

    # Enforce employer-specific selection ranges deterministically.
    by_id = {item.source_bullet_id: item for item in result}
    limits = [(6, 7), (3, 4), (2, 3)]
    for index, experience in enumerate(profile.experiences):
        minimum, maximum = limits[index] if index < len(limits) else (2, 3)
        items = [by_id[bullet.id] for bullet in experience.bullets]
        source_order = {bullet.id: position for position, bullet in enumerate(experience.bullets)}

        def rank(item: BulletProposal) -> tuple[int, int, int, int, int]:
            return (
                1 if item.include else 0,
                1 if item.source_bullet_id in confirmed_source_ids else 0,
                1 if item.source_bullet_id in restored_ids else 0,
                _requirement_score(item.matched_requirement_ids, analysis),
                -source_order[item.source_bullet_id],
            )

        selected = sorted(items, key=rank, reverse=True)[:maximum]
        selected_ids = {item.source_bullet_id for item in selected if item.include}
        if len(selected_ids) < minimum:
            candidates = [item for item in sorted(items, key=rank, reverse=True) if item.source_bullet_id not in selected_ids]
            for item in candidates:
                selected_ids.add(item.source_bullet_id)
                if len(selected_ids) >= minimum:
                    break
        if len(selected_ids) > maximum:
            selected_ids = {
                item.source_bullet_id
                for item in sorted(
                    [item for item in items if item.source_bullet_id in selected_ids],
                    key=rank,
                    reverse=True,
                )[:maximum]
            }

        for item in items:
            by_id[item.source_bullet_id] = item.model_copy(
                update={"include": item.source_bullet_id in selected_ids}
            )

    return [by_id[bullet.id] for experience in profile.experiences for bullet in experience.bullets]


def _clean_evidence(
    profile: CandidateProfile, analysis: JobAnalysis, proposal: TailoringProposal
) -> list[EvidenceMatch]:
    valid_evidence_ids = set(profile.bullet_lookup()) | {
        item.id for item in profile.supplemental_evidence
    }
    grouped: dict[str, list[EvidenceMatch]] = {}
    requirement_ids = {item.id for item in analysis.requirements}
    for match in proposal.evidence_matches:
        if match.requirement_id in requirement_ids:
            grouped.setdefault(match.requirement_id, []).append(match)

    cleaned: list[EvidenceMatch] = []
    for requirement in analysis.requirements:
        matches = grouped.get(requirement.id, [])
        if not matches:
            cleaned.append(
                EvidenceMatch(
                    requirement_id=requirement.id,
                    status="unsupported",
                    evidence_ids=[],
                    rationale="No supporting evidence is documented in the candidate profile.",
                )
            )
            continue

        best = max(matches, key=lambda item: _STATUS_WEIGHT[item.status])
        evidence_ids = _dedupe(
            [
                evidence_id
                for match in matches
                for evidence_id in match.evidence_ids
                if evidence_id in valid_evidence_ids
            ]
        )
        status = best.status
        rationale = best.rationale.strip()
        if status == "supported" and not evidence_ids:
            status = "unsupported"
            rationale = "No supporting evidence is documented in the candidate profile."
        elif status == "partial" and not evidence_ids:
            status = "unsupported"
            rationale = "No supporting evidence is documented in the candidate profile."
        cleaned.append(
            EvidenceMatch(
                requirement_id=requirement.id,
                status=status,
                evidence_ids=evidence_ids,
                rationale=rationale or "Evidence status was normalized from the current proposal.",
            )
        )
    return cleaned


def remove_adjacent_repeated_words_from_proposal(
    proposal: TailoringProposal,
) -> TailoringProposal:
    """Apply the safe repeated-word cleanup without changing any other content."""
    repaired = proposal.model_copy(deep=True)
    repaired.professional_summary = remove_adjacent_repeated_words(
        repaired.professional_summary
    ).strip()
    for field in (
        "hard_skills",
        "soft_skills",
        "tools_software",
        "industry_knowledge",
    ):
        setattr(
            repaired.skills,
            field,
            [
                remove_adjacent_repeated_words(skill).strip()
                for skill in getattr(repaired.skills, field)
            ],
        )
    for bullet in repaired.bullet_proposals:
        cleaned = remove_adjacent_repeated_words(bullet.proposed_text).strip()
        bullet.proposed_text = (
            normalize_resume_bullet_text(cleaned, max_words=55)
            if has_bullet_structure_artifacts(cleaned)
            else cleaned
        )
    return repaired


def apply_deterministic_repairs(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> TailoringProposal:
    """Normalize every rule enforced by validate_proposal without inventing evidence."""
    repaired = remove_adjacent_repeated_words_from_proposal(proposal)
    repaired.professional_summary = _build_safe_summary(profile, repaired.professional_summary)
    repaired.skills = _clean_skills(profile, analysis, repaired)
    repaired.bullet_proposals = _clean_bullets(profile, analysis, repaired)
    repaired.evidence_matches = _clean_evidence(profile, analysis, repaired)
    repaired = ensure_confirmed_answers_visible(profile, repaired)
    gap_ids = {
        match.requirement_id
        for match in repaired.evidence_matches
        if match.status in {"partial", "unsupported"}
    }
    repaired.unsupported_requirements = [
        requirement.requirement
        for requirement in analysis.requirements
        if requirement.id in gap_ids
    ]
    repaired.candidate_questions = []
    return repaired


def apply_all_until_valid(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    *,
    max_passes: int = 3,
) -> tuple[TailoringProposal, list[AuditIssue]]:
    """Apply bounded deterministic repair passes until validation is stable or clear."""
    repaired = proposal.model_copy(deep=True)
    previous_signature: tuple | None = None
    # Always normalize once so category balancing is applied even when the incoming
    # proposal has no traditional blocking validation issue.
    repaired = apply_deterministic_repairs(profile, analysis, repaired)
    issues = validate_proposal(profile, analysis, repaired)
    for _ in range(max(0, max_passes - 1)):
        if not issues:
            break
        repaired = apply_deterministic_repairs(profile, analysis, repaired)
        issues = validate_proposal(profile, analysis, repaired)
        signature = tuple(
            (item.severity, item.section, item.source_id, item.issue) for item in issues
        )
        if signature == previous_signature:
            break
        previous_signature = signature
    return repaired, issues
