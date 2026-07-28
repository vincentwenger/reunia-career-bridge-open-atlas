from __future__ import annotations

from typing import Any


GRADE_MAP = {
    "A+": 100,
    "A": 95,
    "A-": 90,
    "B+": 87,
    "B": 83,
    "B-": 80,
    "C+": 77,
    "C": 73,
    "C-": 70,
    "D+": 67,
    "D": 63,
    "D-": 60,
    "F": 0,
}

FORM_GRADE_KEYS = {
    "pace_grade",
    "filler_words_grade",
    "power_words_grade",
    "negative_words_grade",
    "negative_tone_grade",
    "pauses_grade",
}

CONTENT_WEIGHT = 0.65
FORM_WEIGHT = 0.35
EVIDENCE_BASELINE_SCORE = 65.0


def calculate_overall_performance_score(
    content_grades: list[dict[str, Any]],
    form_metrics: dict[str, Any],
    evidence: dict[str, Any] | None = None,
) -> dict[str, float | None]:
    """Calculate raw and evidence-adjusted scorecard scores.

    Existing callers that do not pass evidence retain the legacy behavior. During
    transcript analysis, section evidence prevents a tiny communication sample
    from receiving an unjustifiably high aggregate score merely because few
    weaknesses were observable.
    """
    content_scores = [
        score
        for item in content_grades
        if (score := _grade_to_score(item.get("grade"))) is not None
    ]

    form_scores = [
        score
        for key in FORM_GRADE_KEYS
        if (score := _grade_to_score(form_metrics.get(key))) is not None
    ]

    raw_content_average = _average(content_scores, round_result=False)
    raw_form_average = _average(form_scores, round_result=False)

    content_average = _apply_evidence_adjustment(
        raw_content_average,
        _section_evidence(evidence, "content"),
    )
    form_average = _apply_evidence_adjustment(
        raw_form_average,
        _section_evidence(evidence, "form"),
    )

    if content_average is not None and form_average is not None:
        final_score = (
            content_average * CONTENT_WEIGHT
            + form_average * FORM_WEIGHT
        )
    elif content_average is not None:
        final_score = content_average
    elif form_average is not None:
        final_score = form_average
    else:
        final_score = None

    return {
        "content_raw_score": _round_optional(raw_content_average),
        "form_raw_score": _round_optional(raw_form_average),
        "content_average_score": _round_optional(content_average),
        "form_average_score": _round_optional(form_average),
        "final_grade": _round_optional(final_score),
    }


def _section_evidence(
    evidence: dict[str, Any] | None,
    section: str,
) -> dict[str, Any] | None:
    if not isinstance(evidence, dict):
        return None
    value = evidence.get(section)
    return value if isinstance(value, dict) else None


def _apply_evidence_adjustment(
    raw_score: float | None,
    section_evidence: dict[str, Any] | None,
) -> float | None:
    if raw_score is None or section_evidence is None:
        return raw_score

    level = str(section_evidence.get("level") or "reliable").strip().lower()
    if level == "insufficient":
        return None
    if level != "limited":
        return raw_score

    try:
        ratio = float(section_evidence.get("ratio", 1.0))
    except (TypeError, ValueError):
        ratio = 1.0
    ratio = max(0.0, min(1.0, ratio))
    return EVIDENCE_BASELINE_SCORE + (raw_score - EVIDENCE_BASELINE_SCORE) * ratio


def _grade_to_score(value: Any) -> float | None:
    if not isinstance(value, str):
        return None

    normalized_grade = value.strip().upper()
    return GRADE_MAP.get(normalized_grade)


def _average(
    scores: list[float],
    *,
    round_result: bool = True,
) -> float | None:
    if not scores:
        return None

    result = sum(scores) / len(scores)
    return round(result, 1) if round_result else result


def _round_optional(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None
