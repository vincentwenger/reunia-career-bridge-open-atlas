from __future__ import annotations

import re

from .bullet_text import (
    has_bullet_structure_artifacts,
    normalize_resume_bullet_text,
    normalize_resume_bullet_terminal_punctuation,
    summarize_confirmation_answer_as_bullet,
)
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
from .bullet_selection import select_job_aligned_bullets
from .proposal_integrity import (
    BULLET_MAPPING_FALLBACK_NOTE,
    DETERMINISTIC_DUPLICATE_PREFIX,
    DETERMINISTIC_EXCLUDE_PREFIX,
    DETERMINISTIC_INCLUDE_PREFIX,
    DETERMINISTIC_TRANSFERABLE_INCLUDE_PREFIX,
)
from .grounding import validate_candidate_claim
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

_RECONCILIATION_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "using",
    "with",
}


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


def _build_safe_summary(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    preferred: str,
) -> str:
    """Return a 3-4 sentence, 50-80 word summary using only source-backed text."""
    cleaned_preferred = remove_adjacent_repeated_words(preferred).strip()
    cleaned_source_summary = remove_adjacent_repeated_words(
        profile.current_summary
    ).strip()
    job_context = "\n".join(
        [
            analysis.target_title,
            analysis.target_company,
            *(requirement.requirement for requirement in analysis.requirements),
            *(keyword for requirement in analysis.requirements for keyword in requirement.keywords),
        ]
    )

    # Preserve a model-written summary only when the complete text is already
    # structurally valid and every claim is traceable to verified evidence.
    if _summary_is_valid(cleaned_preferred) and not validate_candidate_claim(
        cleaned_preferred,
        [profile.all_source_text()],
        context_texts=[job_context],
        allow_gap_context=True,
    ):
        return cleaned_preferred

    # Otherwise compose exclusively from verbatim source-summary sentences,
    # source bullets, and candidate-confirmed supplemental statements.
    source_units: list[str] = []
    source_units.extend(_sentence_parts(cleaned_source_summary))
    source_units.extend(
        remove_adjacent_repeated_words(bullet.text).strip().rstrip(".!?")
        for experience in profile.experiences
        for bullet in experience.bullets
        if remove_adjacent_repeated_words(bullet.text).strip()
    )
    source_units.extend(
        remove_adjacent_repeated_words(item.statement).strip().rstrip(".!?")
        for item in profile.supplemental_evidence
        if remove_adjacent_repeated_words(item.statement).strip()
    )
    source_units = _dedupe(source_units)

    selected: list[str] = []
    for unit in source_units:
        if not unit:
            continue
        selected.append(unit)
        if len(selected) >= 3 and sum(word_count(item) for item in selected) >= 52:
            break
        if len(selected) == 4:
            break

    # Profiles with unusually terse source text can be expanded using only
    # exact verified skill names and exact employer/title values.
    if len(selected) < 3:
        skills = _dedupe(profile.all_verified_skills())
        if skills:
            selected.append("Verified skills: " + ", ".join(skills[:12]))
    if len(selected) < 3:
        for experience in profile.experiences:
            selected.append(
                f"Documented role: {experience.title} at {experience.employer}"
            )
            if len(selected) >= 3:
                break
    while len(selected) < 3:
        selected.append("Documented professional experience from the Verified Resume Evidence")

    selected = selected[:4]
    skills = _dedupe(profile.all_verified_skills())
    if sum(word_count(item) for item in selected) < 50 and skills:
        verified_skill_text = "Verified skills: " + ", ".join(skills[:16])
        if len(selected) < 4:
            selected.append(verified_skill_text)
        else:
            selected[-1] = selected[-1].rstrip(".!?") + "; " + verified_skill_text

    # Trim only the final source-backed sentence to satisfy the upper bound.
    while sum(word_count(item) for item in selected) > 80:
        words = selected[-1].split()
        if len(words) > 8:
            selected[-1] = " ".join(words[:-1])
        elif len(selected) == 4:
            selected.pop()
        else:
            break

    summary = " ".join(_ensure_period(item) for item in selected if item)
    # Do not return a fallback that fails its own grounding gate. A source summary
    # is safer than preserving unsupported generated language, even when terse.
    if validate_candidate_claim(
        summary,
        [profile.all_source_text()],
        context_texts=[job_context],
        allow_gap_context=True,
    ):
        return cleaned_source_summary
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


def _bullet_grounding_evidence(
    profile: CandidateProfile,
    source_text: str,
    evidence_note: str,
) -> list[str]:
    grounding_evidence = [source_text]
    evidence_note_normalized = normalize(evidence_note)
    for evidence_item in profile.supplemental_evidence:
        if normalize(evidence_item.id) in evidence_note_normalized:
            grounding_evidence.append(evidence_item.statement)
            grounding_evidence.extend(evidence_item.verified_skills)
    for verified_skill in profile.all_verified_skills():
        if normalize(verified_skill) in evidence_note_normalized:
            grounding_evidence.append(verified_skill)
    return grounding_evidence


def _reconciliation_token(value: str) -> str:
    """Return a conservative lightweight stem for requirement comparison."""

    token = value.casefold()
    concept_prefixes = {
        "resolut": "resolv",
        "resolv": "resolv",
        "troubleshoot": "troubleshoot",
        "transform": "transform",
        "integrat": "integrat",
        "aggregat": "aggregat",
        "collaborat": "collaborat",
        "automat": "automat",
        "analy": "analy",
    }
    for prefix, normalized in concept_prefixes.items():
        if token.startswith(prefix):
            return normalized
    if token.endswith("ies") and len(token) > 5:
        token = token[:-3] + "y"
    elif token.endswith("ing") and len(token) > 6:
        token = token[:-3]
    elif token.endswith("ed") and len(token) > 5:
        token = token[:-2]
    elif token.endswith("es") and len(token) > 5:
        token = token[:-2]
    elif token.endswith("s") and len(token) > 4:
        token = token[:-1]
    return token


def _reconciliation_tokens(value: str) -> set[str]:
    return {
        _reconciliation_token(token)
        for token in re.findall(r"[a-z0-9+#.]+", value.casefold())
        if len(token) >= 3 and token not in _RECONCILIATION_STOPWORDS
    }


def _infer_requirement_ids_for_bullet(
    source_id: str,
    source_text: str,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> list[str]:
    """Infer conservative requirement links for an omitted selection record.

    Evidence-match links are authoritative. A bounded lexical fallback is used only
    when at least two meaningful requirement concepts appear in the verified bullet.
    """

    inferred: set[str] = {
        match.requirement_id
        for match in proposal.evidence_matches
        if match.status != "unsupported" and source_id in match.evidence_ids
    }
    source_tokens = _reconciliation_tokens(source_text)
    for requirement in analysis.requirements:
        requirement_tokens = _reconciliation_tokens(
            " ".join([requirement.requirement, *requirement.keywords])
        )
        if len(source_tokens & requirement_tokens) >= 2:
            inferred.add(requirement.id)
    return [
        requirement.id
        for requirement in analysis.requirements
        if requirement.id in inferred
    ]


def repair_unsupported_candidate_claims(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> TailoringProposal:
    """Remove unsupported generated candidate claims without changing workflow metadata.

    Unlike the full deterministic repair pass, this function preserves candidate
    questions, inclusion choices, evidence decisions, and Baseline Resume
    findings. It is safe to run immediately after every model-generated proposal.
    """

    repaired = proposal.model_copy(deep=True)
    repaired.professional_summary = _build_safe_summary(
        profile,
        analysis,
        repaired.professional_summary,
    )
    source_lookup = profile.bullet_lookup()
    for bullet in repaired.bullet_proposals:
        source_text = source_lookup.get(bullet.source_bullet_id)
        if source_text is None or not bullet.include:
            continue
        findings = validate_candidate_claim(
            bullet.proposed_text,
            _bullet_grounding_evidence(profile, source_text, bullet.evidence_note),
            require_overlap=True,
        )
        if findings:
            bullet.proposed_text = source_text.strip()
            bullet.evidence_note = (
                bullet.evidence_note.strip()
                or f"Directly supported by source bullet {bullet.source_bullet_id}."
            )
    return repaired


def _clean_bullets(
    profile: CandidateProfile, analysis: JobAnalysis, proposal: TailoringProposal
) -> list[BulletProposal]:
    """Map every verified bullet, then select the job-aligned set deterministically.

    The AI-generated ``include`` value is intentionally ignored. The model may map
    requirements and propose grounded wording, while one simple two-pass selector
    controls which bullets appear in the Job-Aligned Resume.
    """

    source_lookup = profile.bullet_lookup()
    confirmed_source_ids = {
        evidence.source_bullet_id
        for evidence in profile.supplemental_evidence
        if evidence.source == "candidate_confirmation" and evidence.source_bullet_id
    }
    requirement_ids = {item.id for item in analysis.requirements}
    requirement_lookup = {item.id: item for item in analysis.requirements}
    first_by_id: dict[str, BulletProposal] = {}
    for item in proposal.bullet_proposals:
        if item.source_bullet_id in source_lookup and item.source_bullet_id not in first_by_id:
            first_by_id[item.source_bullet_id] = item

    mapped: list[BulletProposal] = []
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
            use_past_tense = not bool(
                re.search(
                    r"\b(?:present|current|now|ongoing|today|actuel|actuelle|actuellement|en cours)\b",
                    experience.dates or "",
                    re.IGNORECASE,
                )
            )
            if source_is_confirmed:
                source_text = summarize_confirmation_answer_as_bullet(
                    source_text,
                    max_words=35,
                    use_past_tense=use_past_tense,
                )
            elif has_bullet_structure_artifacts(source_text):
                source_text = normalize_resume_bullet_text(source_text, max_words=55)

            if existing is None:
                proposed_text = source_text
                existing_matches: list[str] = []
                evidence_note = BULLET_MAPPING_FALLBACK_NOTE
            else:
                proposed_text = remove_adjacent_repeated_words(existing.proposed_text).strip()
                if source_is_confirmed:
                    proposed_text = summarize_confirmation_answer_as_bullet(
                        proposed_text,
                        max_words=35,
                        use_past_tense=use_past_tense,
                    )
                elif has_bullet_structure_artifacts(proposed_text):
                    proposed_text = normalize_resume_bullet_text(
                        proposed_text,
                        max_words=55,
                    )
                source_numbers = numeric_tokens(source.text)
                new_numbers = numeric_tokens(proposed_text) - source_numbers - supplemental_numbers
                grounding_findings = (
                    validate_candidate_claim(
                        proposed_text,
                        _bullet_grounding_evidence(
                            profile, source.text, existing.evidence_note
                        ),
                        require_overlap=True,
                    )
                    if proposed_text
                    else []
                )
                if (
                    not proposed_text
                    or new_numbers
                    or grounding_findings
                    or word_count(proposed_text) > 55
                ):
                    proposed_text = source_text
                existing_matches = [
                    requirement_id
                    for requirement_id in _dedupe(existing.matched_requirement_ids)
                    if requirement_id in requirement_ids
                ]
                evidence_note = existing.evidence_note.strip() or (
                    f"Directly supported by source bullet {source.id}."
                )

            proposed_text = normalize_resume_bullet_terminal_punctuation(proposed_text)

            # Evidence-match links and conservative lexical matches are combined for
            # every bullet, not only malformed or omitted model records.
            matched = _dedupe(
                [
                    *existing_matches,
                    *_infer_requirement_ids_for_bullet(
                        source.id,
                        source.text,
                        analysis,
                        proposal,
                    ),
                ]
            )
            mapped.append(
                BulletProposal(
                    source_bullet_id=source.id,
                    include=False,
                    proposed_text=proposed_text,
                    matched_requirement_ids=matched,
                    evidence_note=evidence_note,
                )
            )

    by_id = {item.source_bullet_id: item for item in mapped}
    limits = [(6, 7), (3, 4), (2, 3)]

    for index, experience in enumerate(profile.experiences):
        minimum, maximum = limits[index] if index < len(limits) else (2, 3)
        items = [by_id[bullet.id] for bullet in experience.bullets]
        source_order = {
            bullet.id: position for position, bullet in enumerate(experience.bullets)
        }
        selection = select_job_aligned_bullets(
            items,
            analysis.requirements,
            source_order=source_order,
            confirmed_source_ids=confirmed_source_ids,
            minimum_count=minimum,
            maximum_count=maximum,
        )

        item_lookup = {item.source_bullet_id: item for item in items}
        for item in items:
            selected = item.source_bullet_id in selection.selected_ids
            duplicate = item.source_bullet_id in selection.duplicate_ids
            labels = [
                requirement_lookup[requirement_id].requirement
                for requirement_id in item.matched_requirement_ids
                if requirement_id in requirement_lookup
            ]
            score = selection.scores[item.source_bullet_id]
            score_detail = (
                f"Job relevance {score.relevance}/3; evidence strength "
                f"{score.evidence_strength}/2; unique coverage "
                f"{score.unique_coverage}/2."
            )

            selected_instead_ids = (
                []
                if selected
                else list(
                    selection.selected_instead_ids.get(item.source_bullet_id, ())
                )
            )
            comparison_reasons: dict[str, list[str]] = {}
            item_requirements = set(item.matched_requirement_ids)
            for selected_id in selected_instead_ids:
                other = item_lookup[selected_id]
                other_score = selection.scores[selected_id]
                other_requirements = set(other.matched_requirement_ids)
                shared_ids = item_requirements & other_requirements
                additional_ids = other_requirements - item_requirements
                reasons: list[str] = []
                if shared_ids:
                    shared_labels = [
                        requirement_lookup[requirement_id].requirement
                        for requirement_id in shared_ids
                        if requirement_id in requirement_lookup
                    ]
                    if shared_labels:
                        reasons.append(
                            "Supports the same requirement: "
                            + "; ".join(shared_labels)
                        )
                if other_score.unique_coverage > score.unique_coverage:
                    reasons.append("Provides more unique job-requirement coverage")
                if other_score.evidence_strength > score.evidence_strength:
                    reasons.append("Provides stronger or more specific evidence")
                if other_score.relevance > score.relevance:
                    reasons.append("Has a stronger direct job match")
                if additional_ids:
                    additional_labels = [
                        requirement_lookup[requirement_id].requirement
                        for requirement_id in additional_ids
                        if requirement_id in requirement_lookup
                    ]
                    if additional_labels:
                        reasons.append(
                            "Also covers: " + "; ".join(additional_labels)
                        )
                if duplicate and not reasons:
                    reasons.append("Covers substantially similar evidence with less duplication")
                if not reasons:
                    reasons.append(
                        "Ranked higher after the deterministic tie-break within the role's bullet limit"
                    )
                comparison_reasons[selected_id] = reasons

            if selected and labels:
                note = (
                    f"{DETERMINISTIC_INCLUDE_PREFIX} Supports "
                    + "; ".join(labels)
                    + f" with specific verified evidence. {score_detail}"
                )
            elif selected:
                note = (
                    f"{DETERMINISTIC_TRANSFERABLE_INCLUDE_PREFIX} Selected to complete "
                    "the role with specific, non-duplicative verified evidence. "
                    + score_detail
                )
            elif duplicate:
                requirement_text = (
                    " It supports " + "; ".join(labels) + ", but"
                    if labels
                    else " It"
                )
                comparison_text = (
                    " The higher-ranked related accomplishments are identified below."
                    if selected_instead_ids
                    else ""
                )
                note = (
                    f"{DETERMINISTIC_DUPLICATE_PREFIX}{requirement_text} overlaps with a "
                    "stronger selected accomplishment. The source evidence remains available "
                    "for manual restoration."
                    + comparison_text
                    + " "
                    + score_detail
                )
            elif labels:
                comparison_text = (
                    " The higher-ranked related accomplishments are identified below."
                    if selected_instead_ids
                    else ""
                )
                note = (
                    f"{DETERMINISTIC_EXCLUDE_PREFIX} This accomplishment supports "
                    + "; ".join(labels)
                    + ", but other selected evidence ranked higher or covered the same "
                    "requirement more uniquely within the available resume space."
                    + comparison_text
                    + " "
                    + score_detail
                )
            else:
                note = (
                    f"{DETERMINISTIC_EXCLUDE_PREFIX} This accomplishment is valid, but it "
                    "does not directly support a target-job requirement and ranked below "
                    "matched evidence within the available resume space. "
                    + score_detail
                )

            by_id[item.source_bullet_id] = item.model_copy(
                update={
                    "include": selected,
                    "evidence_note": note,
                    "selected_instead_ids": selected_instead_ids,
                    "selection_comparison_reasons": comparison_reasons,
                }
            )

    return [
        by_id[bullet.id]
        for experience in profile.experiences
        for bullet in experience.bullets
    ]


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
        normalized = (
            normalize_resume_bullet_text(cleaned, max_words=55)
            if has_bullet_structure_artifacts(cleaned)
            else cleaned
        )
        bullet.proposed_text = normalize_resume_bullet_terminal_punctuation(normalized)
    return repaired


def apply_deterministic_repairs(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> TailoringProposal:
    """Normalize every rule enforced by validate_proposal without inventing evidence."""
    repaired = remove_adjacent_repeated_words_from_proposal(proposal)
    repaired.professional_summary = _build_safe_summary(
        profile, analysis, repaired.professional_summary
    )
    repaired.skills = _clean_skills(profile, analysis, repaired)
    repaired.bullet_proposals = _clean_bullets(profile, analysis, repaired)
    repaired.evidence_matches = _clean_evidence(profile, analysis, repaired)
    repaired = ensure_confirmed_answers_visible(profile, repaired)
    for bullet in repaired.bullet_proposals:
        bullet.proposed_text = normalize_resume_bullet_terminal_punctuation(
            bullet.proposed_text
        )
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
