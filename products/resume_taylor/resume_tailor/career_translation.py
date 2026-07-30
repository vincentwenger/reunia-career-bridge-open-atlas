from __future__ import annotations

import re
from collections import Counter

from .grounding import validate_candidate_claim
from .models import (
    CandidateProfile,
    CareerTranslationAssessment,
    CareerTranslationFinding,
    JobAnalysis,
    NewcomerCareerProfile,
    TailoringProposal,
)

_WORD_RE = re.compile(r"[A-Za-zÀ-ž0-9+#.-]+")
_STOP_WORDS = {
    "and",
    "the",
    "with",
    "for",
    "from",
    "that",
    "this",
    "into",
    "using",
    "role",
    "work",
    "years",
    "experience",
    "skills",
    "knowledge",
    "ability",
    "required",
    "preferred",
}


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _WORD_RE.findall(value)
        if len(token) > 2 and token.casefold() not in _STOP_WORDS
    }


def _finding_key(finding: CareerTranslationFinding) -> tuple[str, str]:
    return finding.category, _normalized(finding.source_text)


def _profile_contains(profile: CandidateProfile, value: str) -> bool:
    return bool(value.strip()) and _normalized(value) in _normalized(profile.all_source_text())


def _evidence_text_lookup(profile: CandidateProfile) -> dict[str, str]:
    lookup: dict[str, str] = {"CANDIDATE-PROFILE": profile.all_source_text()}
    for experience in profile.experiences:
        lookup[experience.id] = " ".join(
            value
            for value in (
                experience.title,
                experience.employer,
                experience.location,
                experience.dates,
                *(bullet.text for bullet in experience.bullets),
            )
            if value.strip()
        )
        for bullet in experience.bullets:
            lookup[bullet.id] = bullet.text
    for index, education in enumerate(profile.education, start=1):
        lookup[f"EDUCATION-{index}"] = " ".join(
            value
            for value in (
                education.credential,
                education.institution,
                education.location,
                education.date,
                education.detail,
            )
            if value.strip()
        )
    for item in profile.supplemental_evidence:
        lookup[item.id] = " ".join(
            value for value in (item.statement, *item.verified_skills) if value.strip()
        )
    return lookup


def _valid_evidence_ids(profile: CandidateProfile) -> set[str]:
    ids = {experience.id for experience in profile.experiences}
    ids.update(profile.bullet_lookup())
    ids.update(item.id for item in profile.supplemental_evidence)
    ids.update(f"EDUCATION-{index}" for index, _ in enumerate(profile.education, start=1))
    ids.add("CANDIDATE-PROFILE")
    return ids


def _evidence_ids_for_text(profile: CandidateProfile, value: str) -> list[str]:
    normalized = _normalized(value)
    if not normalized:
        return []
    matches: list[str] = []
    for experience in profile.experiences:
        if normalized in _normalized(experience.title):
            matches.append(experience.id)
        for bullet in experience.bullets:
            if normalized in _normalized(bullet.text):
                matches.append(bullet.id)
    for index, education in enumerate(profile.education, start=1):
        education_text = " ".join(
            [education.credential, education.institution, education.detail]
        )
        if normalized in _normalized(education_text):
            matches.append(f"EDUCATION-{index}")
    for item in profile.supplemental_evidence:
        if normalized in _normalized(item.statement):
            matches.append(item.id)
    if not matches and normalized in _normalized(profile.all_source_text()):
        matches.append("CANDIDATE-PROFILE")
    return list(dict.fromkeys(matches))


def ensure_career_translation_assessment(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    background: NewcomerCareerProfile | None = None,
) -> TailoringProposal:
    """Supplement the model assessment with deterministic evidence-protection checks.

    The model remains responsible for nuanced terminology and title translation. This
    function guarantees that user-entered credentials are never silently treated as
    evidence, unsupported job requirements stay visible, and confirmation questions
    are represented as missing-evidence findings.
    """

    context = background or NewcomerCareerProfile()
    updated = proposal.model_copy(deep=True)
    assessment = updated.career_translation_assessment.model_copy(deep=True)
    assessment.target_country = assessment.target_country or context.target_country
    assessment.target_role = assessment.target_role or context.target_role or analysis.target_title

    valid_ids = _valid_evidence_ids(profile)
    evidence_lookup = _evidence_text_lookup(profile)
    job_context = "\n".join(
        [
            analysis.target_title,
            analysis.target_company,
            *(requirement.requirement for requirement in analysis.requirements),
            *(keyword for requirement in analysis.requirements for keyword in requirement.keywords),
        ]
    )
    protected_findings: list[CareerTranslationFinding] = []
    for finding in assessment.findings:
        protected = finding.model_copy(deep=True)
        protected.evidence_ids = [
            evidence_id
            for evidence_id in dict.fromkeys(protected.evidence_ids)
            if evidence_id in valid_ids
        ]
        if protected.disposition in {
            "confirmed_experience",
            "reasonable_rephrasing",
        } and not protected.evidence_ids:
            protected.evidence_ids = _evidence_ids_for_text(
                profile, protected.source_text
            )
            if not protected.evidence_ids:
                protected.disposition = "user_clarification_required"
                protected.rationale = (
                    protected.rationale.rstrip()
                    + " The assessment could not trace this item to Candidate Profile evidence, so it cannot shape resume wording yet."
                ).strip()
                protected.recommended_action = (
                    protected.recommended_action
                    or "Confirm the exact fact and attach it to a documented role before using it."
                )

        if protected.disposition in {"confirmed_experience", "reasonable_rephrasing"}:
            cited_evidence = [
                evidence_lookup[evidence_id]
                for evidence_id in protected.evidence_ids
                if evidence_id in evidence_lookup
            ]
            source_findings = validate_candidate_claim(
                protected.source_text,
                cited_evidence,
                require_overlap=True,
            ) if cited_evidence else []
            translation_findings = validate_candidate_claim(
                protected.translated_meaning,
                cited_evidence,
                context_texts=[job_context],
                allow_gap_context=False,
                require_overlap=True,
            ) if cited_evidence and protected.translated_meaning.strip() else []
            if not cited_evidence or source_findings or translation_findings:
                protected.disposition = "user_clarification_required"
                protected.evidence_ids = []
                protected.translated_meaning = (
                    "This proposed translation or interpretation requires candidate confirmation "
                    "before it can shape resume or interview wording."
                )
                protected.rationale = (
                    "The generated interpretation was not fully traceable to the cited Candidate "
                    "Profile evidence."
                )
                protected.recommended_action = (
                    "Confirm the official wording, factual responsibilities, and closest target-market "
                    "explanation before using it."
                )
            else:
                protected.rationale = (
                    "The source wording and evidence IDs are present in the verified Candidate "
                    "Profile; any explanation must preserve those facts."
                )
                protected.recommended_action = (
                    "Keep official facts unchanged and use only the evidence-supported explanation."
                )
        protected_findings.append(protected)
    assessment.findings = protected_findings

    existing_index = {
        _finding_key(item): index
        for index, item in enumerate(assessment.findings)
    }

    def add(
        finding: CareerTranslationFinding,
        *,
        authoritative: bool = False,
    ) -> None:
        key = _finding_key(finding)
        index = existing_index.get(key)
        if index is not None:
            if authoritative:
                current = assessment.findings[index]
                replacement = finding.model_copy(deep=True)
                if current.translated_meaning.strip():
                    replacement.translated_meaning = current.translated_meaning
                assessment.findings[index] = replacement
            return
        assessment.findings.append(finding)
        existing_index[key] = len(assessment.findings) - 1

    title_lookup = {
        _normalized(experience.title): experience
        for experience in profile.experiences
        if experience.title.strip()
    }
    for title in context.unfamiliar_job_titles:
        matched = title_lookup.get(_normalized(title))
        add(
            CareerTranslationFinding(
                category="job_title_translation",
                source_text=title,
                translated_meaning=(
                    f"Explain this documented title in recruiter-readable terms for {assessment.target_role}."
                    if matched
                    else "The title needs a factual description of responsibilities before it can be translated safely."
                ),
                disposition=(
                    "reasonable_rephrasing" if matched else "user_clarification_required"
                ),
                evidence_ids=[matched.id] if matched else [],
                rationale=(
                    "The exact title is documented in the Candidate Profile; only its explanation may be clarified."
                    if matched
                    else "The title was supplied as onboarding context but is not independently documented in the Candidate Profile."
                ),
                recommended_action=(
                    "Keep the official title unchanged and add a concise functional explanation only when useful."
                    if matched
                    else "Confirm the employer, dates, responsibilities, and closest target-market equivalent before using it."
                ),
            ),
            authoritative=True,
        )

    credential_items = [
        ("International credential", item)
        for item in context.international_credentials
    ] + [
        ("Professional certification", item)
        for item in context.professional_certifications
    ]
    for kind, credential in credential_items:
        documented = _profile_contains(profile, credential)
        evidence_ids: list[str] = []
        if documented:
            for index, education in enumerate(profile.education, start=1):
                if _normalized(credential) in _normalized(
                    " ".join(
                        [education.credential, education.institution, education.detail]
                    )
                ):
                    evidence_ids.append(f"EDUCATION-{index}")
            if not evidence_ids:
                evidence_ids.append("CANDIDATE-PROFILE")
        add(
            CareerTranslationFinding(
                category="credential_explanation",
                source_text=credential,
                translated_meaning=(
                    f"{kind} that may need a short target-market explanation."
                ),
                disposition=(
                    "confirmed_experience" if documented else "user_clarification_required"
                ),
                evidence_ids=evidence_ids,
                rationale=(
                    "The credential is present in the Candidate Profile and may be explained without changing its official name."
                    if documented
                    else "The credential was entered during onboarding but is not yet documented in the Candidate Profile."
                ),
                recommended_action=(
                    "Preserve the official credential name and add issuing body or equivalency context only when verified."
                    if documented
                    else "Confirm the official name, issuer, country, completion status, and any verified equivalency before adding it."
                ),
            ),
            authoritative=True,
        )

    requirement_lookup = {item.id: item for item in analysis.requirements}
    question_requirements = {
        item.requirement_id for item in proposal.candidate_questions if item.requirement_id
    }
    unsupported_ids = {
        match.requirement_id
        for match in proposal.evidence_matches
        if match.status == "unsupported"
    }
    for requirement_id in unsupported_ids:
        requirement = requirement_lookup.get(requirement_id)
        if requirement is None:
            continue
        needs_question = requirement_id in question_requirements
        disposition = (
            "user_clarification_required"
            if needs_question
            else (
                "recommended_learning_or_future_action"
                if requirement.priority == "secondary"
                else "unsupported_claim"
            )
        )
        add(
            CareerTranslationFinding(
                category="unsupported_requirement",
                source_text=requirement.requirement,
                translated_meaning="Target-job requirement not supported by current evidence.",
                disposition=disposition,
                evidence_ids=[],
                rationale=(
                    "A focused confirmation question can determine whether relevant experience exists."
                    if needs_question
                    else "No Candidate Profile evidence currently supports this requirement."
                ),
                recommended_action=(
                    "Answer the related confirmation question with a specific fact or keep the requirement outside the resume."
                    if needs_question
                    else (
                        "Treat this as a learning or portfolio-development opportunity rather than current experience."
                        if disposition == "recommended_learning_or_future_action"
                        else "Do not claim this requirement; pursue training, project evidence, or relevant experience before presenting it."
                    )
                ),
            ),
            authoritative=True,
        )

    for question in proposal.candidate_questions:
        requirement = requirement_lookup.get(question.requirement_id)
        add(
            CareerTranslationFinding(
                category="missing_evidence",
                source_text=(
                    requirement.requirement if requirement is not None else question.question
                ),
                translated_meaning="A specific candidate fact could change the resume decision.",
                disposition="user_clarification_required",
                evidence_ids=(
                    [question.source_id]
                    if question.source_id in valid_ids
                    else []
                ),
                rationale=question.help_text or question.question,
                recommended_action=question.details_prompt or "Provide one concise factual example or mark the experience as not applicable.",
            ),
            authoritative=True,
        )

    requirement_tokens = {
        item.id: _tokens(" ".join([item.requirement, *item.keywords]))
        for item in analysis.requirements
    }
    transfer_candidates: list[tuple[int, str, str, list[str]]] = []
    for experience in profile.experiences:
        for bullet in experience.bullets:
            bullet_tokens = _tokens(bullet.text)
            matched_ids = [
                requirement_id
                for requirement_id, tokens in requirement_tokens.items()
                if len(bullet_tokens & tokens) >= 2
            ]
            if matched_ids:
                transfer_candidates.append(
                    (len(matched_ids), bullet.id, bullet.text, matched_ids)
                )
    transfer_candidates.sort(key=lambda row: (-row[0], row[1]))
    for _, bullet_id, bullet_text, matched_ids in transfer_candidates[:3]:
        labels = [requirement_lookup[item].requirement for item in matched_ids[:3]]
        add(
            CareerTranslationFinding(
                category="transferable_skill",
                source_text=bullet_text,
                translated_meaning="Transferable evidence for: " + "; ".join(labels),
                disposition="confirmed_experience",
                evidence_ids=[bullet_id],
                rationale="This documented accomplishment overlaps with multiple target-role requirements even when the original industry or title differs.",
                recommended_action="Lead with the supported responsibility or outcome and keep the original employer, title, scope, and metrics unchanged.",
            )
        )

    counts = Counter(item.disposition for item in assessment.findings)
    base_summary = (
        "Career Bridge reviewed documented titles, credentials, terminology, accomplishments, "
        "and transferable skills while keeping unsupported interpretations outside candidate claims."
    )
    assessment.summary = (
        base_summary
        + " Evidence protection result: "
        + f"{counts['confirmed_experience']} confirmed item(s), "
        + f"{counts['reasonable_rephrasing']} safe translation(s), "
        + f"{counts['user_clarification_required']} clarification item(s), "
        + f"{counts['unsupported_claim']} unsupported claim(s), and "
        + f"{counts['recommended_learning_or_future_action']} future action(s)."
    )

    updated.career_translation_assessment = CareerTranslationAssessment.model_validate(
        assessment.model_dump()
    )
    return updated
