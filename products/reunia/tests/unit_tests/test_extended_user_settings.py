from __future__ import annotations

import io
import time
from types import SimpleNamespace

import pytest
from flask import Flask
from werkzeug.datastructures import FileStorage

from meeting_assistant.repositories.knowledge_repository import InMemoryKnowledgeRepository
from meeting_assistant.services.knowledge_service import KnowledgeService
from meeting_assistant.services.meeting_share_service import MeetingShareService
from meeting_assistant.services.transcript_analysis_service import TranscriptAnalysisService
from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.exceptions import ValidationError


class _UserRepository:
    def __init__(self, settings: dict | None = None) -> None:
        self.user = {"user_id": "user-1", "settings": dict(settings or {})}
        self.saved_settings: dict | None = None

    def get_by_id(self, user_id: str):
        return self.user

    def update_settings(self, user_id: str, settings: dict):
        self.saved_settings = dict(settings)
        self.user["settings"] = dict(settings)
        return settings


def _settings_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        DEFAULT_AI_MODEL="fast-model-id",
        DEFAULT_AI_MODEL_PRESET="fast",
        AI_MODEL_PRESETS={
            "fast": "fast-model-id",
            "balanced": "balanced-model-id",
            "advanced": "advanced-model-id",
        },
        ALLOW_CLIENT_AI_MODEL_OVERRIDE=False,
    )
    return app


def test_extended_settings_have_resource_saving_defaults() -> None:
    with _settings_app().app_context():
        settings = UserService(repository=_UserRepository()).get_settings("user-1")

    assert settings["meetingRetentionDays"] == 7
    assert settings["documentRetentionDays"] == 7
    assert settings["shareDefaultExpirationDays"] == 30
    assert settings["shareRequirePassword"] is False
    assert settings["shareAllowDownload"] is False
    assert settings["shareIncludeScorecard"] is False
    assert settings["meetingSummaryDetail"] == "brief"
    assert settings["meetingExtractActionItems"] is True
    assert settings["meetingGenerateScorecard"] is True


def test_extended_settings_are_validated_and_persisted() -> None:
    repository = _UserRepository()
    payload = {
        "meetingRetentionDays": 90,
        "documentRetentionDays": 30,
        "shareDefaultExpirationDays": 7,
        "shareRequirePassword": True,
        "shareAllowDownload": True,
        "shareIncludeScorecard": True,
        "meetingSummaryDetail": "detailed",
        "meetingExtractActionItems": False,
        "meetingGenerateScorecard": False,
    }
    with _settings_app().app_context():
        result = UserService(repository=repository).update_settings("user-1", payload)

    for key, value in payload.items():
        assert result[key] == value
        assert repository.saved_settings[key] == value


@pytest.mark.parametrize(
    "payload",
    [
        {"meetingRetentionDays": 10},
        {"documentRetentionDays": -1},
        {"shareDefaultExpirationDays": 365},
        {"meetingSummaryDetail": "exhaustive"},
    ],
)
def test_extended_settings_reject_unknown_options(payload: dict) -> None:
    with _settings_app().app_context(), pytest.raises(ValidationError):
        UserService(repository=_UserRepository()).update_settings("user-1", payload)


class _ChatCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][1]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(self.responses)))]
        )


def test_meeting_review_preferences_change_analysis_behavior() -> None:
    completions = _ChatCompletions(
        [
            '{"meeting_name":"Planning","summary":"Detailed summary","action_items":["Hidden task"],"open_questions":[]}',
            '{"key_wins":[],"improvement_areas":[]}',
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = TranscriptAnalysisService(client=client).analyze(
        "[MICROPHONE] We approved the plan.",
        "test-model",
        {
            "meetingSummaryDetail": "detailed",
            "meetingExtractActionItems": False,
            "meetingGenerateScorecard": False,
        },
    )

    assert result["action_items"] == []
    assert result["content_grades"] == []
    assert len(completions.prompts) == 2
    assert "detailed summary covering the main discussion" in completions.prompts[0]
    assert "empty action_items array" in completions.prompts[0]


class _TranscriptRepository:
    def __init__(self, records: list[dict] | None = None) -> None:
        self.created: dict | None = None
        self.records = list(records or [])
        self.deleted: list[tuple[str, str, str]] = []

    def create(self, item: dict) -> None:
        self.created = dict(item)

    def list_for_user(self, user_id: str) -> list[dict]:
        return list(self.records)

    def delete_owned(self, user_id: str, meeting_id: str, timestamp: str) -> None:
        self.deleted.append((user_id, meeting_id, timestamp))


class _AnalysisService:
    def analyze(self, **kwargs):
        return {"meeting_name": "Meeting", "summary": "Summary"}


class _SettingsService:
    def __init__(self, settings: dict) -> None:
        self.settings = settings

    def get_settings(self, user_id: str) -> dict:
        return dict(self.settings)


def test_meeting_retention_marks_new_records_and_removes_expired_records() -> None:
    repository = _TranscriptRepository()
    with _settings_app().app_context():
        service = TranscriptService(
            repository=repository,
            analysis_service=_AnalysisService(),
            user_service=_SettingsService(
                {"aiModel": "test-model", "meetingRetentionDays": 7}
            ),
        )
        service.create("user-1", {"meeting_id": "new", "transcript": "Hello"})

    assert repository.created["retention_expires_at"] > int(time.time())

    expired = {
        "meeting_id": "old",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "retention_expires_at": int(time.time()) - 1,
    }
    repository = _TranscriptRepository([expired])
    with _settings_app().app_context():
        records = TranscriptService(repository=repository).list_for_user("user-1")
    assert records == []
    assert repository.deleted == [
        ("user-1", "old", "2026-01-01T00:00:00+00:00")
    ]


class _FileStore:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def put(self, object_key: str, content: bytes, content_type: str) -> None:
        self.items[object_key] = content

    def get(self, object_key: str) -> bytes:
        return self.items[object_key]

    def delete(self, object_key: str) -> None:
        self.items.pop(object_key, None)


def test_document_retention_marks_uploads_and_cleans_expired_files() -> None:
    app = _settings_app()
    app.config.update(TESTING=False, KNOWLEDGE_MAX_FILE_BYTES=1024 * 1024)
    repository = InMemoryKnowledgeRepository()
    store = _FileStore()
    with app.app_context():
        service = KnowledgeService(
            repository=repository,
            file_store=store,
            user_service=_SettingsService({"documentRetentionDays": 7}),
        )
        service.upload_files(
            "user-1",
            [FileStorage(stream=io.BytesIO(b"notes"), filename="notes.txt")],
        )
        uploaded = repository.list_files("user-1")[0]
        assert uploaded["retention_expires_at"] > int(time.time())

        expired = dict(uploaded)
        expired["file_id"] = "expired"
        expired["item_id"] = "file#expired"
        expired["object_key"] = "knowledge/expired/notes.txt"
        expired["retention_expires_at"] = int(time.time()) - 1
        repository.create_file(expired)
        store.put(expired["object_key"], b"expired", "text/plain")

        library = service.list_library("user-1")

    assert [item["file_id"] for item in library["files"]] == [uploaded["file_id"]]
    assert repository.get_file("user-1", "expired") is None


class _ShareRepository:
    def __init__(self) -> None:
        self.created: dict | None = None

    def create(self, item: dict) -> None:
        self.created = dict(item)


class _OwnedTranscriptRepository:
    def get_owned(self, user_id: str, meeting_id: str, timestamp: str) -> dict:
        return {
            "meeting_id": meeting_id,
            "timestamp": timestamp,
            "meeting_name": "Planning",
            "summary": "Summary",
        }


def test_share_defaults_are_applied_and_required_password_is_enforced(app) -> None:
    app.config["TESTING"] = False
    repository = _ShareRepository()
    service = MeetingShareService(
        repository=repository,
        transcript_repository=_OwnedTranscriptRepository(),
        user_service=_SettingsService(
            {
                "shareDefaultExpirationDays": 7,
                "shareRequirePassword": True,
                "shareAllowDownload": True,
                "shareIncludeScorecard": True,
            }
        ),
    )
    with app.test_request_context():
        with pytest.raises(ValidationError, match="password is required"):
            service.create("user-1", "meeting-1", "2026-07-18T10:00:00Z", {})
        result = service.create(
            "user-1",
            "meeting-1",
            "2026-07-18T10:00:00Z",
            {"password": "secret"},
        )

    assert result["include_scorecard"] is True
    assert result["allow_download"] is True
    assert repository.created["expires_at"]
    assert repository.created["password_hash"]
