from meeting_assistant.services.scoring_service import calculate_overall_performance_score


def test_score_handles_empty_content_and_form_metrics():
    result = calculate_overall_performance_score([], {})
    assert result["content_average_score"] is None
    assert result["form_average_score"] is None
    assert result["final_grade"] is None


def test_score_ignores_ungraded_content_entries():
    result = calculate_overall_performance_score(
        [{"question": "Q1"}, {"grade": "A"}],
        {},
    )
    assert result["content_average_score"] == 95.0
    assert result["form_average_score"] is None
    assert result["final_grade"] == 95.0
