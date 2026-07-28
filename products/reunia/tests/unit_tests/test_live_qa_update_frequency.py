from __future__ import annotations

from flask import Flask
import pytest

from meeting_assistant.services.user_service import (
    UserService,
    live_qa_answer_update_profile,
)
from meeting_assistant.utils.exceptions import ValidationError


class _UserRepository:
    def __init__(self, settings: dict | None = None) -> None:
        self.user = {"user_id": "user-1", "settings": settings or {}}
        self.saved_settings: dict | None = None

    def get_by_id(self, user_id: str):
        return self.user

    def update_settings(self, user_id: str, settings: dict):
        self.saved_settings = dict(settings)
        self.user["settings"] = dict(settings)
        return settings


def _app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        DEFAULT_AI_MODEL="balanced-model-id",
        DEFAULT_AI_MODEL_PRESET="balanced",
        AI_MODEL_PRESETS={
            "fast": "fast-model-id",
            "balanced": "balanced-model-id",
            "advanced": "advanced-model-id",
        },
    )
    return app


def test_live_qa_update_frequency_defaults_to_efficient() -> None:
    repository = _UserRepository()

    with _app().app_context():
        settings = UserService(repository=repository).get_settings("user-1")

    assert settings["liveQaAnswerUpdateFrequency"] == "efficient"


def test_live_qa_update_frequency_is_saved_for_user() -> None:
    repository = _UserRepository()

    with _app().app_context():
        settings = UserService(repository=repository).update_settings(
            "user-1",
            {"liveQaAnswerUpdateFrequency": "fast"},
        )

    assert settings["liveQaAnswerUpdateFrequency"] == "fast"
    assert repository.saved_settings["liveQaAnswerUpdateFrequency"] == "fast"


def test_live_qa_update_frequency_rejects_unknown_value() -> None:
    repository = _UserRepository()

    with _app().app_context(), pytest.raises(ValidationError):
        UserService(repository=repository).update_settings(
            "user-1",
            {"liveQaAnswerUpdateFrequency": "instant"},
        )


def test_live_qa_update_profiles_use_safe_intervals() -> None:
    assert live_qa_answer_update_profile("fast") == {
        "persist_interval_seconds": 1.0,
        "stream_interval_seconds": 1.0,
        "max_cache_age_seconds": 1.0,
    }
    assert live_qa_answer_update_profile("balanced")["stream_interval_seconds"] == 2.0
    assert live_qa_answer_update_profile("efficient")["persist_interval_seconds"] == 5.0
