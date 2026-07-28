from __future__ import annotations

from meeting_assistant.services.scoring_service import calculate_overall_performance_score
from meeting_assistant.services.transcript_analysis_service import _build_scorecard_evidence


def test_insufficient_evidence_suppresses_aggregate_scores() -> None:
    result = calculate_overall_performance_score(
        [{"grade": "A"}],
        {"pace_grade": "A", "filler_words_grade": "A"},
        evidence={
            "content": {"level": "insufficient", "ratio": 0.1},
            "form": {"level": "insufficient", "ratio": 0.1},
        },
    )

    assert result["content_raw_score"] == 95.0
    assert result["form_raw_score"] == 95.0
    assert result["content_average_score"] is None
    assert result["form_average_score"] is None
    assert result["final_grade"] is None


def test_limited_evidence_moderates_high_raw_scores() -> None:
    result = calculate_overall_performance_score(
        [{"grade": "A"}],
        {"pace_grade": "A", "filler_words_grade": "A"},
        evidence={
            "content": {"level": "limited", "ratio": 0.2},
            "form": {"level": "limited", "ratio": 0.2},
        },
    )

    assert result["content_raw_score"] == 95.0
    assert result["content_average_score"] == 71.0
    assert result["form_average_score"] == 71.0
    assert result["final_grade"] == 71.0


def test_evidence_requires_repeated_substantive_content() -> None:
    transcript = "[MICROPHONE] " + " ".join(["word"] * 180)
    evidence = _build_scorecard_evidence(
        transcript,
        [
            {
                "answer": "This is one substantive answer with enough words to count.",
                "grade": "A",
            }
        ],
    )

    assert evidence["content"]["level"] == "insufficient"
    assert evidence["content"]["substantive_response_count"] == 1
    assert evidence["form"]["level"] == "limited"
