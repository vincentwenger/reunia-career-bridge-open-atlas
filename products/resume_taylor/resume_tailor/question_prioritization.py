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
_INITIAL_QUESTION_ID = re.compile(r"^Q[\s_-]*(\d+)(?:\D.*)?$", re.IGNORECASE)
_FOLLOW_UP_QUESTION_ID = re.compile(
    r"^FQ[\s_-]*(?:(\d+)[\s_-]+)?(\d+)$", re.IGNORECASE
)


def candidate_question_display_sort_key(
    question: CandidateQuestion,
) -> tuple[int, int, int, str]:
    """Sort question identifiers naturally for the candidate-facing form.

    AI generation and priority filtering can leave identifiers such as Q2 and Q6
    while omitting Q5.  The internal identifiers must remain stable for form
    fields and saved evidence, but the visible question list should follow the
    original numeric sequence rather than priority rank.
    """

    identifier = question.id.strip()
    initial_match = _INITIAL_QUESTION_ID.match(identifier)
    if initial_match:
        return (0, int(initial_match.group(1)), 0, identifier.casefold())

    follow_up_match = _FOLLOW_UP_QUESTION_ID.match(identifier)
    if follow_up_match:
        round_number = int(follow_up_match.group(1) or 0)
        question_number = int(follow_up_match.group(2))
        return (1, round_number, question_number, identifier.casefold())

    numeric_part = re.search(r"\d+", identifier)
    if numeric_part:
        return (2, int(numeric_part.group()), 0, identifier.casefold())
    return (3, 0, 0, identifier.casefold())


def order_candidate_questions_for_display(
    questions: list[CandidateQuestion],
) -> list[CandidateQuestion]:
    """Return questions in natural numeric order without changing their IDs."""

    return sorted(questions, key=candidate_question_display_sort_key)


def candidate_question_display_label(
    question: CandidateQuestion, position: int
) -> str:
    """Return a consecutive candidate-facing label while preserving internal IDs."""

    if question.id.strip().upper().startswith("FQ"):
        return question.id
    return f"Q{max(1, position)}"


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
    selected_questions = [
        question.model_copy(deep=True) for _, question in ranked[:maximum]
    ]
    updated.candidate_questions = order_candidate_questions_for_display(
        selected_questions
    )
    return updated
