from __future__ import annotations

from pathlib import Path

from resume_tailor.models import CandidateQuestion
from resume_tailor.question_prioritization import (
    candidate_question_display_label,
    order_candidate_questions_for_display,
)


def _question(identifier: str) -> CandidateQuestion:
    return CandidateQuestion(
        id=identifier,
        question=f"Question {identifier}",
        answer_type="yes_no_with_details",
    )


def test_initial_questions_are_sorted_by_numeric_identifier() -> None:
    questions = [_question("Q2"), _question("Q6"), _question("Q1"), _question("Q3"), _question("Q4")]

    ordered = order_candidate_questions_for_display(questions)

    assert [question.id for question in ordered] == ["Q1", "Q2", "Q3", "Q4", "Q6"]


def test_visible_labels_are_consecutive_after_filtered_questions_are_removed() -> None:
    ordered = order_candidate_questions_for_display(
        [_question("Q2"), _question("Q6"), _question("Q1"), _question("Q3"), _question("Q4")]
    )

    labels = [
        candidate_question_display_label(question, position)
        for position, question in enumerate(ordered, start=1)
    ]

    assert labels == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert ordered[-1].id == "Q6"  # Stable internal ID remains unchanged.


def test_follow_up_questions_keep_their_round_aware_ids() -> None:
    questions = [_question("FQ1-2"), _question("FQ1-1")]

    ordered = order_candidate_questions_for_display(questions)

    assert [question.id for question in ordered] == ["FQ1-1", "FQ1-2"]
    assert candidate_question_display_label(ordered[0], 1) == "FQ1-1"


def test_template_uses_candidate_facing_display_label() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "products/resume_taylor/templates/application_builder/index.html").read_text()
    app_source = (root / "products/resume_taylor/app.py").read_text()

    assert "{{ row.display_id }} — {{ q.question }}" in template
    assert '"display_id": candidate_question_display_label(' in app_source
