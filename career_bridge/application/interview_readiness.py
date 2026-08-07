"""Automatic interview-readiness scoring for Career Bridge.

The calculator is intentionally delivery- and storage-neutral. A saved,
application-specific Interview Preparation contributes 40 points. The latest
scored mock interview contributes the remaining 60 points proportionally.
This keeps the score understandable and prevents a user-entered confidence
number from being presented as an objective readiness measure.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

PREPARATION_POINTS = 40.0
PRACTICE_WEIGHT = 0.60
READY_THRESHOLD = 70.0


def _bounded_score(value: Any) -> float | None:
    try:
        return round(max(0.0, min(100.0, float(value))), 1)
    except (TypeError, ValueError):
        return None


def application_id_from_review(review: dict[str, Any]) -> str:
    """Return the linked Career Bridge application id, when present."""

    return str(
        review.get("career_application_id")
        or review.get("application_id")
        or ""
    ).strip()


def interview_review_score(review: dict[str, Any]) -> float | None:
    """Return the best available overall score from an interview review."""

    scorecard = review.get("interview_scorecard")
    if isinstance(scorecard, dict):
        score = _bounded_score(scorecard.get("overall_score"))
        if score is not None:
            return score
    return _bounded_score(review.get("overall_score") or review.get("final_grade"))


def is_interview_review(review: dict[str, Any]) -> bool:
    """Return whether a transcript record represents scored interview practice."""

    return bool(
        review.get("scorecard_type") == "interview"
        or review.get("interview_scorecard")
        or str(review.get("meeting_id") or "").startswith("mock-interview-")
    )


@dataclass(frozen=True, slots=True)
class InterviewReadinessAssessment:
    application_id: str
    score: float | None
    preparation_ready: bool
    latest_mock_score: float | None
    scored_mock_interviews: int

    @property
    def is_ready(self) -> bool:
        return self.score is not None and self.score >= READY_THRESHOLD

    @property
    def label(self) -> str:
        return "Not calculated" if self.score is None else f"{self.score:.0f}%"

    @property
    def status_label(self) -> str:
        if self.score is None:
            return "Not started"
        if self.is_ready:
            return "Interview-ready"
        if self.preparation_ready and self.latest_mock_score is None:
            return "Practice needed"
        if not self.preparation_ready:
            return "Preparation needed"
        return "Building readiness"

    @property
    def explanation(self) -> str:
        if self.score is None:
            return "Generate Interview Preparation and complete a scored mock interview."
        components: list[str] = []
        components.append(
            "Interview Preparation complete" if self.preparation_ready
            else "Interview Preparation missing"
        )
        if self.latest_mock_score is None:
            components.append("no scored mock interview yet")
        else:
            components.append(f"latest mock interview {self.latest_mock_score:.0f}%")
        return " · ".join(components)


def calculate_interview_readiness(
    application_id: str,
    *,
    preparation_ready: bool,
    latest_mock_score: float | int | str | None,
    scored_mock_interviews: int = 0,
) -> InterviewReadinessAssessment:
    """Calculate one application readiness score from saved workflow evidence."""

    practice_score = _bounded_score(latest_mock_score)
    if not preparation_ready and practice_score is None:
        score = None
    else:
        score = round(
            (PREPARATION_POINTS if preparation_ready else 0.0)
            + (practice_score * PRACTICE_WEIGHT if practice_score is not None else 0.0),
            1,
        )
    return InterviewReadinessAssessment(
        application_id=str(application_id or "").strip(),
        score=score,
        preparation_ready=bool(preparation_ready),
        latest_mock_score=practice_score,
        scored_mock_interviews=max(0, int(scored_mock_interviews or 0)),
    )


def build_interview_readiness_assessments(
    application_ids: Iterable[str],
    *,
    prepared_application_ids: Iterable[str] = (),
    reviews: Iterable[dict[str, Any]] = (),
) -> dict[str, InterviewReadinessAssessment]:
    """Calculate readiness for multiple applications from one review collection."""

    prepared = {str(value or "").strip() for value in prepared_application_ids}
    scores_by_application: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for review in reviews:
        if not isinstance(review, dict) or not is_interview_review(review):
            continue
        application_id = application_id_from_review(review)
        score = interview_review_score(review)
        if not application_id or score is None:
            continue
        scores_by_application[application_id].append(
            (str(review.get("timestamp") or ""), score)
        )

    assessments: dict[str, InterviewReadinessAssessment] = {}
    for raw_application_id in application_ids:
        application_id = str(raw_application_id or "").strip()
        if not application_id:
            continue
        scored = sorted(scores_by_application.get(application_id, []), key=lambda item: item[0])
        latest_score = scored[-1][1] if scored else None
        assessments[application_id] = calculate_interview_readiness(
            application_id,
            preparation_ready=application_id in prepared,
            latest_mock_score=latest_score,
            scored_mock_interviews=len(scored),
        )
    return assessments
