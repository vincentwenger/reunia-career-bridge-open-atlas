from __future__ import annotations

import re
from typing import Any

from .bullet_text import (
    normalize_resume_bullet_terminal_punctuation,
    normalize_resume_bullet_text,
    summarize_confirmation_answer_as_bullet,
)
from .models import (
    BulletProposal,
    CandidateAnswer,
    CandidateProfile,
    CandidateQuestion,
    JobAnalysis,
    ResumeBullet,
    SupplementalEvidence,
    TailoringProposal,
)


_SKILL_CATEGORIES = {"technical_skill", "domain_knowledge", "methodology", "leadership"}
_CONFIRM_ID_RE = re.compile(r"^(?P<prefix>[A-Za-z0-9]+)-CONF-(?P<number>\d+)$", re.IGNORECASE)

_CURRENT_ROLE_RE = re.compile(
    r"\b(?:present|current|now|ongoing|today|actuel|actuelle|actuellement|en cours)\b",
    re.IGNORECASE,
)


def _experience_uses_past_tense(profile: CandidateProfile, experience_id: str) -> bool:
    experience = profile.experience_lookup().get(experience_id)
    return not bool(experience and _CURRENT_ROLE_RE.search(experience.dates or ""))


def validate_candidate_answers(
    questions: list[CandidateQuestion],
    answers: list[CandidateAnswer],
) -> list[str]:
    answer_lookup = {answer.question_id: answer for answer in answers}
    errors: list[str] = []

    for question in questions:
        answer = answer_lookup.get(question.id)
        if answer is None:
            if question.required:
                errors.append(f"{question.id}: Please answer this question.")
            continue

        if answer.yes_no is False:
            continue

        if question.answer_type in {"yes_no", "yes_no_with_details"}:
            if answer.yes_no is None:
                errors.append(f"{question.id}: Select Yes or No.")
                continue
            if answer.yes_no and not answer.text.strip():
                errors.append(
                    f"{question.id}: Add one brief detail so the confirmed experience can be used in the resume."
                )
        elif question.required and not answer.text.strip():
            errors.append(f"{question.id}: Enter an answer or mark it as no relevant experience.")

        if _answer_supports_evidence(question, answer) and not answer.experience_id.strip():
            errors.append(f"{question.id}: Select the job where you gained this experience.")

        if question.answer_type == "number" and answer.text.strip():
            if not re.search(r"\d", answer.text):
                errors.append(f"{question.id}: Enter a number or a numeric result.")

    return errors


def _answer_supports_evidence(question: CandidateQuestion, answer: CandidateAnswer) -> bool:
    if answer.yes_no is False:
        return False
    if question.answer_type in {"yes_no", "yes_no_with_details"}:
        return answer.yes_no is True
    return bool(answer.text.strip())


def _evidence_statement(
    question: CandidateQuestion,
    answer: CandidateAnswer,
    requirement_text: str,
) -> str:
    detail = answer.text.strip()
    if detail:
        return detail
    return f"Candidate confirmed experience relevant to: {requirement_text}."


def _experience_prefix(profile: CandidateProfile, experience_id: str) -> str:
    experience = profile.experience_lookup()[experience_id]
    for bullet in experience.bullets:
        prefix = bullet.id.split("-", 1)[0].strip()
        if prefix:
            return re.sub(r"[^A-Za-z0-9]+", "", prefix).upper()
    fallback = re.sub(r"[^A-Za-z0-9]+", "", experience.id).upper()
    return fallback or "EXP"


def _next_confirmed_bullet_id(profile: CandidateProfile, experience_id: str) -> str:
    prefix = _experience_prefix(profile, experience_id)
    used_numbers: set[int] = set()
    for bullet_id in profile.bullet_lookup():
        match = _CONFIRM_ID_RE.match(bullet_id)
        if match and match.group("prefix").casefold() == prefix.casefold():
            used_numbers.add(int(match.group("number")))
    number = 1
    while number in used_numbers:
        number += 1
    return f"{prefix}-CONF-{number:02d}"


def is_candidate_confirmed_bullet_id(value: str) -> bool:
    return bool(_CONFIRM_ID_RE.match(value.strip()))


def _resume_ready_confirmation_bullet(
    *values: str,
    fallback: str = "",
    use_past_tense: bool = True,
) -> str:
    """Return the first usable action-led bullet from candidate-confirmed prose.

    This is also applied to previously saved workflows so conversational lead-ins
    such as ``From there,`` are repaired when the proposal is reopened.
    """
    for value in values:
        bullet = summarize_confirmation_answer_as_bullet(
            value,
            max_words=35,
            use_past_tense=use_past_tense,
        )
        if bullet:
            return normalize_resume_bullet_terminal_punctuation(bullet)
    return normalize_resume_bullet_terminal_punctuation(
        normalize_resume_bullet_text(fallback, max_words=35)
    )


def build_profile_with_candidate_answers(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    questions: list[CandidateQuestion],
    answers: list[CandidateAnswer],
) -> CandidateProfile:
    """Attach every affirmative answer to a role as traceable source evidence.

    A candidate-confirmed answer becomes both supplemental evidence (CONF-Q*) and a
    source bullet under the selected experience (for example NAS-CONF-01). Downstream
    proposal, validation, comparison, audit, and export logic can therefore treat it as
    first-class resume evidence without inventing an employer or placement.
    """
    updated = profile.model_copy(deep=True)
    requirement_lookup = {item.id: item for item in analysis.requirements}
    question_lookup = {item.id: item for item in questions}
    experience_lookup = updated.experience_lookup()

    existing_evidence_ids = {item.id for item in updated.supplemental_evidence}
    for answer in answers:
        question = question_lookup.get(answer.question_id)
        if question is None or not _answer_supports_evidence(question, answer):
            continue
        experience_id = answer.experience_id.strip()
        if experience_id not in experience_lookup:
            raise ValueError(
                f"{question.id}: The selected job is no longer available. Select a valid experience."
            )

        requirement = requirement_lookup.get(question.requirement_id)
        requirement_text = requirement.requirement if requirement else question.question
        evidence_id = f"CONF-{re.sub(r'[^A-Za-z0-9_-]+', '-', question.id).strip('-')}"
        if not evidence_id or evidence_id in existing_evidence_ids:
            continue

        statement = _evidence_statement(question, answer, requirement_text)
        resume_bullet_text = _resume_ready_confirmation_bullet(
            statement,
            fallback=requirement_text,
            use_past_tense=_experience_uses_past_tense(updated, experience_id),
        )
        source_bullet_id = _next_confirmed_bullet_id(updated, experience_id)
        experience_lookup[experience_id].bullets.append(
            ResumeBullet(id=source_bullet_id, text=resume_bullet_text)
        )

        verified_skills: list[str] = []
        if requirement and requirement.category in _SKILL_CATEGORIES:
            verified_skills = list(
                dict.fromkeys(keyword.strip() for keyword in requirement.keywords if keyword.strip())
            )

        updated.supplemental_evidence.append(
            SupplementalEvidence(
                id=evidence_id,
                statement=statement,
                requirement_ids=[question.requirement_id] if question.requirement_id else [],
                verified_skills=verified_skills,
                experience_id=experience_id,
                source_bullet_id=source_bullet_id,
                placement=answer.placement,
            )
        )
        existing_evidence_ids.add(evidence_id)

    # Re-validate uniqueness after mutating the deep copy.
    return CandidateProfile.model_validate(updated.model_dump())


def _experience_limits(index: int) -> tuple[int, int]:
    return [(6, 7), (3, 4), (2, 3)][index] if index < 3 else (2, 3)


def ensure_confirmed_answers_visible(
    profile: CandidateProfile,
    proposal: TailoringProposal,
) -> TailoringProposal:
    """Guarantee a visible, traceable disposition for every affirmative answer.

    The model may merge a candidate-confirmed source bullet into an existing bullet by
    citing its CONF evidence ID or source bullet ID. If it does not, the confirmed source
    bullet is included as a new bullet. This conservative fallback ensures an answer is
    never retained only as hidden evidence metadata.
    """
    updated = proposal.model_copy(deep=True)
    lookup = {item.source_bullet_id: item for item in updated.bullet_proposals}
    source_lookup = profile.bullet_lookup()
    confirmed = [
        evidence
        for evidence in profile.supplemental_evidence
        if evidence.source == "candidate_confirmation" and evidence.source_bullet_id
    ]
    confirmed_ids = {item.source_bullet_id for item in confirmed}

    for evidence in confirmed:
        source_id = evidence.source_bullet_id
        item = lookup.get(source_id)
        if item is None:
            item = BulletProposal(
                source_bullet_id=source_id,
                include=True,
                proposed_text=_resume_ready_confirmation_bullet(
                    evidence.statement,
                    source_lookup.get(source_id, ""),
                    fallback=evidence.statement,
                    use_past_tense=_experience_uses_past_tense(
                        profile,
                        evidence.experience_id,
                    ),
                ),
                matched_requirement_ids=list(evidence.requirement_ids),
                evidence_note=f"Candidate-confirmed experience from {evidence.id}.",
            )
            updated.bullet_proposals.append(item)
            lookup[source_id] = item

        references = (evidence.id.casefold(), source_id.casefold())
        represented_by = next(
            (
                other
                for other in updated.bullet_proposals
                if other.source_bullet_id != source_id
                and other.include
                and any(token in other.evidence_note.casefold() for token in references)
            ),
            None,
        )

        if evidence.placement == "new_bullet" or represented_by is None:
            item.include = True
            item.proposed_text = _resume_ready_confirmation_bullet(
                item.proposed_text.strip(),
                evidence.statement,
                source_lookup.get(source_id, ""),
                fallback=evidence.statement,
                use_past_tense=_experience_uses_past_tense(
                    profile,
                    evidence.experience_id,
                ),
            )
            item.matched_requirement_ids = list(
                dict.fromkeys(item.matched_requirement_ids + evidence.requirement_ids)
            )
            item.evidence_note = f"Candidate-confirmed experience from {evidence.id}."
        else:
            item.include = False
            item.evidence_note = (
                f"Candidate-confirmed source bullet {source_id} is represented by "
                f"{represented_by.source_bullet_id}; supported by {evidence.id}."
            )

        for requirement_id in evidence.requirement_ids:
            match = next(
                (row for row in updated.evidence_matches if row.requirement_id == requirement_id),
                None,
            )
            if match is not None and evidence.id not in match.evidence_ids:
                match.evidence_ids.append(evidence.id)

    # Keep role-specific bullet limits valid while prioritizing confirmed content.
    proposal_lookup = {item.source_bullet_id: item for item in updated.bullet_proposals}
    for index, experience in enumerate(profile.experiences):
        _, maximum = _experience_limits(index)
        selected = [
            proposal_lookup[bullet.id]
            for bullet in experience.bullets
            if bullet.id in proposal_lookup and proposal_lookup[bullet.id].include
        ]
        overflow = len(selected) - maximum
        if overflow <= 0:
            continue
        removable = [item for item in reversed(selected) if item.source_bullet_id not in confirmed_ids]
        for item in removable[:overflow]:
            item.include = False
            if not item.evidence_note.strip():
                item.evidence_note = "Not selected after prioritizing candidate-confirmed experience."

    return updated


def confirmation_dispositions(
    profile: CandidateProfile | None,
    proposal: TailoringProposal | None,
    answers: list[CandidateAnswer],
) -> list[dict[str, Any]]:
    if profile is None or proposal is None:
        return []
    evidence_by_question = {
        item.id.removeprefix("CONF-"): item
        for item in profile.supplemental_evidence
        if item.source == "candidate_confirmation"
    }
    experience_lookup = profile.experience_lookup()
    proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
    rows: list[dict[str, Any]] = []
    for answer in answers:
        if answer.yes_no is False or not (answer.yes_no is True or answer.text.strip()):
            continue
        evidence = evidence_by_question.get(answer.question_id)
        if evidence is None:
            continue
        own = proposal_lookup.get(evidence.source_bullet_id)
        represented_by = next(
            (
                item
                for item in proposal.bullet_proposals
                if item.source_bullet_id != evidence.source_bullet_id
                and item.include
                and (
                    evidence.id.casefold() in item.evidence_note.casefold()
                    or evidence.source_bullet_id.casefold() in item.evidence_note.casefold()
                )
            ),
            None,
        )
        if represented_by:
            result = f"Updated existing bullet {represented_by.source_bullet_id}"
            status = "updated"
        elif own and own.include:
            result = f"Added new bullet {evidence.source_bullet_id}"
            status = "added"
        else:
            result = "Not represented in visible resume"
            status = "warning"
        experience = experience_lookup.get(evidence.experience_id)
        rows.append(
            {
                "question_id": answer.question_id,
                "answer": answer.text.strip(),
                "experience": experience.employer if experience else evidence.experience_id,
                "result": result,
                "status": status,
            }
        )
    return rows
