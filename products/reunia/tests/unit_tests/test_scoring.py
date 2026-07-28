from meeting_assistant.services.scoring_service import calculate_overall_performance_score


def test_score_uses_content_and_form_averages():
    result = calculate_overall_performance_score(
        [{"grade": "A"}, {"grade": "B"}],
        {"pace_grade": "A", "filler_words_grade": "B"},
    )
    assert result["content_average_score"] == 89.0
    assert result["form_average_score"] == 89.0
    assert result["final_grade"] == 89.0
