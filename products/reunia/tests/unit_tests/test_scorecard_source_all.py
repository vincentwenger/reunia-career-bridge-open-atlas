from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from meeting_assistant.services.transcript_analysis_service import (
    TranscriptAnalysisService,
    _filter_labeled_lines,
    _resolve_scorecard_source,
    _ALL_GRADING_LABELS,
)
from meeting_assistant.services.user_service import UserService, _normalize_scorecard_source


class _UserRepository:
    def __init__(self) -> None:
        self.saved_settings = None

    def get_by_id(self, user_id: str):
        return {"user_id": user_id, "settings": {}}

    def update_settings(self, user_id: str, settings: dict):
        self.saved_settings = dict(settings)
        return settings


class _ChatCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][1]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(self.responses)))]
        )


class _OpenAIClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = SimpleNamespace(completions=_ChatCompletions(responses))


def test_user_settings_accept_all_audio_sources() -> None:
    app = Flask(__name__)
    app.config["DEFAULT_AI_MODEL"] = "test-model"
    repository = _UserRepository()

    with app.app_context():
        result = UserService(repository=repository).update_settings(
            "user-1",
            {"scorecard_source": "all"},
        )

    assert result["scorecard_source"] == "all"
    assert repository.saved_settings["scorecard_source"] == "all"


def test_all_audio_aliases_are_normalized() -> None:
    for value in ("all", "any", "both", "any or both", "all_audio_sources"):
        assert _normalize_scorecard_source(value) == "all"
        assert _resolve_scorecard_source({"scorecard_source": value}) == "all"


def test_all_source_filter_uses_whichever_sources_are_available() -> None:
    microphone_only = _filter_labeled_lines(
        "[MICROPHONE] My answer\nUnlabeled text",
        _ALL_GRADING_LABELS,
    )
    speaker_only = _filter_labeled_lines(
        "[SPEAKER] Their answer\nUnlabeled text",
        _ALL_GRADING_LABELS,
    )

    assert microphone_only == "[MICROPHONE] My answer"
    assert speaker_only == "[SPEAKER] Their answer"


def test_analysis_all_works_with_speaker_audio_only() -> None:
    client = _OpenAIClient(
        [
            '{"meeting_name":"Test","summary":"Summary","action_items":[],"open_questions":[]}',
            '{"content_grades":[{"question":"Can you help?","answer":"I can help.","relevance_analysis":"Direct answer.","grade":"A"}],"form_metrics":{"pace_wpm":120,"pace_grade":"A","filler_words_count":0,"filler_words":[],"filler_words_grade":"A","power_words_count":0,"power_words":[],"power_words_grade":"A","negative_words_count":0,"negative_words":[],"negative_words_grade":"A","negative_tone_count":0,"negative_tone":[],"negative_tone_grade":"A","pauses_count":0,"pauses_grade":"A","overall_assessment":"Clear communication."}}',
            '{"key_wins":[],"improvement_areas":[]}',
        ]
    )

    result = TranscriptAnalysisService(client=client).analyze(
        "[SPEAKER] I can help.",
        "test-model",
        {"scorecard_source": "all"},
    )

    assert result["scorecard_source"] == "all"
    assert result["content_grades"][0]["answer"] == "I can help."
    assert len(client.chat.completions.prompts) == 3
    assert "ANY supported microphone or speaker" in client.chat.completions.prompts[1]
    assert "Analyze ALL supported" in client.chat.completions.prompts[1]
    assert '"content_grades"' in client.chat.completions.prompts[1]
    assert '"form_metrics"' in client.chat.completions.prompts[1]
