"""Keep candidate confirmation focused on high-value, job-relevant facts."""

from __future__ import annotations

import re

from .models import CandidateQuestion, JobAnalysis, TailoringProposal

MAX_INITIAL_CONFIRMATION_QUESTIONS = 6

_PRIORITY_RANK = {"critical": 0, "important": 1, "secondary": 2}
_ANSWER_TYPE_RANK = {
    "yes_no_with_details": 0,
    "yes_no": 1,
    "number": 2,
    "date_or_range": 3,
    "short_text": 4,
    "long_text": 5,
}


def _normalized_question(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold()).strip()


def prioritize_candidate_questions(
    proposal: TailoringProposal,
    analysis: JobAnalysis,
    *,
    maximum: int = MAX_INITIAL_CONFIRMATION_QUESTIONS,
) -> TailoringProposal:
    """Return a copy with only material confirmation questions.

    Questions are useful only when they are tied to a critical or important job
    requirement that is not already fully supported. Secondary requirements and
    duplicate prompts are left as acknowledged gaps rather than increasing user effort.
    """
    updated = proposal.model_copy(deep=True)
    if not updated.candidate_questions or maximum <= 0:
        updated.candidate_questions = []
        return updated

    requirements = {item.id: item for item in analysis.requirements}
    evidence_status = {
        item.requirement_id: item.status for item in updated.evidence_matches
    }
    ranked: list[tuple[tuple[int, int, int, int], CandidateQuestion]] = []
    seen: set[tuple[str, str, str]] = set()

    for position, question in enumerate(updated.candidate_questions):
        requirement = requirements.get(question.requirement_id)
        if requirement is None:
            # Untied questions are too difficult for users to evaluate and cannot
            # demonstrate material value against the target job.
            continue
        if requirement.priority == "secondary":
            continue
        if evidence_status.get(requirement.id) == "supported":
            continue

        dedupe_key = (
            requirement.id,
            question.source_id.strip().casefold(),
            _normalized_question(question.question),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        rank = (
            _PRIORITY_RANK.get(requirement.priority, 9),
            0 if evidence_status.get(requirement.id) == "partial" else 1,
            _ANSWER_TYPE_RANK.get(question.answer_type, 9),
            position,
        )
        ranked.append((rank, question))

    ranked.sort(key=lambda item: item[0])
    updated.candidate_questions = [
        question.model_copy(deep=True) for _, question in ranked[:maximum]
    ]
    return updated
