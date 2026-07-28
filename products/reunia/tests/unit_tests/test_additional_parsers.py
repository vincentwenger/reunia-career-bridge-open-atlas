from meeting_assistant.utils.json_parsing import (
    normalize_transcript_item,
    parse_content_grades,
    parse_form_metrics,
    parse_meeting_insights,
    parse_scorecard_grading,
)


def test_meeting_insights_handles_markdown_json_fence():
    raw = '''```json
    {"meeting_name":"Weekly sync","summary":"Done","action_items":[],"open_questions":[]}
    ```'''
    result = parse_meeting_insights(raw)
    assert result["meeting_name"] == "Weekly sync"
    assert result["summary"] == "Done"


def test_content_grades_invalid_input_returns_empty_list():
    assert parse_content_grades("not valid json") == []


def test_form_metrics_missing_fields_are_none():
    result = parse_form_metrics('{"pace_wpm": 130}')
    assert result["pace_wpm"] == 130
    assert result["filler_words_count"] is None
    assert result["negative_tone_grade"] is None


def test_transcript_normalization_preserves_native_values():
    item = {
        "meeting_name": "Planning",
        "content_grades": [{"grade": "A"}],
        "key_wins": ["Clear decision"],
        "improvement_areas": ["Shorter answers"],
    }
    assert normalize_transcript_item(item) == item


def test_combined_scorecard_validates_sections_independently():
    result = parse_scorecard_grading(
        '{"content_grades":[{"question":"Q","answer":"A","grade":"invalid"}],"form_metrics":"invalid"}'
    )
    assert result["content_grades"][0]["grade"] == "F"
    assert result["form_metrics"]["pace_wpm"] is None
