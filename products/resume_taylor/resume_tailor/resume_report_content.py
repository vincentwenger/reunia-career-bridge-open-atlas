from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module, module_exports

"""Resume content, language, readability, and semantic analysis."""

def _education_match(profile: CandidateProfile, analysis: JobAnalysis) -> ReportCheck:
    education_requirements = [
        requirement
        for requirement in analysis.requirements
        if requirement.category == "qualification"
        and re.search(
            r"\b(?:degree|bachelor|bachelor's|master|master's|phd|doctorate|education|college|university)\b",
            requirement.requirement,
            re.IGNORECASE,
        )
    ]
    if not education_requirements:
        return ReportCheck(
            "Education matches the job description",
            "pass",
            "No explicit required or preferred degree was identified in the job description, so no education mismatch was found.",
        )

    education_text = _normalize(
        " ".join(
            f"{item.credential} {item.institution} {item.detail}"
            for item in profile.education
        )
    )
    failures: list[str] = []
    for requirement in education_requirements:
        normalized_requirement = _normalize(requirement.requirement)
        requires_master = bool(re.search(r"\bmaster", normalized_requirement))
        requires_bachelor = bool(re.search(r"\bbachelor|\bdegree|\bcollege|\buniversity", normalized_requirement))
        has_master = "master" in education_text or "m s" in education_text
        has_bachelor = "bachelor" in education_text or "b s" in education_text or has_master
        if requires_master and not has_master:
            failures.append(requirement.requirement)
        elif requires_bachelor and not has_bachelor:
            failures.append(requirement.requirement)

    if failures:
        return ReportCheck(
            "Education matches the job description",
            "fail",
            "The profile does not clearly satisfy: " + "; ".join(failures),
        )
    return ReportCheck(
        "Education matches the job description",
        "pass",
        "The candidate's verified education satisfies the degree requirements identified in the job description.",
    )


def _status_for_score(score: float, *, pass_at: float = 80.0, warning_at: float = 50.0) -> ReportStatus:
    if score >= pass_at:
        return "pass"
    if score >= warning_at:
        return "warning"
    return "fail"


def _semantic_match_subsection(
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> ReportSubsection:
    evidence_by_requirement = {
        item.requirement_id: item for item in proposal.evidence_matches
    }
    priority_weights = {"critical": 3.0, "important": 2.0, "secondary": 1.0}
    checks: list[ReportCheck] = []

    for requirement in analysis.requirements:
        evidence = evidence_by_requirement.get(requirement.id)
        evidence_status = evidence.status if evidence else "unsupported"
        represented = _requirement_is_represented(requirement, proposal)
        evidence_score = {"supported": 70.0, "partial": 45.0, "unsupported": 0.0}[evidence_status]
        representation_score = 30.0 if represented else 0.0
        score = min(100.0, evidence_score + representation_score)
        status = _status_for_score(score)
        if evidence_status == "supported" and represented:
            interpretation = "Verified experience supports the meaning of this requirement and the current resume represents it."
        elif evidence_status == "supported":
            interpretation = "Verified experience supports this requirement, but the meaning is not clearly represented in the current resume."
        elif evidence_status == "partial" and represented:
            interpretation = "The resume addresses this requirement, but the verified evidence supports only part of it."
        elif evidence_status == "partial":
            interpretation = "Only partial verified evidence exists, and the current resume does not clearly represent it."
        else:
            interpretation = "No verified experience currently supports this requirement, so it should remain a disclosed gap."

        checks.append(
            ReportCheck(
                f'{requirement.id} semantic match · {requirement.requirement}',
                status,
                f"Meaning-based score: {score:.0f}%. Evidence: {evidence_status}. Represented in resume: {'yes' if represented else 'no'}. {interpretation}",
                weight=priority_weights.get(requirement.priority, 1.0),
                score_value=score,
            )
        )

    if not checks:
        checks.append(
            ReportCheck(
                "Meaning-based requirement coverage can be calculated",
                "warning",
                "No job requirements were available for semantic comparison.",
            )
        )
    return ReportSubsection("Semantic Match", checks)


def _document_section_positions(document: Document | None) -> dict[str, int]:
    if document is None:
        return {}
    paragraphs = [paragraph.text.strip().casefold() for paragraph in _body_paragraphs(document)]
    positions: dict[str, int] = {}
    for key, aliases in _SECTION_HEADING_ALIASES:
        for index, text in enumerate(paragraphs):
            if any(text == alias or text.startswith(alias + " ") for alias in aliases):
                positions[key] = index
                break
    return positions


def _data_structure_subsection(
    profile: CandidateProfile,
    proposal: TailoringProposal,
    document: Document | None,
    inspection_note: str | None,
    *,
    career_stage: str | None = None,
    resume_format: str | None = None,
) -> ReportSubsection:
    missing_entities: list[str] = []
    if not profile.name.strip():
        missing_entities.append("candidate name")
    if not profile.contact.email.strip():
        missing_entities.append("email")
    if not profile.contact.phone.strip():
        missing_entities.append("phone")
    if not profile.contact.location.strip():
        missing_entities.append("location")
    for index, experience in enumerate(profile.experiences, start=1):
        if not experience.employer.strip():
            missing_entities.append(f"experience {index} employer")
        if not experience.title.strip():
            missing_entities.append(f"experience {index} title")
        if not experience.dates.strip():
            missing_entities.append(f"experience {index} dates")
        if not experience.bullets:
            missing_entities.append(f"experience {index} accomplishments")
    for index, education in enumerate(profile.education, start=1):
        if not education.credential.strip():
            missing_entities.append(f"education {index} credential")
        if not education.institution.strip():
            missing_entities.append(f"education {index} institution")
        if not education.date.strip():
            missing_entities.append(f"education {index} date")

    source_ids = set(profile.bullet_lookup())
    proposal_ids = {item.source_bullet_id for item in proposal.bullet_proposals}
    unmapped_source_ids = sorted(source_ids - proposal_ids)
    unknown_proposal_ids = sorted(proposal_ids - source_ids)

    positions = _document_section_positions(document)
    stage = normalize_career_stage(career_stage)
    format_key = normalize_resume_format(resume_format)
    if format_key == "technical":
        expected_order = ["skills", "summary", "experience", "education"]
    elif format_key == "career_changer":
        expected_order = ["summary", "skills", "education", "experience"]
    elif format_key == "freelance":
        expected_order = ["summary", "skills", "experience", "education"]
    elif stage == "early_career":
        expected_order = ["summary", "skills", "education", "experience"]
    else:
        expected_order = ["summary", "skills", "experience", "education"]
    missing_sections = [name for name in expected_order if name not in positions]
    order_is_valid = not missing_sections and [positions[name] for name in expected_order] == sorted(
        positions[name] for name in expected_order
    )

    document_text = _document_text(document).casefold() if document is not None else ""
    key_entities = [profile.name, profile.contact.email, profile.contact.phone]
    missing_from_document = [
        value for value in key_entities if value.strip() and value.strip().casefold() not in document_text
    ]

    return ReportSubsection(
        "Data & Structure",
        [
            ReportCheck(
                "Core resume entities are complete",
                "pass" if not missing_entities else "fail",
                "The structured profile includes the candidate name, contact details, complete work-history entities, and complete education entities."
                if not missing_entities
                else "Missing or incomplete entities: " + "; ".join(missing_entities[:12]) + ".",
            ),
            ReportCheck(
                "Every source accomplishment maps to the resume workflow",
                "pass" if not unmapped_source_ids and not unknown_proposal_ids else "fail",
                "Every source bullet has a corresponding proposal record and no unknown source IDs were introduced."
                if not unmapped_source_ids and not unknown_proposal_ids
                else "Mapping problems — missing proposal IDs: "
                + (", ".join(unmapped_source_ids) or "none")
                + "; unknown proposal IDs: "
                + (", ".join(unknown_proposal_ids) or "none")
                + ".",
            ),
            ReportCheck(
                "The generated resume preserves the expected section hierarchy",
                "pass" if order_is_valid else "warning" if document is None else "fail",
                "The generated resume sections match the selected career stage and resume format."
                if order_is_valid
                else (inspection_note or "The generated document was unavailable for structural inspection.")
                if document is None
                else "Missing or out-of-order sections: " + ", ".join(missing_sections or expected_order) + ".",
            ),
            ReportCheck(
                "Key extracted entities appear in the generated document",
                "pass" if document is not None and not missing_from_document else "warning" if document is None else "fail",
                "The candidate name, email, and phone number were found in the generated document."
                if document is not None and not missing_from_document
                else (inspection_note or "The generated document was unavailable for entity verification.")
                if document is None
                else "These profile entities were not found in the generated document: " + ", ".join(missing_from_document) + ".",
            ),
        ],
    )


def _language_quality_subsection(summary: str, bullets: list[str]) -> ReportSubsection:
    text_blocks = [summary.strip(), *[bullet.strip() for bullet in bullets if bullet.strip()]]
    combined = "\n".join(block for block in text_blocks if block)
    normalized_words = [word.casefold() for word in _words(combined)]
    misspellings = sorted(
        {word for word in normalized_words if word in _COMMON_MISSPELLINGS}
    )
    repeated_words = sorted(
        {word.casefold() for word in adjacent_repeated_words(combined)}
    )
    malformed_punctuation: list[str] = []
    if re.search(r"\s+[,.!?;:]", combined):
        malformed_punctuation.append("spaces before punctuation")
    if re.search(r"[!?.,;:]{2,}", combined):
        malformed_punctuation.append("repeated punctuation")
    if re.search(r"\s{2,}", combined):
        malformed_punctuation.append("repeated spaces")
    bracket_pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    unbalanced = [f"{left}{right}" for left, right in bracket_pairs if combined.count(left) != combined.count(right)]

    lowercase_starts = [
        block[:45]
        for block in text_blocks
        if block and block[0].isalpha() and block[0].islower()
    ]
    language_issues = len(misspellings) + len(repeated_words) + len(malformed_punctuation) + len(unbalanced)
    grammar_status: ReportStatus = "pass" if language_issues == 0 else "warning" if language_issues <= 2 else "fail"
    grammar_details: list[str] = []
    if misspellings:
        grammar_details.append(
            "possible misspellings: "
            + ", ".join(f"{word} → {_COMMON_MISSPELLINGS[word]}" for word in misspellings)
        )
    if repeated_words:
        grammar_details.append("repeated words: " + ", ".join(repeated_words))
    if malformed_punctuation:
        grammar_details.append("formatting issues: " + ", ".join(malformed_punctuation))
    if unbalanced:
        grammar_details.append("unbalanced brackets: " + ", ".join(unbalanced))

    return ReportSubsection(
        "Grammar & Spelling",
        [
            ReportCheck(
                "No common spelling, repeated-word, or punctuation errors were detected",
                grammar_status,
                "The deterministic language scan found no common spelling, repeated-word, spacing, punctuation, or bracket issues."
                if not grammar_details
                else "The language scan found " + "; ".join(grammar_details) + ". Review context before accepting a correction.",
            ),
            ReportCheck(
                "Summary and bullets begin with consistent capitalization",
                "pass" if not lowercase_starts else "warning",
                "The summary and selected bullets begin with consistent capitalization."
                if not lowercase_starts
                else "These entries begin with lowercase text: " + "; ".join(lowercase_starts[:6]) + ".",
            ),
        ],
    )


def _normalize_number_token(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold().replace(",", ""))


def _metric_quality_subsection(
    profile: CandidateProfile,
    proposal: TailoringProposal,
    candidate_answers: list[CandidateAnswer] | None,
) -> ReportSubsection:
    source_lookup = profile.bullet_lookup()
    supplemental_text = " ".join(item.statement for item in profile.supplemental_evidence)
    answer_text = " ".join(answer.text for answer in (candidate_answers or []) if answer.text.strip())
    unsupported_metrics: list[str] = []
    suspicious_metrics: list[str] = []
    formatting_issues: list[str] = []

    metric_items = [
        (
            "professional summary",
            proposal.professional_summary,
            profile.current_summary + " " + supplemental_text + " " + answer_text,
        )
    ]
    metric_items.extend(
        (
            bullet.source_bullet_id,
            bullet.proposed_text,
            source_lookup.get(bullet.source_bullet_id, "") + " " + supplemental_text + " " + answer_text,
        )
        for bullet in proposal.bullet_proposals
        if bullet.include
    )

    for source_id, proposed_text, verified_text in metric_items:
        verified_tokens = {
            _normalize_number_token(match.group(0))
            for match in _NUMBER_TOKEN_PATTERN.finditer(verified_text)
        }
        for match in _NUMBER_TOKEN_PATTERN.finditer(proposed_text):
            raw = match.group(0)
            normalized = _normalize_number_token(raw)
            if normalized not in verified_tokens:
                unsupported_metrics.append(f"{source_id}: {raw}")
            numeric_match = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
            if numeric_match:
                value = float(numeric_match.group(0).replace(",", ""))
                if "%" in raw and value > 1000:
                    suspicious_metrics.append(f"{source_id}: {raw}")
            if re.search(r"[$€£]\s+\d", raw):
                formatting_issues.append(f"space after currency symbol in {raw}")
            if re.search(r"\d\s+%", raw):
                formatting_issues.append(f"space before percent sign in {raw}")

        for range_match in re.finditer(r"\b(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)\b", proposed_text):
            if float(range_match.group(1)) > float(range_match.group(2)):
                suspicious_metrics.append(
                    f"{source_id}: descending range {range_match.group(0)}"
                )

    unsupported_metrics = sorted(set(unsupported_metrics))
    suspicious_metrics = sorted(set(suspicious_metrics))
    formatting_issues = sorted(set(formatting_issues))
    return ReportSubsection(
        "Metric Integrity",
        [
            ReportCheck(
                "Every numeric claim is traceable to verified source evidence",
                "pass" if not unsupported_metrics else "fail",
                "All numbers, percentages, and monetary values in selected bullets also appear in the verified profile or candidate confirmations."
                if not unsupported_metrics
                else "Potentially unsupported metrics: " + "; ".join(unsupported_metrics[:12]) + ". Remove them or confirm the evidence.",
            ),
            ReportCheck(
                "Metrics are logically plausible",
                "pass" if not suspicious_metrics else "warning",
                "No obviously implausible percentages or descending numerical ranges were detected."
                if not suspicious_metrics
                else "Review these potentially implausible metrics: " + "; ".join(suspicious_metrics[:10]) + ".",
            ),
            ReportCheck(
                "Metric formatting is consistent",
                "pass" if not formatting_issues else "warning",
                "Currency and percentage symbols use consistent compact formatting."
                if not formatting_issues
                else "Formatting inconsistencies: " + "; ".join(formatting_issues[:10]) + ".",
            ),
        ],
    )


def _syllable_count(word: str) -> int:
    cleaned = re.sub(r"[^a-z]", "", word.casefold())
    if not cleaned:
        return 0
    if len(cleaned) <= 3:
        return 1
    groups = re.findall(r"[aeiouy]+", cleaned)
    count = len(groups)
    if cleaned.endswith("e") and not cleaned.endswith(("le", "ye")) and count > 1:
        count -= 1
    return max(1, count)


def _readability_subsection(summary: str, bullets: list[str]) -> ReportSubsection:
    blocks = [summary.strip(), *[bullet.strip() for bullet in bullets if bullet.strip()]]
    words = [word for block in blocks for word in _words(block)]
    sentence_count = max(1, sum(max(1, len(re.findall(r"[.!?]+", block))) for block in blocks if block))
    syllables = sum(_syllable_count(word) for word in words)
    word_count = max(1, len(words))
    reading_ease = 206.835 - 1.015 * (word_count / sentence_count) - 84.6 * (syllables / word_count)
    grade_level = 0.39 * (word_count / sentence_count) + 11.8 * (syllables / word_count) - 15.59
    reading_ease = max(0.0, min(100.0, reading_ease))
    grade_level = max(0.0, grade_level)
    readability_score = max(0.0, min(100.0, 100.0 - max(0.0, grade_level - 10.0) * 8.0))
    jargon_words = sorted(
        {
            word
            for word in words
            if len(word) >= 13 and _syllable_count(word) >= 4
        },
        key=lambda value: (-len(value), value.casefold()),
    )
    jargon_ratio = len([word for word in words if len(word) >= 13 and _syllable_count(word) >= 4]) / word_count

    return ReportSubsection(
        "Readability",
        [
            ReportCheck(
                "The resume has a recruiter-friendly readability level",
                _status_for_score(readability_score, pass_at=70.0, warning_at=45.0),
                f"Estimated Flesch reading ease: {reading_ease:.1f}; estimated grade level: {grade_level:.1f}. Technical resumes can be specialized, but sentences should remain direct and scannable.",
                score_value=readability_score,
            ),
            ReportCheck(
                "Jargon density is controlled",
                "pass" if jargon_ratio <= 0.08 else "warning" if jargon_ratio <= 0.14 else "fail",
                f"Approximately {jargon_ratio:.0%} of words are long, complex terms."
                + (" The density is reasonable for a technical resume." if jargon_ratio <= 0.08 else " Consider simplifying or defining terms such as " + ", ".join(jargon_words[:8]) + "."),
            ),
        ],
    )


def _writing_style_subsection(
    profile: CandidateProfile,
    proposal: TailoringProposal,
) -> ReportSubsection:
    selected = [item for item in proposal.bullet_proposals if item.include and item.proposed_text.strip()]
    personal_pronouns = sorted(
        {match.group(0).casefold() for item in selected for match in _PERSONAL_PRONOUN_PATTERN.finditer(item.proposed_text)}
        | {match.group(0).casefold() for match in _PERSONAL_PRONOUN_PATTERN.finditer(proposal.professional_summary)}
    )
    terminal_period_violations = [
        item.source_bullet_id
        for item in selected
        if item.proposed_text.strip().endswith(".")
        and normalize_resume_bullet_terminal_punctuation(item.proposed_text)
        != item.proposed_text.strip()
        and not bullet_has_multiple_complete_sentences(item.proposed_text)
    ]
    non_action_openers: list[str] = []
    tense_issues: list[str] = []
    experience_by_bullet = {
        bullet.id: experience
        for experience in profile.experiences
        for bullet in experience.bullets
    }
    for item in selected:
        words = _words(item.proposed_text)
        if not words:
            continue
        opener = words[0].casefold()
        if opener not in _ACTION_VERBS and not opener.endswith("ed") and opener not in _IRREGULAR_PAST_OPENERS:
            non_action_openers.append(f"{item.source_bullet_id}: {words[0]}")
        experience = experience_by_bullet.get(item.source_bullet_id)
        if experience and not re.search(r"\b(?:present|current)\b", experience.dates, re.IGNORECASE):
            if opener in _ACTION_VERBS and not opener.endswith("ed") and opener not in _IRREGULAR_PAST_OPENERS:
                tense_issues.append(f"{item.source_bullet_id}: {words[0]}")

    action_ratio = 1.0 - (len(non_action_openers) / max(1, len(selected)))
    return ReportSubsection(
        "Writing Style",
        [
            ReportCheck(
                "The resume avoids personal pronouns",
                "pass" if not personal_pronouns else "warning",
                "No first-person personal pronouns were detected."
                if not personal_pronouns
                else "Remove personal pronouns such as: " + ", ".join(personal_pronouns) + ".",
            ),
            ReportCheck(
                "Single-sentence bullets omit terminal periods",
                "pass" if not terminal_period_violations else "warning",
                "All single-sentence bullets use the clean no-period resume style."
                if not terminal_period_violations
                else "Remove optional terminal periods from: "
                + ", ".join(terminal_period_violations[:8])
                + ". Multi-sentence bullets and intrinsic abbreviation periods may retain them.",
            ),
            ReportCheck(
                "Bullets use a parallel action-led structure",
                "pass" if action_ratio >= 0.8 else "warning" if action_ratio >= 0.6 else "fail",
                f"{len(selected) - len(non_action_openers)} of {len(selected)} selected bullets begin with a recognized action-oriented verb."
                + ("" if not non_action_openers else " Review: " + "; ".join(non_action_openers[:8]) + "."),
                score_value=action_ratio * 100.0,
            ),
            ReportCheck(
                "Past roles use consistent past-tense openings",
                "pass" if not tense_issues else "warning",
                "No clear tense inconsistencies were detected in completed roles."
                if not tense_issues
                else "Review possible present-tense openings in completed roles: " + "; ".join(tense_issues[:8]) + ".",
            ),
        ],
    )


def _content_focus_subsection(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> ReportSubsection:
    selected = [item for item in proposal.bullet_proposals if item.include and item.proposed_text.strip()]
    long_bullets = [
        f"{item.source_bullet_id} ({len(_words(item.proposed_text))} words)"
        for item in selected
        if len(_words(item.proposed_text)) > 35
    ]
    normalized_counts = Counter(_normalize(item.proposed_text) for item in selected)
    duplicates = [text for text, count in normalized_counts.items() if text and count > 1]
    evidence_by_id = {item.requirement_id: item for item in proposal.evidence_matches}
    supported_not_represented = [
        requirement.requirement
        for requirement in analysis.requirements
        if requirement.priority in {"critical", "important"}
        and evidence_by_id.get(requirement.id)
        and evidence_by_id[requirement.id].status in {"supported", "partial"}
        and not _requirement_is_represented(requirement, proposal)
    ]
    selected_ids = {item.source_bullet_id for item in selected}
    older_role_warnings: list[str] = []
    current_month = date.today().year * 12 + date.today().month - 1
    for experience in profile.experiences:
        parts = re.split(r"\s*[-–—]\s*", experience.dates.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        end = _month_index(parts[1])
        selected_count = sum(bullet.id in selected_ids for bullet in experience.bullets)
        if end is not None and current_month - end >= 120 and selected_count > 3:
            older_role_warnings.append(f"{experience.employer}: {selected_count} bullets")

    return ReportSubsection(
        "Content Focus",
        [
            ReportCheck(
                "Supported priority requirements are represented without inventing experience",
                "pass" if not supported_not_represented else "warning",
                "All supported critical and important requirements are represented in the resume."
                if not supported_not_represented
                else "Consider safely augmenting existing evidence for: " + "; ".join(supported_not_represented[:10]) + ".",
            ),
            ReportCheck(
                "Bullets are concise enough to scan quickly",
                "pass" if not long_bullets else "warning",
                "Every selected bullet contains 35 words or fewer."
                if not long_bullets
                else "Shorten these bullets: " + "; ".join(long_bullets[:10]) + ".",
            ),
            ReportCheck(
                "The resume does not repeat identical accomplishment bullets",
                "pass" if not duplicates else "fail",
                "No duplicate selected bullets were detected."
                if not duplicates
                else f"{len(duplicates)} duplicated bullet text pattern(s) were detected and should be pruned.",
            ),
            ReportCheck(
                "Older roles are proportionately concise",
                "pass" if not older_role_warnings else "warning",
                "Roles that ended at least 10 years ago use no more than three selected bullets."
                if not older_role_warnings
                else "Consider pruning older roles: " + "; ".join(older_role_warnings) + ".",
            ),
        ],
    )


def _content_quality_section(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    selected_bullets: list[str],
    document: Document | None,
    inspection_note: str | None,
    candidate_answers: list[CandidateAnswer] | None,
    *,
    career_stage: str | None = None,
    resume_format: str | None = None,
) -> ReportSection:
    return ReportSection(
        "Content Quality",
        "These checks validate the transformed resume beyond exact keywords: structured data integrity, meaning-based job alignment, grammar and spelling, metric credibility, readability, consistent writing style, and focused content.",
        [
            _data_structure_subsection(
                profile,
                proposal,
                document,
                inspection_note,
                career_stage=career_stage,
                resume_format=resume_format,
            ),
            _semantic_match_subsection(analysis, proposal),
            _language_quality_subsection(proposal.professional_summary, selected_bullets),
            _metric_quality_subsection(profile, proposal, candidate_answers),
            _readability_subsection(proposal.professional_summary, selected_bullets),
            _writing_style_subsection(profile, proposal),
            _content_focus_subsection(profile, analysis, proposal),
        ],
    )

_EXPORT_NAMES = (
    '_education_match',
    '_status_for_score',
    '_semantic_match_subsection',
    '_document_section_positions',
    '_data_structure_subsection',
    '_language_quality_subsection',
    '_normalize_number_token',
    '_metric_quality_subsection',
    '_syllable_count',
    '_readability_subsection',
    '_writing_style_subsection',
    '_content_focus_subsection',
    '_content_quality_section',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
