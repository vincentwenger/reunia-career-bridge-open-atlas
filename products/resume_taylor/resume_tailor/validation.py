from __future__ import annotations

import re
from collections import Counter

from .bullet_text import has_bullet_structure_artifacts
from .grounding import validate_candidate_claim
from .models import (
    ApprovedResume,
    AuditIssue,
    CandidateProfile,
    JobAnalysis,
    ProposalAudit,
    SkillSet,
    TailoringProposal,
)
from .skill_rules import SKILL_TOTAL_MAXIMUM


def normalize(value: str) -> str:
    value = value.casefold().strip()
    value = re.sub(r"[^\w+#./-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def sentence_count(text: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()])


def numeric_tokens(text: str) -> set[str]:
    return set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)", text))


_ADJACENT_REPEATED_WORD_RE = re.compile(
    r"\b(?P<word>[A-Za-z][A-Za-z'’\-]*)\b(?P<gap>\s+)(?P=word)\b",
    re.IGNORECASE,
)


def adjacent_repeated_words(text: str) -> list[str]:
    """Return unique adjacent repeated words in their first-seen order."""
    seen: set[str] = set()
    repeated: list[str] = []
    for match in _ADJACENT_REPEATED_WORD_RE.finditer(text or ""):
        word = match.group("word")
        key = word.casefold()
        if key not in seen:
            repeated.append(word)
            seen.add(key)
    return repeated


def remove_adjacent_repeated_words(text: str) -> str:
    """Remove safe, exact adjacent word repetitions such as ``and and``."""
    cleaned = text or ""
    while _ADJACENT_REPEATED_WORD_RE.search(cleaned):
        cleaned = _ADJACENT_REPEATED_WORD_RE.sub(
            lambda match: match.group("word"),
            cleaned,
        )
    return cleaned


def _repeated_word_issue(
    *,
    section: str,
    text: str,
    source_id: str = "",
) -> AuditIssue | None:
    repeated = adjacent_repeated_words(text)
    if not repeated:
        return None
    pairs = ", ".join(f"'{word} {word}'" for word in repeated)
    return AuditIssue(
        severity="blocking",
        section=section,
        source_id=source_id,
        issue=f"Adjacent repeated word(s) found: {pairs}.",
        suggested_fix="Remove the second occurrence of each adjacent repeated word.",
    )


def proposed_skills(skill_set: SkillSet) -> list[str]:
    return (
        skill_set.hard_skills
        + skill_set.soft_skills
        + skill_set.tools_software
        + skill_set.industry_knowledge
    )


def validate_proposal(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []

    summary_words = word_count(proposal.professional_summary)
    if not 50 <= summary_words <= 80:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Professional Summary",
                issue=f"Summary has {summary_words} words; required range is 50-80.",
                suggested_fix="Shorten or expand the summary without adding unsupported claims.",
            )
        )
    summary_sentences = sentence_count(proposal.professional_summary)
    if summary_sentences not in (3, 4):
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Professional Summary",
                issue=f"Summary has {summary_sentences} sentences; required count is 3 or 4.",
                suggested_fix="Use three or four complete sentences.",
            )
        )
    summary_repetition = _repeated_word_issue(
        section="Professional Summary",
        text=proposal.professional_summary,
    )
    if summary_repetition is not None:
        issues.append(summary_repetition)

    job_context = "\n".join(
        [
            analysis.target_title,
            analysis.target_company,
            *(requirement.requirement for requirement in analysis.requirements),
            *(keyword for requirement in analysis.requirements for keyword in requirement.keywords),
        ]
    )
    for finding in validate_candidate_claim(
        proposal.professional_summary,
        [profile.all_source_text()],
        context_texts=[job_context],
        allow_gap_context=True,
    ):
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Professional Summary",
                issue=finding.message,
                suggested_fix=(
                    "Remove or rewrite the unsupported claim using only facts present in "
                    "the Candidate Profile or candidate-confirmed evidence."
                ),
            )
        )

    title_repetition = _repeated_word_issue(
        section="Target Title",
        text=analysis.target_title,
    )
    if title_repetition is not None:
        issues.append(title_repetition)

    skills = proposed_skills(proposal.skills)
    if len(skills) > SKILL_TOTAL_MAXIMUM:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Skills",
                issue=f"Proposal contains {len(skills)} skills; maximum is {SKILL_TOTAL_MAXIMUM}.",
                suggested_fix="Keep only the most relevant verified skills.",
            )
        )
    normalized_skills = [normalize(skill) for skill in skills]
    duplicates = [item for item, count in Counter(normalized_skills).items() if item and count > 1]
    if duplicates:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Skills",
                issue="Duplicate skills are present: " + ", ".join(sorted(duplicates)),
                suggested_fix="Remove duplicate skills.",
            )
        )

    verified = {
        normalize(remove_adjacent_repeated_words(skill)): remove_adjacent_repeated_words(skill).strip()
        for skill in profile.all_verified_skills()
    }
    for skill in skills:
        skill_repetition = _repeated_word_issue(
            section="Skills",
            text=skill,
            source_id=skill,
        )
        if skill_repetition is not None:
            issues.append(skill_repetition)
        if normalize(skill) not in verified:
            issues.append(
                AuditIssue(
                    severity="blocking",
                    section="Skills",
                    source_id=skill,
                    issue=f"'{skill}' is not in the candidate's verified skills.",
                    suggested_fix="Remove it or add it to the source profile only after the candidate confirms it.",
                )
            )

    source_bullets = profile.bullet_lookup()
    proposal_ids = [item.source_bullet_id for item in proposal.bullet_proposals]
    expected_ids = set(source_bullets)
    actual_ids = set(proposal_ids)
    missing_ids = sorted(expected_ids - actual_ids)
    unknown_ids = sorted(actual_ids - expected_ids)
    duplicate_ids = sorted(item for item, count in Counter(proposal_ids).items() if count > 1)
    if missing_ids:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Experience",
                issue="Missing source bullet proposals: " + ", ".join(missing_ids),
                suggested_fix="Return one proposal for every source bullet.",
            )
        )
    if unknown_ids:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Experience",
                issue="Unknown source bullet IDs: " + ", ".join(unknown_ids),
                suggested_fix="Use only IDs from the candidate profile.",
            )
        )
    if duplicate_ids:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Experience",
                issue="Duplicate source bullet IDs: " + ", ".join(duplicate_ids),
                suggested_fix="Return each source bullet exactly once.",
            )
        )

    requirement_ids = {requirement.id for requirement in analysis.requirements}
    for bullet in proposal.bullet_proposals:
        if bullet.source_bullet_id not in source_bullets:
            continue
        if not bullet.proposed_text.strip():
            issues.append(
                AuditIssue(
                    severity="blocking",
                    section="Experience",
                    source_id=bullet.source_bullet_id,
                    issue="Proposed bullet is empty.",
                    suggested_fix="Restore the source bullet or write an evidence-supported revision.",
                )
            )
            continue
        if bullet.include:
            if has_bullet_structure_artifacts(bullet.proposed_text):
                issues.append(
                    AuditIssue(
                        severity="blocking",
                        section="Experience",
                        source_id=bullet.source_bullet_id,
                        issue=(
                            "Bullet contains markdown, a nested list, or multiple paragraphs "
                            "instead of one plain resume statement."
                        ),
                        suggested_fix=(
                            "Rewrite it as one plain-text, action-led resume bullet with no "
                            "heading, label, bullet symbol, or line break."
                        ),
                    )
                )
            bullet_repetition = _repeated_word_issue(
                section="Experience",
                text=bullet.proposed_text,
                source_id=bullet.source_bullet_id,
            )
            if bullet_repetition is not None:
                issues.append(bullet_repetition)
        grounding_evidence = [source_bullets[bullet.source_bullet_id]]
        evidence_note_normalized = normalize(bullet.evidence_note)
        for evidence_item in profile.supplemental_evidence:
            if normalize(evidence_item.id) in evidence_note_normalized:
                grounding_evidence.append(evidence_item.statement)
                grounding_evidence.extend(evidence_item.verified_skills)
        for verified_skill in profile.all_verified_skills():
            if normalize(verified_skill) in evidence_note_normalized:
                grounding_evidence.append(verified_skill)
        if bullet.include:
            for finding in validate_candidate_claim(
                bullet.proposed_text,
                grounding_evidence,
                require_overlap=True,
            ):
                issues.append(
                    AuditIssue(
                        severity="blocking",
                        section="Experience",
                        source_id=bullet.source_bullet_id,
                        issue=finding.message,
                        suggested_fix=(
                            "Restore the source bullet wording or rewrite it using only the cited "
                            "source bullet and explicitly referenced verified evidence."
                        ),
                    )
                )

        source_numbers = numeric_tokens(source_bullets[bullet.source_bullet_id])
        confirmed_numbers = {
            token
            for evidence in profile.supplemental_evidence
            for token in numeric_tokens(evidence.statement)
        }
        new_numbers = numeric_tokens(bullet.proposed_text) - source_numbers - confirmed_numbers
        if new_numbers:
            issues.append(
                AuditIssue(
                    severity="blocking",
                    section="Experience",
                    source_id=bullet.source_bullet_id,
                    issue="Proposed bullet introduces new number(s): " + ", ".join(sorted(new_numbers)),
                    suggested_fix="Use only numbers present in the source bullet.",
                )
            )
        invalid_requirements = sorted(set(bullet.matched_requirement_ids) - requirement_ids)
        if invalid_requirements:
            issues.append(
                AuditIssue(
                    severity="blocking",
                    section="Experience",
                    source_id=bullet.source_bullet_id,
                    issue="Bullet references unknown job requirements: " + ", ".join(invalid_requirements),
                    suggested_fix="Use only requirement IDs from the job analysis.",
                )
            )
        if bullet.include and word_count(bullet.proposed_text) > 55:
            issues.append(
                AuditIssue(
                    severity="warning",
                    section="Experience",
                    source_id=bullet.source_bullet_id,
                    issue=f"Bullet is long ({word_count(bullet.proposed_text)} words).",
                    suggested_fix=(
                        "Shorten it to a concise action-led statement, ideally 35 words or fewer, "
                        "while preserving all supported facts and metrics."
                    ),
                )
            )

    proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
    for evidence in profile.supplemental_evidence:
        if evidence.source != "candidate_confirmation" or not evidence.source_bullet_id:
            continue
        own = proposal_lookup.get(evidence.source_bullet_id)
        represented_elsewhere = any(
            item.include
            and item.source_bullet_id != evidence.source_bullet_id
            and (
                evidence.id.casefold() in item.evidence_note.casefold()
                or evidence.source_bullet_id.casefold() in item.evidence_note.casefold()
            )
            for item in proposal.bullet_proposals
        )
        if not ((own is not None and own.include) or represented_elsewhere):
            issues.append(
                AuditIssue(
                    severity="blocking",
                    section="Confirmed Experience",
                    source_id=evidence.source_bullet_id,
                    issue=(
                        f"Candidate-confirmed experience {evidence.id} is not represented "
                        "in the visible resume."
                    ),
                    suggested_fix=(
                        "Include its candidate-confirmed bullet or cite the confirmation "
                        "in an included bullet under the selected job."
                    ),
                )
            )

    by_experience: dict[str, int] = {experience.id: 0 for experience in profile.experiences}
    bullet_to_experience = {
        bullet.id: experience.id
        for experience in profile.experiences
        for bullet in experience.bullets
    }
    for bullet in proposal.bullet_proposals:
        if bullet.include and bullet.source_bullet_id in bullet_to_experience:
            by_experience[bullet_to_experience[bullet.source_bullet_id]] += 1

    for index, experience in enumerate(profile.experiences):
        minimum, maximum = _experience_bullet_limit(index)
        count = by_experience[experience.id]
        if not minimum <= count <= maximum:
            issues.append(
                AuditIssue(
                    severity="blocking",
                    section="Experience",
                    source_id=experience.id,
                    issue=(
                        f"{experience.employer} has {count} selected bullets; "
                        f"required range is {minimum}-{maximum}."
                    ),
                    suggested_fix="Select the strongest relevant bullets within the required range.",
                )
            )

    evidence_ids = [match.requirement_id for match in proposal.evidence_matches]
    missing_evidence = sorted(requirement_ids - set(evidence_ids))
    unknown_evidence = sorted(set(evidence_ids) - requirement_ids)
    duplicate_evidence = sorted(item for item, count in Counter(evidence_ids).items() if count > 1)
    if missing_evidence:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Evidence Matrix",
                issue="Missing evidence decisions for: " + ", ".join(missing_evidence),
                suggested_fix="Classify every job requirement as supported, partial, or unsupported.",
            )
        )
    if unknown_evidence:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Evidence Matrix",
                issue="Unknown requirement IDs in evidence matrix: " + ", ".join(unknown_evidence),
                suggested_fix="Use only requirement IDs from the job analysis.",
            )
        )
    if duplicate_evidence:
        issues.append(
            AuditIssue(
                severity="blocking",
                section="Evidence Matrix",
                issue="Duplicate evidence decisions for: " + ", ".join(duplicate_evidence),
                suggested_fix="Return exactly one evidence decision per requirement.",
            )
        )

    return issues


def _normalized_audit_text(issue: AuditIssue) -> str:
    return re.sub(
        r"\s+",
        " ",
        " ".join(
            [issue.section, issue.source_id, issue.issue, issue.suggested_fix]
        )
        .replace("_", " ")
        .replace("–", "-")
        .casefold(),
    ).strip()


def _canonical_audit_section(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _audit_source_ids(value: str) -> list[str]:
    """Split a model-generated source field into individual durable IDs."""

    cleaned = re.sub(r"\b(?:and|or)\b", ",", value, flags=re.IGNORECASE)
    return [
        part.strip().strip("()[]{}")
        for part in re.split(r"[,;/|]+", cleaned)
        if part.strip().strip("()[]{}")
    ]


def _visible_proposal_segments(proposal: TailoringProposal) -> list[str]:
    """Return only text that is visible in the generated Final resume."""

    segments = [proposal.professional_summary]
    segments.extend(proposed_skills(proposal.skills))
    segments.extend(
        item.proposed_text
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    )
    return [segment for segment in segments if segment.strip()]


_CLAIM_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def _claim_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in _CLAIM_STOPWORDS and len(token) > 1
    }


def _claim_is_visible(claim: str, proposal: TailoringProposal) -> bool:
    """Return whether a named claim is materially present in visible resume text."""

    claim_tokens = _claim_tokens(claim)
    if not claim_tokens:
        return True

    normalized_claim = normalize(claim)
    for segment in _visible_proposal_segments(proposal):
        normalized_segment = normalize(segment)
        if normalized_claim and normalized_claim in normalized_segment:
            return True
        segment_tokens = _claim_tokens(segment)
        overlap = len(claim_tokens & segment_tokens)
        required = max(2, (len(claim_tokens) + 1) // 2)
        if overlap >= required:
            return True
    return False


def _explicit_removal_targets(issue: AuditIssue) -> list[str]:
    """Extract only concrete phrases that a finding explicitly says to remove."""

    text = issue.suggested_fix.strip()
    targets: list[str] = []
    quoted_patterns = (
        r"\b(?:remove|delete|omit|exclude|eliminate|drop)\b[^\n]*?"
        r"\b(?:phrase|wording|text|claim)\b[^\n]*?[\"'“](.+?)[\"'”]",
        r"\b(?:remove|delete|omit|exclude|eliminate|drop)\b[^\n]*?"
        r"\b(?:references?\s+to|mentions?\s+of)\s+[\"'“]?(.+?)[\"'”]?(?:;|\n|$)",
    )
    for pattern in quoted_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            target = match.group(1).strip(" \t\r\n'\"“”.,;:")
            if target:
                targets.append(target)
    return targets


def _referenced_requirement_ids(
    issue: AuditIssue,
    analysis: JobAnalysis,
) -> set[str]:
    requirement_ids = {requirement.id for requirement in analysis.requirements}
    candidates = set(_audit_source_ids(issue.source_id))
    candidates.update(
        re.findall(
            r"\bR\d+\b",
            f"{issue.issue} {issue.suggested_fix}",
            flags=re.IGNORECASE,
        )
    )
    normalized_lookup = {item.casefold(): item for item in requirement_ids}
    return {
        normalized_lookup[candidate.casefold()]
        for candidate in candidates
        if candidate.casefold() in normalized_lookup
    }


def _is_unsupported_requirement(
    proposal: TailoringProposal,
    requirement_id: str,
) -> bool:
    match = next(
        (
            item
            for item in proposal.evidence_matches
            if item.requirement_id == requirement_id
        ),
        None,
    )
    if match is not None:
        return match.status == "unsupported"
    return any(
        requirement_id.casefold() in item.casefold()
        for item in proposal.unsupported_requirements
    )


def _malformed_semantic_audit_finding(
    issue: AuditIssue,
    proposal: TailoringProposal,
    analysis: JobAnalysis | None,
) -> bool:
    """Reject findings that confuse job requirements with visible resume claims."""

    section = _canonical_audit_section(issue.section)
    if section in {"bullet_proposal", "bullet_proposals"} and issue.source_id.strip():
        proposal_ids = {item.source_bullet_id for item in proposal.bullet_proposals}
        source_ids = _audit_source_ids(issue.source_id)
        if not source_ids or any(source_id not in proposal_ids for source_id in source_ids):
            return True

    removal_targets = _explicit_removal_targets(issue)
    if removal_targets and all(
        not _claim_is_visible(target, proposal) for target in removal_targets
    ):
        return True

    if analysis is None:
        return False

    requirement_lookup = {
        requirement.id: requirement for requirement in analysis.requirements
    }
    referenced_requirements = _referenced_requirement_ids(issue, analysis)
    if not referenced_requirements:
        return False

    normalized_issue = _normalized_audit_text(issue)
    missing_evidence_language = any(
        marker in normalized_issue
        for marker in (
            "lacks evidence",
            "lacks specific evidence",
            "no evidence",
            "not supported",
            "unsupported requirement",
            "missing evidence",
        )
    )
    removal_language = bool(removal_targets) or bool(
        re.search(
            r"\b(?:remove|delete|omit|exclude|eliminate|drop)\b",
            issue.suggested_fix,
            flags=re.IGNORECASE,
        )
    )
    if not (missing_evidence_language and removal_language):
        return False

    for requirement_id in referenced_requirements:
        requirement = requirement_lookup[requirement_id]
        claim_texts = [requirement.requirement, *requirement.keywords]
        if not _is_unsupported_requirement(proposal, requirement_id):
            return False
        if any(_claim_is_visible(claim, proposal) for claim in claim_texts if claim.strip()):
            return False
    return True


def _is_professional_summary_audit_issue(issue: AuditIssue) -> bool:
    text = _normalized_audit_text(issue)
    return "professional summary" in text or issue.section.strip().casefold() == "summary"


def _is_summary_length_finding(issue: AuditIssue) -> bool:
    if not _is_professional_summary_audit_issue(issue):
        return False
    text = _normalized_audit_text(issue)
    return any(
        marker in text
        for marker in (
            "word count",
            " words",
            "50 to 80",
            "50-80",
            "recommended length",
            "too long",
            "too short",
            "word limit",
            "word range",
        )
    )


def _is_summary_sentence_finding(issue: AuditIssue) -> bool:
    if not _is_professional_summary_audit_issue(issue):
        return False
    text = _normalized_audit_text(issue)
    return "sentence" in text and any(
        marker in text for marker in ("3 or 4", "three or four", "3-4")
    )


def _is_experience_bullet_count_finding(issue: AuditIssue) -> bool:
    """Return whether an AI audit finding is specifically about role bullet counts."""

    text = _normalized_audit_text(issue)
    if "bullet" not in text:
        return False
    if "word" in text and not any(
        marker in text for marker in ("most recent role", "second role", "selected bullets")
    ):
        return False
    return any(
        marker in text
        for marker in (
            "bullet count",
            "bullet counts",
            "selected bullets",
            "recommended count",
            "recommended range",
            "most recent role",
            "second role",
            "6-7",
            "3-4",
        )
    )


def _experience_bullet_limit(index: int) -> tuple[int, int]:
    limits = ((6, 7), (3, 4), (2, 3))
    return limits[index] if index < len(limits) else (2, 3)


def _included_bullet_counts(
    profile: CandidateProfile,
    proposal: TailoringProposal,
) -> list[tuple[str, str, int, int, int]]:
    """Count included bullets in the same role and source order used by Word export."""

    proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
    counts: list[tuple[str, str, int, int, int]] = []
    for index, experience in enumerate(profile.experiences):
        count = sum(
            1
            for source_bullet in experience.bullets
            if (item := proposal_lookup.get(source_bullet.id)) is not None and item.include
        )
        minimum, maximum = _experience_bullet_limit(index)
        counts.append((experience.id, experience.employer, count, minimum, maximum))
    return counts


_DETERMINISTIC_CATEGORIES = {
    "summary_length",
    "summary_sentences",
    "skills_count",
    "skills_duplicates",
    "skills_verified",
    "bullet_source_ids",
    "bullet_empty",
    "bullet_numbers",
    "bullet_requirement_ids",
    "bullet_length",
    "experience_counts",
    "evidence_matrix_ids",
}


def _deterministic_audit_categories(issue: AuditIssue) -> set[str]:
    """Classify objective findings that must be decided by local code, not the AI."""

    text = _normalized_audit_text(issue)
    categories: set[str] = set()

    if _is_summary_length_finding(issue):
        categories.add("summary_length")
    if _is_summary_sentence_finding(issue):
        categories.add("summary_sentences")

    skills_context = "skill" in text or issue.section.strip().casefold() == "skills"
    if skills_context:
        if any(
            marker in text
            for marker in (
                "total counted skills",
                "total skills",
                "skills total",
                "more than 16",
                "exceed 16",
                "exceeds 16",
                "maximum of 16",
                "maximum is 16",
                "no more than 16",
                "more than 30",
                "exceed 30",
                "exceeds 30",
                "maximum of 30",
                "maximum is 30",
                "no more than 30",
                "count limit",
                "skill count",
            )
        ):
            categories.add("skills_count")
        if "duplicate" in text or "duplicated" in text:
            categories.add("skills_duplicates")
        if any(
            marker in text
            for marker in (
                "not found in the verified candidate profile",
                "not found in the candidate profile",
                "not in the candidate's verified skills",
                "not in the candidates verified skills",
                "not in the verified skills",
                "not verified in the candidate profile",
                "unverified skill",
                "unsupported skill",
                "skills are not supported",
                "skill is not supported",
                "skills listed not found",
            )
        ):
            categories.add("skills_verified")

    if _is_experience_bullet_count_finding(issue):
        categories.add("experience_counts")

    bullet_context = "bullet" in text or "experience" in issue.section.strip().casefold()
    if bullet_context:
        if any(
            marker in text
            for marker in (
                "missing source bullet",
                "unknown source bullet",
                "duplicate source bullet",
                "one proposal for every source bullet",
                "every source bullet",
                "source bullet id",
            )
        ):
            categories.add("bullet_source_ids")
        if "empty bullet" in text or "bullet is empty" in text or "proposed bullet is empty" in text:
            categories.add("bullet_empty")
        if any(
            marker in text
            for marker in (
                "introduces new number",
                "introduce new number",
                "new number(s)",
                "new numbers",
                "number appears in its source bullet",
                "numbers present in the source bullet",
                "unsupported numerical",
            )
        ):
            categories.add("bullet_numbers")
        if any(
            marker in text
            for marker in (
                "unknown job requirement",
                "invalid requirement id",
                "unknown requirement id",
                "requirement ids from the job analysis",
            )
        ):
            categories.add("bullet_requirement_ids")
        if any(
            marker in text
            for marker in (
                "above 55 words",
                "over 55 words",
                "more than 55 words",
                "55-word",
                "55 word",
                "bullet is long",
                "bullet length",
                "bullet word count",
            )
        ):
            categories.add("bullet_length")

    evidence_context = "evidence matrix" in text or "evidence decision" in text
    if evidence_context and any(
        marker in text
        for marker in (
            "missing evidence",
            "unknown requirement",
            "duplicate evidence",
            "one evidence decision",
            "every job requirement",
            "evidence match per",
        )
    ):
        categories.add("evidence_matrix_ids")

    return categories & _DETERMINISTIC_CATEGORIES


def _objective_rule_statuses(
    proposal: TailoringProposal,
    profile: CandidateProfile | None,
    analysis: JobAnalysis | None,
) -> dict[str, bool | None]:
    """Return True/False for objective rules, or None when inputs are unavailable."""

    skills = proposed_skills(proposal.skills)
    normalized_skills = [normalize(skill) for skill in skills]
    statuses: dict[str, bool | None] = {
        "summary_length": 50 <= word_count(proposal.professional_summary) <= 80,
        "summary_sentences": sentence_count(proposal.professional_summary) in (3, 4),
        "skills_count": len(skills) <= SKILL_TOTAL_MAXIMUM,
        "skills_duplicates": len([item for item in normalized_skills if item])
        == len(set(item for item in normalized_skills if item)),
        "skills_verified": None,
        "bullet_source_ids": None,
        "bullet_empty": all(item.proposed_text.strip() for item in proposal.bullet_proposals),
        "bullet_numbers": None,
        "bullet_requirement_ids": None,
        "bullet_length": all(word_count(item.proposed_text) <= 55 for item in proposal.bullet_proposals),
        "experience_counts": None,
        "confirmed_experience_visible": None,
        "evidence_matrix_ids": None,
    }

    if profile is not None:
        verified = {normalize(skill) for skill in profile.all_verified_skills()}
        statuses["skills_verified"] = all(
            normalize(skill) in verified for skill in skills
        )

        source_bullets = profile.bullet_lookup()
        proposal_ids = [item.source_bullet_id for item in proposal.bullet_proposals]
        statuses["bullet_source_ids"] = (
            set(proposal_ids) == set(source_bullets)
            and len(proposal_ids) == len(set(proposal_ids))
        )

        confirmed_numbers = {
            token
            for evidence in profile.supplemental_evidence
            for token in numeric_tokens(evidence.statement)
        }
        statuses["bullet_numbers"] = all(
            item.source_bullet_id not in source_bullets
            or not (
                numeric_tokens(item.proposed_text)
                - numeric_tokens(source_bullets[item.source_bullet_id])
                - confirmed_numbers
            )
            for item in proposal.bullet_proposals
        )

        counts = _included_bullet_counts(profile, proposal)
        statuses["experience_counts"] = bool(counts) and all(
            minimum <= count <= maximum
            for _experience_id, _employer, count, minimum, maximum in counts
        )
        proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
        confirmations = [
            evidence
            for evidence in profile.supplemental_evidence
            if evidence.source == "candidate_confirmation" and evidence.source_bullet_id
        ]
        statuses["confirmed_experience_visible"] = all(
            (
                evidence.source_bullet_id in proposal_lookup
                and proposal_lookup[evidence.source_bullet_id].include
            )
            or any(
                item.include
                and item.source_bullet_id != evidence.source_bullet_id
                and (
                    evidence.id.casefold() in item.evidence_note.casefold()
                    or evidence.source_bullet_id.casefold() in item.evidence_note.casefold()
                )
                for item in proposal.bullet_proposals
            )
            for evidence in confirmations
        )

    if analysis is not None:
        requirement_ids = {requirement.id for requirement in analysis.requirements}
        statuses["bullet_requirement_ids"] = all(
            set(item.matched_requirement_ids) <= requirement_ids
            for item in proposal.bullet_proposals
        )
        evidence_ids = [match.requirement_id for match in proposal.evidence_matches]
        statuses["evidence_matrix_ids"] = (
            set(evidence_ids) == requirement_ids
            and len(evidence_ids) == len(set(evidence_ids))
        )

    return statuses


def deterministic_audit_facts(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> dict[str, object]:
    """Expose the exact current counts supplied to the independent AI audit."""

    skills = proposed_skills(proposal.skills)
    verified = {normalize(skill) for skill in profile.all_verified_skills()}
    normalized_skills = [normalize(skill) for skill in skills]
    role_counts = _included_bullet_counts(profile, proposal)
    requirement_ids = {requirement.id for requirement in analysis.requirements}
    evidence_ids = [match.requirement_id for match in proposal.evidence_matches]
    return {
        "professional_summary_words": word_count(proposal.professional_summary),
        "professional_summary_sentences": sentence_count(proposal.professional_summary),
        "skills_total": len(skills),
        "skills_duplicates": sorted(
            item for item, count in Counter(normalized_skills).items() if item and count > 1
        ),
        "skills_not_verified": [skill for skill in skills if normalize(skill) not in verified],
        "included_bullets_by_role": [
            {
                "experience_id": experience_id,
                "employer": employer,
                "count": count,
                "required_minimum": minimum,
                "required_maximum": maximum,
            }
            for experience_id, employer, count, minimum, maximum in role_counts
        ],
        "proposal_bullet_records": len(proposal.bullet_proposals),
        "source_bullet_records": len(profile.bullet_lookup()),
        "evidence_decisions": len(evidence_ids),
        "job_requirements": len(requirement_ids),
    }


def candidate_claim_grounding_issues(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> list[AuditIssue]:
    """Return only blocking findings about generated candidate claims.

    This narrow view is used by export and downstream-output gates so a cached
    document can never bypass the same evidence checks applied during review.
    """
    return [
        issue
        for issue in validate_proposal(profile, analysis, proposal)
        if issue.severity == "blocking"
        and issue.issue.startswith("Generated candidate claim")
    ]


def reconcile_audit_with_deterministic_rules(
    audit: ProposalAudit,
    proposal: TailoringProposal,
    profile: CandidateProfile | None = None,
    analysis: JobAnalysis | None = None,
) -> ProposalAudit:
    """Remove or replace AI findings about rules that local code can decide exactly.

    The independent model remains responsible for semantic evidence quality and
    wording. Counts, limits, exact IDs, duplicates, selected-skill membership,
    and numeric-token checks are owned by deterministic validation. An AI claim
    about one of those rules is removed when local code proves it false. If the
    rule is genuinely failing, the AI wording is replaced by the exact local
    validation issue so a partly-correct mixed finding cannot misstate the facts.
    """

    statuses = _objective_rule_statuses(proposal, profile, analysis)
    deterministic_issues = (
        validate_proposal(profile, analysis, proposal)
        if profile is not None and analysis is not None
        else []
    )
    deterministic_by_category: dict[str, list[AuditIssue]] = {
        category: [] for category in _DETERMINISTIC_CATEGORIES
    }
    for issue in deterministic_issues:
        for category in _deterministic_audit_categories(issue):
            deterministic_by_category[category].append(issue)

    retained: list[AuditIssue] = []
    retained_keys: set[tuple[str, str, str, str]] = set()
    removed_categories: set[str] = set()

    def add_retained(issue: AuditIssue) -> None:
        key = (issue.severity, issue.section, issue.source_id, issue.issue)
        if key not in retained_keys:
            retained.append(issue)
            retained_keys.add(key)

    for issue in audit.issues:
        if _malformed_semantic_audit_finding(issue, proposal, analysis):
            continue

        categories = _deterministic_audit_categories(issue)
        if not categories:
            add_retained(issue)
            continue

        invalid_categories = {
            category for category in categories if statuses.get(category) is False
        }
        unknown_categories = {
            category for category in categories if statuses.get(category) is None
        }
        if not invalid_categories and not unknown_categories:
            removed_categories.update(categories)
            continue

        exact_replacements: list[AuditIssue] = []
        for category in invalid_categories:
            exact_replacements.extend(deterministic_by_category.get(category, []))
        if exact_replacements:
            for replacement in exact_replacements:
                add_retained(replacement)
            removed_categories.update(categories - invalid_categories)
            continue

        # Without enough local inputs to decide the rule, preserve the finding.
        add_retained(issue)

    strengths = list(audit.verified_strengths)
    summary_categories = {"summary_length", "summary_sentences"}
    if removed_categories & summary_categories:
        verified_message = (
            "Professional summary structure is within the required limits: "
            f"{word_count(proposal.professional_summary)} words and "
            f"{sentence_count(proposal.professional_summary)} sentences."
        )
        if verified_message not in strengths:
            strengths.append(verified_message)

    skill_categories = {"skills_count", "skills_duplicates", "skills_verified"}
    if removed_categories & skill_categories:
        skills = proposed_skills(proposal.skills)
        verified_message = (
            "Skills were verified deterministically: "
            f"{len(skills)} selected (maximum {SKILL_TOTAL_MAXIMUM}), all selected skills are present "
            "in the verified candidate profile, and no duplicates are present."
        )
        if verified_message not in strengths:
            strengths.append(verified_message)

    if "experience_counts" in removed_categories and profile is not None:
        role_labels = ("Most recent role", "Second role")
        verified_parts = []
        for index, (_experience_id, employer, count, minimum, maximum) in enumerate(
            _included_bullet_counts(profile, proposal)[:2]
        ):
            label = role_labels[index] if index < len(role_labels) else f"Role {index + 1}"
            verified_parts.append(
                f"{label} ({employer}): {count} included bullets, required {minimum}-{maximum}"
            )
        verified_message = (
            "Professional experience bullet counts are within the required ranges: "
            + "; ".join(verified_parts)
            + "."
        )
        if verified_message not in strengths:
            strengths.append(verified_message)

    other_removed = removed_categories - summary_categories - skill_categories - {"experience_counts"}
    if other_removed:
        verified_message = (
            "Objective resume constraints were rechecked against the current Final draft "
            "and any contradicted AI findings were removed."
        )
        if verified_message not in strengths:
            strengths.append(verified_message)

    return audit.model_copy(
        update={
            "issues": retained,
            "verified_strengths": strengths,
        }
    )

def build_approved_resume(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> ApprovedResume:
    proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
    bullets_by_experience: dict[str, list[str]] = {}
    for experience in profile.experiences:
        selected: list[str] = []
        for source_bullet in experience.bullets:
            item = proposal_lookup.get(source_bullet.id)
            if item and item.include:
                selected.append(item.proposed_text.strip())
        bullets_by_experience[experience.id] = selected

    return ApprovedResume(
        target_title=analysis.target_title,
        professional_summary=proposal.professional_summary.strip(),
        skills=proposal.skills,
        bullets_by_experience=bullets_by_experience,
    )
