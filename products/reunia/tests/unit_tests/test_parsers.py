from meeting_assistant.utils.json_parsing import (
    normalize_transcript_item,
    parse_content_grades,
    parse_form_metrics,
    parse_meeting_insights,
    parse_scorecard_grading,
)


def test_parse_meeting_insights_json():
    result = parse_meeting_insights(
        '{"meeting_name":"Planning","summary":"Summary","action_items":["Ship"],"open_questions":[]}'
    )
    assert result["meeting_name"] == "Planning"
    assert result["topics"] == []
    assert result["action_items"] == ["Ship"]


def test_parse_content_grades_returns_native_lists():
    result = parse_content_grades(
        '{"content_grades":[{"question":"Q","answer":"A","relevance_analysis":"Direct","grade":"A"}]}'
    )
    assert result == [
        {"question": "Q", "answer": "A", "relevance_analysis": "Direct", "grade": "A"}
    ]


def test_parse_form_null_returns_empty_metrics():
    assert parse_form_metrics("null")["pace_wpm"] is None


def test_old_dynamodb_shapes_are_normalized():
    item = {
        "content_grades": {"L": [{"M": {"grade": {"S": "A"}}}]},
        "key_wins": {"L": [{"S": "Good outcome"}]},
    }
    normalized = normalize_transcript_item(item)
    assert normalized["content_grades"] == [{"grade": "A"}]
    assert normalized["key_wins"] == ["Good outcome"]


def test_parse_combined_scorecard_response():
    result = parse_scorecard_grading(
        '{"content_grades":[{"question":"Q","answer":"A","relevance_analysis":"Direct","grade":"A"}],"form_metrics":{"pace_wpm":125,"pace_grade":"A","filler_words":[],"filler_words_grade":"A"}}'
    )
    assert result["content_grades"][0]["grade"] == "A"
    assert result["form_metrics"]["pace_wpm"] == 125
    assert result["form_metrics"]["filler_words_count"] == 0
