from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module, module_exports

"""Job matching, skill comparison, and evidence lookup helpers."""

def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _words(value: str) -> list[str]:
    return _WORD_PATTERN.findall(value)


def _month_index(value: str) -> int | None:
    match = re.fullmatch(r"(0?[1-9]|1[0-2])/(19|20)\d{2}", value.strip())
    if not match:
        return None
    month_text, year_text = value.strip().split("/")
    return int(year_text) * 12 + int(month_text) - 1


def _documented_experience_years(profile: CandidateProfile) -> float:
    intervals: list[tuple[int, int]] = []
    current_month = date.today().year * 12 + date.today().month - 1
    for experience in profile.experiences:
        parts = re.split(r"\s*[-–—]\s*", experience.dates.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        start = _month_index(parts[0])
        end = current_month if parts[1].casefold() in {"present", "current"} else _month_index(parts[1])
        if start is None or end is None or end < start:
            continue
        intervals.append((start, end))

    if not intervals:
        return 0.0

    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    months = sum(end - start + 1 for start, end in merged)
    return months / 12.0


def _replace_number_words(value: str) -> str:
    result = value.casefold()
    for word, number in _NUMBER_WORDS.items():
        result = re.sub(rf"\b{word}\b", number, result)
    return result


def _required_experience_years(analysis: JobAnalysis) -> int | None:
    minimums: list[int] = []
    for requirement in analysis.requirements:
        text = _replace_number_words(requirement.requirement)
        range_spans: list[tuple[int, int]] = []
        for match in re.finditer(r"\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:years?|yrs?)\b", text):
            minimums.append(int(match.group(1)))
            range_spans.append(match.span())
        for match in re.finditer(r"\b(\d{1,2})\s*(?:\+|plus|or more)?\s*(?:years?|yrs?)\b", text):
            if any(start <= match.start() < end for start, end in range_spans):
                continue
            minimums.append(int(match.group(1)))
    return max(minimums) if minimums else None


def _job_level_match(profile: CandidateProfile, analysis: JobAnalysis) -> ReportCheck:
    years = _documented_experience_years(profile)
    required_years = _required_experience_years(analysis)
    years_display = f"{years:.1f}".rstrip("0").rstrip(".")

    if required_years is not None:
        status: ReportStatus = "pass" if years >= required_years else "fail"
        detail = (
            f"The resume documents approximately {years_display} years of work experience, "
            f"compared with a detected minimum requirement of {required_years} years. "
            "Carefully review all other job criteria to confirm a strong overall match before applying."
        )
        return ReportCheck("Your years of experience align with the role's requirements", status, detail)

    normalized_title = _normalize(analysis.target_title)
    for level, minimum in _TITLE_LEVEL_MINIMUM_YEARS.items():
        if re.search(rf"\b{re.escape(level)}\b", normalized_title):
            status = "pass" if years >= minimum else "fail"
            return ReportCheck(
                "Your years of experience align with the role's requirements",
                status,
                f'The title "{analysis.target_title}" suggests a {minimum}+ year experience level, and the resume documents approximately {years_display} years. Carefully review all other job criteria before applying.',
            )

    if any(term in analysis.target_title.casefold() for term in _ENTRY_LEVEL_TITLE_TERMS) and years > 5:
        return ReportCheck(
            "Your years of experience align with the role's requirements",
            "warning",
            f'The resume documents approximately {years_display} years of experience, while "{analysis.target_title}" appears entry-level. Consider whether the role, compensation, and growth path fit your experience.',
        )

    return ReportCheck(
        "Your years of experience align with the role's requirements",
        "pass",
        f"The resume documents approximately {years_display} years of work experience, and no explicit minimum-years mismatch was detected. Carefully review all other job criteria to confirm a strong overall match before applying.",
    )


def _professional_web_links(
    template_path: str | Path | None,
    profile: CandidateProfile | None = None,
) -> tuple[list[str], str | None]:
    targets: list[str] = []
    if profile is not None:
        targets.extend(
            url.strip()
            for url in (profile.contact.linkedin_url, profile.contact.github_url)
            if url.strip().casefold().startswith(("http://", "https://"))
        )

    if template_path:
        try:
            with ZipFile(str(template_path)) as archive:
                for name in archive.namelist():
                    if not name.startswith("word/") or not name.endswith(".rels"):
                        continue
                    root = ElementTree.fromstring(archive.read(name))
                    for relationship in root:
                        target = relationship.attrib.get("Target", "").strip()
                        relation_type = relationship.attrib.get("Type", "")
                        if relation_type.endswith("/hyperlink") and target.casefold().startswith(("http://", "https://")):
                            targets.append(target)
        except Exception as exc:  # pragma: no cover - defensive UI fallback
            if not targets:
                return [], f"The resume hyperlinks could not be inspected: {exc}"
    elif not targets:
        return [], "No resume template or candidate web links were supplied for hyperlink inspection."

    excluded_social = {"facebook.com", "instagram.com", "tiktok.com", "twitter.com", "x.com"}
    professional: list[str] = []
    for target in targets:
        hostname = (urlparse(target).hostname or "").casefold().removeprefix("www.")
        if not hostname or any(hostname == domain or hostname.endswith("." + domain) for domain in excluded_social):
            continue
        professional.append(hostname)
    return sorted(set(professional)), None


def _estimated_resume_word_count(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    resume_title: str,
) -> int:
    selected_lookup = {
        item.source_bullet_id: item.proposed_text.strip()
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    }
    parts = [
        profile.name,
        profile.contact.location,
        profile.contact.phone,
        profile.contact.email,
        profile.contact.linkedin_label,
        profile.contact.github_label,
        resume_title,
        "Professional Summary",
        proposal.professional_summary,
        "Skills",
        *proposal.skills.hard_skills,
        *proposal.skills.soft_skills,
        *proposal.skills.tools_software,
        *proposal.skills.industry_knowledge,
        *profile.skills.languages,
        "Education",
    ]
    for education in profile.education:
        parts.extend(
            [education.credential, education.institution, education.location, education.date, education.detail]
        )
    parts.append("Work Experience")
    for experience in profile.experiences:
        parts.extend([experience.employer, experience.location, experience.dates, experience.title])
        parts.extend(
            selected_lookup[bullet.id]
            for bullet in experience.bullets
            if bullet.id in selected_lookup
        )
    return sum(len(_words(part)) for part in parts if part)


def _selected_bullets(proposal: TailoringProposal) -> list[str]:
    return [
        item.proposed_text.strip()
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    ]


def _selected_bullet_ids_by_experience(
    profile: CandidateProfile,
    proposal: TailoringProposal,
) -> dict[str, list[str]]:
    selected_ids = {
        item.source_bullet_id
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    }
    return {
        experience.id: [bullet.id for bullet in experience.bullets if bullet.id in selected_ids]
        for experience in profile.experiences
    }


def _proposed_resume_text(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    resume_title: str,
) -> str:
    """Return the searchable text of the proposed resume."""
    selected_lookup = {
        item.source_bullet_id: item.proposed_text.strip()
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    }
    parts = [
        profile.name,
        profile.contact.location,
        profile.contact.phone,
        profile.contact.email,
        profile.contact.linkedin_label,
        profile.contact.github_label,
        resume_title,
        "Professional Summary",
        proposal.professional_summary,
        "Skills",
        *proposal.skills.hard_skills,
        *proposal.skills.soft_skills,
        *proposal.skills.tools_software,
        *proposal.skills.industry_knowledge,
        *profile.skills.languages,
        "Education",
    ]
    for education in profile.education:
        parts.extend(
            [education.credential, education.institution, education.location, education.date, education.detail]
        )
    parts.append("Work Experience")
    for experience in profile.experiences:
        parts.extend([experience.employer, experience.location, experience.dates, experience.title])
        parts.extend(
            selected_lookup[bullet.id]
            for bullet in experience.bullets
            if bullet.id in selected_lookup
        )
    return "\n".join(part for part in parts if part)


def _exact_phrase_pattern(phrase: str) -> re.Pattern[str] | None:
    """Build a case-insensitive exact-phrase pattern with flexible whitespace."""
    cleaned = phrase.strip()
    if not cleaned:
        return None
    escaped = re.escape(cleaned).replace(r"\ ", r"\s+")
    prefix = r"(?<![A-Za-z0-9])" if cleaned[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if cleaned[-1].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def _exact_phrase_count(text: str, phrase: str) -> int:
    pattern = _exact_phrase_pattern(phrase)
    return len(pattern.findall(text)) if pattern else 0


def _job_skill_entries(
    analysis: JobAnalysis,
    job_description: str,
) -> list[dict[str, object]]:
    """Collect deduplicated hard-skill keywords and their job-description frequency."""
    hard_requirements = [
        requirement
        for requirement in analysis.requirements
        if requirement.category in {"technical_skill", "domain_knowledge", "methodology"}
    ]
    source_text = job_description.strip() or "\n".join(
        requirement.requirement for requirement in hard_requirements
    )
    entries: dict[str, dict[str, object]] = {}

    for requirement in hard_requirements:
        candidates = requirement.keywords or [requirement.requirement]
        for raw_skill in candidates:
            skill = re.sub(r"\s+", " ", raw_skill).strip(" \t\r\n,;:.")
            if not skill or len(_words(skill)) > 8 or len(skill) > 80:
                continue
            key = skill.casefold()
            pattern = _exact_phrase_pattern(skill)
            match = pattern.search(source_text) if pattern else None
            display = re.sub(r"\s+", " ", match.group(0)).strip() if match else skill
            job_count = _exact_phrase_count(source_text, display)

            entry = entries.setdefault(
                key,
                {
                    "skill": display,
                    "job_count": job_count,
                    "priority": requirement.priority,
                    "requirement_ids": [],
                },
            )
            if job_count > int(entry["job_count"]):
                entry["skill"] = display
                entry["job_count"] = job_count
            if _HARD_SKILL_PRIORITY_ORDER[requirement.priority] < _HARD_SKILL_PRIORITY_ORDER[str(entry["priority"])]:
                entry["priority"] = requirement.priority
            requirement_ids = entry["requirement_ids"]
            if requirement.id not in requirement_ids:
                requirement_ids.append(requirement.id)

    return sorted(
        entries.values(),
        key=lambda item: (
            -int(item["job_count"]),
            _HARD_SKILL_PRIORITY_ORDER[str(item["priority"])],
            str(item["skill"]).casefold(),
        ),
    )


def _skill_check_score(
    *,
    resume_count: int,
    job_count: int,
    all_unsupported: bool,
    any_partial: bool,
) -> tuple[ReportStatus, float, float]:
    coverage = min(resume_count / max(job_count, 1), 1.0)
    if all_unsupported:
        return "fail", 0.0, coverage
    score = coverage * (50.0 if any_partial else 100.0)
    if score >= 99.95:
        status: ReportStatus = "pass"
    elif score <= 0.05:
        status = "fail"
    else:
        status = "warning"
    return status, score, coverage


def _hard_skill_comparison_subsection(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    job_description: str,
    resume_title: str,
) -> ReportSubsection:
    resume_text = _proposed_resume_text(profile, analysis, proposal, resume_title)
    evidence_lookup = {match.requirement_id: match for match in proposal.evidence_matches}
    checks: list[ReportCheck] = []

    for entry in _job_skill_entries(analysis, job_description):
        skill = str(entry["skill"])
        job_count = int(entry["job_count"])
        resume_count = _exact_phrase_count(resume_text, skill)
        priority = str(entry["priority"])
        evidence = [
            evidence_lookup[requirement_id]
            for requirement_id in entry["requirement_ids"]
            if requirement_id in evidence_lookup
        ]
        all_unsupported = bool(evidence) and all(match.status == "unsupported" for match in evidence)
        any_partial = any(match.status == "partial" for match in evidence)
        status, score_value, coverage = _skill_check_score(
            resume_count=resume_count,
            job_count=job_count,
            all_unsupported=all_unsupported,
            any_partial=any_partial,
        )
        weight = max(job_count, 1) * _SKILL_PRIORITY_WEIGHTS.get(priority, 1.0)

        count_text = (
            f"Resume: {resume_count}, Job description: {job_count}. "
            f"Weighted coverage: {coverage * 100:.1f}%."
        )
        if resume_count == 0:
            if all_unsupported:
                detail = (
                    f"{count_text} This {priority} hard skill is not supported by verified evidence. "
                    "Treat it as a gap and do not add it unless the candidate confirms relevant experience."
                )
            else:
                detail = (
                    f"{count_text} The exact job-description spelling is missing from the proposed resume. "
                    "Add or emphasize it only where verified evidence supports the claim."
                )
        elif all_unsupported:
            detail = (
                f"{count_text} The term appears in the resume even though the mapped requirement lacks verified evidence. "
                "Remove it or confirm supporting experience before export."
            )
        elif any_partial:
            detail = (
                f"{count_text} The exact spelling is present, but the supporting evidence is partial. "
                "Its score is reduced until the candidate verifies the claim."
            )
        elif coverage < 1.0:
            detail = (
                f"{count_text} The skill is present, but it appears less often than in the job description. "
                "Strengthen it naturally in evidence-based content when the additional emphasis is accurate."
            )
        else:
            frequency_note = (
                " It is one of the most repeated hard skills in the job description, and the proposed resume provides full frequency coverage."
                if job_count >= 2
                else " The exact spelling is represented with full frequency coverage."
            )
            detail = count_text + frequency_note
        checks.append(
            ReportCheck(
                skill,
                status,
                detail,
                weight=weight,
                score_value=score_value,
            )
        )

    if not checks:
        checks.append(
            ReportCheck(
                "No explicit hard skills identified",
                "info",
                "The job analyzer did not return technical, domain, or methodology keywords for comparison.",
            )
        )
    return ReportSubsection("Skill comparison", checks)


def _looks_like_soft_skill(value: str) -> bool:
    normalized = _normalize(value)
    return any(
        marker == normalized
        or re.search(rf"\b{re.escape(marker)}\b", normalized)
        for marker in _SOFT_SKILL_MARKERS
    )


def _job_soft_skill_entries(
    analysis: JobAnalysis,
    job_description: str,
) -> list[dict[str, object]]:
    """Collect deduplicated soft-skill keywords and their exact job-description frequency."""
    soft_requirements = []
    for requirement in analysis.requirements:
        candidates = requirement.keywords or [requirement.requirement]
        if requirement.category == "leadership" or any(
            _looks_like_soft_skill(candidate) for candidate in candidates
        ):
            soft_requirements.append(requirement)

    source_text = job_description.strip() or "\n".join(
        requirement.requirement for requirement in soft_requirements
    )
    entries: dict[str, dict[str, object]] = {}

    for requirement in soft_requirements:
        candidates = requirement.keywords or [requirement.requirement]
        for raw_skill in candidates:
            skill = re.sub(r"\s+", " ", raw_skill).strip(" \t\r\n,;:.")
            if (
                not skill
                or len(_words(skill)) > 8
                or len(skill) > 80
                or (requirement.category != "leadership" and not _looks_like_soft_skill(skill))
            ):
                continue
            key = skill.casefold()
            pattern = _exact_phrase_pattern(skill)
            match = pattern.search(source_text) if pattern else None
            display = re.sub(r"\s+", " ", match.group(0)).strip() if match else skill
            job_count = _exact_phrase_count(source_text, display)
            if job_description.strip() and job_count == 0:
                continue

            entry = entries.setdefault(
                key,
                {
                    "skill": display,
                    "job_count": job_count,
                    "priority": requirement.priority,
                    "requirement_ids": [],
                },
            )
            if job_count > int(entry["job_count"]):
                entry["skill"] = display
                entry["job_count"] = job_count
            if _HARD_SKILL_PRIORITY_ORDER[requirement.priority] < _HARD_SKILL_PRIORITY_ORDER[str(entry["priority"])]:
                entry["priority"] = requirement.priority
            requirement_ids = entry["requirement_ids"]
            if requirement.id not in requirement_ids:
                requirement_ids.append(requirement.id)

    return sorted(
        entries.values(),
        key=lambda item: (
            -int(item["job_count"]),
            _HARD_SKILL_PRIORITY_ORDER[str(item["priority"])],
            str(item["skill"]).casefold(),
        ),
    )


def _soft_skill_comparison_subsection(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    job_description: str,
    resume_title: str,
) -> ReportSubsection:
    resume_text = _proposed_resume_text(profile, analysis, proposal, resume_title)
    evidence_lookup = {match.requirement_id: match for match in proposal.evidence_matches}
    checks: list[ReportCheck] = []

    for entry in _job_soft_skill_entries(analysis, job_description):
        skill = str(entry["skill"])
        job_count = int(entry["job_count"])
        resume_count = _exact_phrase_count(resume_text, skill)
        priority = str(entry["priority"])
        evidence = [
            evidence_lookup[requirement_id]
            for requirement_id in entry["requirement_ids"]
            if requirement_id in evidence_lookup
        ]
        all_unsupported = bool(evidence) and all(match.status == "unsupported" for match in evidence)
        any_partial = any(match.status == "partial" for match in evidence)
        status, score_value, coverage = _skill_check_score(
            resume_count=resume_count,
            job_count=job_count,
            all_unsupported=all_unsupported,
            any_partial=any_partial,
        )
        weight = max(job_count, 1) * _SKILL_PRIORITY_WEIGHTS.get(priority, 1.0)

        count_text = (
            f"Resume: {resume_count}, Job description: {job_count}. "
            f"Weighted coverage: {coverage * 100:.1f}%."
        )
        if resume_count == 0:
            if all_unsupported:
                detail = (
                    f"{count_text} This {priority} soft skill is not supported by verified evidence. "
                    "Treat it as a gap and do not add it unless the candidate can support it with a real example."
                )
            else:
                detail = (
                    f"{count_text} The exact job-description spelling is missing from the proposed resume. "
                    "Add it only when verified experience demonstrates the trait or ability."
                )
        elif all_unsupported:
            detail = (
                f"{count_text} The term appears in the resume even though the mapped requirement lacks verified evidence. "
                "Remove it or confirm a supporting example before export."
            )
        elif any_partial:
            detail = (
                f"{count_text} The exact spelling is present, but the supporting evidence is partial. "
                "Its score is reduced until the candidate can verify a concrete example."
            )
        elif coverage < 1.0:
            detail = (
                f"{count_text} The skill is present but appears less frequently than in the job description. "
                "Keep the wording natural and prioritize stronger hard-skill evidence before repeating soft skills."
            )
        else:
            frequency_note = (
                " It is repeated in the job description and the proposed resume provides full frequency coverage."
                if job_count >= 2
                else " The exact spelling is represented with full frequency coverage."
            )
            detail = count_text + frequency_note
        checks.append(
            ReportCheck(
                skill,
                status,
                detail,
                weight=weight,
                score_value=score_value,
            )
        )

    if not checks:
        checks.append(
            ReportCheck(
                "No explicit soft skills identified",
                "info",
                "The job analyzer did not identify communication, leadership, collaboration, analytical, coaching, or similar traits for comparison.",
            )
        )
    return ReportSubsection("Skill comparison", checks)


def _requirement_is_represented(requirement, proposal: TailoringProposal) -> bool:
    included_requirement_ids = {
        requirement_id
        for bullet in proposal.bullet_proposals
        if bullet.include
        for requirement_id in bullet.matched_requirement_ids
    }
    if requirement.id in included_requirement_ids:
        return True

    resume_text = " ".join(
        [
            proposal.professional_summary,
            *proposal.skills.hard_skills,
            *proposal.skills.soft_skills,
            *proposal.skills.tools_software,
            *proposal.skills.industry_knowledge,
            *[
                bullet.proposed_text
                for bullet in proposal.bullet_proposals
                if bullet.include and bullet.proposed_text.strip()
            ],
        ]
    )
    normalized_resume = _normalize(resume_text)
    terms = [*requirement.keywords, requirement.requirement]
    for term in terms:
        normalized_term = _normalize(term)
        if normalized_term and normalized_term in normalized_resume:
            return True
    return False


def _evidence_location_lookup(profile: CandidateProfile) -> dict[str, str]:
    locations: dict[str, str] = {}
    for experience in profile.experiences:
        locations[experience.id] = f"{experience.employer} — {experience.title}"
        for bullet in experience.bullets:
            locations[bullet.id] = (
                f"{bullet.id} — {experience.employer}: {bullet.text}"
            )

    for skill in profile.skills.all_non_language_skills():
        locations.setdefault(skill, f"Verified skill — {skill}")
    for language in profile.skills.languages:
        locations.setdefault(language, f"Verified language — {language}")
    for evidence in profile.supplemental_evidence:
        locations[evidence.id] = f"Candidate confirmation — {evidence.statement}"
        for skill in evidence.verified_skills:
            locations.setdefault(skill, f"Candidate-confirmed skill — {skill}")
    return locations


def _evidence_requirement_result(
    status: str,
    represented: bool,
    acknowledged_no: bool,
) -> tuple[ReportStatus, float, str]:
    if status == "supported" and represented:
        return "pass", 100.0, "Verified evidence supports the requirement and it is represented in the resume."
    if status == "supported":
        return "warning", 75.0, "Verified evidence supports the requirement, but the resume does not currently emphasize it."
    if status == "partial" and represented:
        return "warning", 60.0, "The requirement is represented conservatively, but the supporting evidence is only partial."
    if status == "partial":
        return "warning", 40.0, "Only partial evidence exists and the requirement is not currently represented in the resume."
    if represented:
        return "fail", 0.0, "The requirement appears in the resume without verified supporting evidence. Remove it or confirm the experience."
    if acknowledged_no:
        return "fail", 20.0, "The candidate explicitly confirmed that this requirement is not applicable. It remains an acknowledged gap."
    return "fail", 10.0, "The requirement is unsupported or unresolved and is not represented in the resume."


def _negative_answer_requirement_ids(
    candidate_answers: list[CandidateAnswer] | None,
) -> set[str]:
    return {
        answer.requirement_id
        for answer in (candidate_answers or [])
        if answer.requirement_id and answer.yes_no is False
    }


def _recommended_evidence_action(
    status: str,
    represented: bool,
    acknowledged_no: bool = False,
) -> str:
    if status == "supported" and represented:
        return "Keep the wording concise and preserve the verified evidence."
    if status == "supported":
        return "Consider emphasizing this verified requirement in the proposed resume."
    if status == "partial" and represented:
        return "Use cautious wording and ask the candidate to confirm the remaining scope."
    if status == "partial":
        return "Ask the candidate for confirmation before adding or strengthening this requirement."
    if status == "unsupported":
        if acknowledged_no:
            return "Keep this as an acknowledged gap and do not add it to the resume."
        return "Do not add this claim unless the candidate provides verifiable evidence."
    return "Review this requirement and assign an evidence decision before export."

_EXPORT_NAMES = (
    '_normalize',
    '_words',
    '_month_index',
    '_documented_experience_years',
    '_replace_number_words',
    '_required_experience_years',
    '_job_level_match',
    '_professional_web_links',
    '_estimated_resume_word_count',
    '_selected_bullets',
    '_selected_bullet_ids_by_experience',
    '_proposed_resume_text',
    '_exact_phrase_pattern',
    '_exact_phrase_count',
    '_job_skill_entries',
    '_skill_check_score',
    '_hard_skill_comparison_subsection',
    '_looks_like_soft_skill',
    '_job_soft_skill_entries',
    '_soft_skill_comparison_subsection',
    '_requirement_is_represented',
    '_evidence_location_lookup',
    '_evidence_requirement_result',
    '_negative_answer_requirement_ids',
    '_recommended_evidence_action',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
