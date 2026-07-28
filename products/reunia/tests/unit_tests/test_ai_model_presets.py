from __future__ import annotations

from flask import Flask
import pytest

from meeting_assistant.services.user_service import UserService
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


def test_get_settings_exposes_preset_for_stored_model_id() -> None:
    repository = _UserRepository({"aiModel": "advanced-model-id"})

    with _app().app_context():
        settings = UserService(repository=repository).get_settings("user-1")

    assert settings["aiModel"] == "advanced-model-id"
    assert settings["aiModelPreset"] == "advanced"


def test_update_settings_maps_balanced_preset_to_model_id() -> None:
    repository = _UserRepository()

    with _app().app_context():
        settings = UserService(repository=repository).update_settings(
            "user-1",
            {"aiModelPreset": "balanced"},
        )

    assert settings["aiModelPreset"] == "balanced"
    assert settings["aiModel"] == "balanced-model-id"
    assert repository.saved_settings["aiModel"] == "balanced-model-id"
    assert "aiModelPreset" not in repository.saved_settings


def test_update_settings_accepts_legacy_configured_model_id() -> None:
    repository = _UserRepository()

    with _app().app_context():
        settings = UserService(repository=repository).update_settings(
            "user-1",
            {"aiModel": "fast-model-id"},
        )

    assert settings["aiModelPreset"] == "fast"
    assert repository.saved_settings["aiModel"] == "fast-model-id"


def test_update_settings_rejects_unknown_model_selection() -> None:
    repository = _UserRepository()

    with _app().app_context(), pytest.raises(ValidationError):
        UserService(repository=repository).update_settings(
            "user-1",
            {"aiModelPreset": "unsupported"},
        )
