from __future__ import annotations

from io import BytesIO

import pytest
from werkzeug.datastructures import FileStorage

from meeting_assistant.services.browser_recorder_live_service import _extract_questions
from meeting_assistant.services.browser_recorder_service import BrowserRecorderService
from meeting_assistant.services.live_qa_service import LiveQAService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.exceptions import ValidationError


class _UserRepository:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.saved = None

    def get_by_id(self, user_id):
        return {"user_id": user_id, "settings": dict(self.settings)}

    def update_settings(self, user_id, settings):
        self.saved = dict(settings)
        self.settings = dict(settings)
        return {"settings": settings}


class _FakeTranscriptions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "language": kwargs.get("language"),
            "text": "Bonjour",
            "segments": [{"start": 0, "end": 1, "text": "Bonjour"}],
        }


class _FakeOpenAIClient:
    def __init__(self):
        self.audio = type("Audio", (), {"transcriptions": _FakeTranscriptions()})()


class _FakeTranscriptService:
    def create(self, user_id, data):
        return {"meeting_id": data["meeting_id"], "timestamp": data["timestamp"]}


def _upload():
    return FileStorage(
        stream=BytesIO(b"fake browser audio"),
        filename="microphone.webm",
        content_type="audio/webm",
    )


def test_user_language_defaults_and_persists(app):
    repository = _UserRepository()
    with app.app_context():
        service = UserService(repository=repository)
        assert service.get_settings("user-123")["language"] == "en"

        updated = service.update_settings("user-123", {"language": "fr"})

    assert updated["language"] == "fr"
    assert repository.saved["language"] == "fr"


def test_user_language_rejects_unsupported_value(app):
    repository = _UserRepository()
    with app.app_context(), pytest.raises(ValidationError):
        UserService(repository=repository).update_settings(
            "user-123",
            {"language": "de"},
        )


def test_browser_recorder_uses_selected_french_language(app):
    client = _FakeOpenAIClient()
    with app.app_context():
        BrowserRecorderService(
            transcript_service=_FakeTranscriptService(),
            client=client,
        ).create_meeting(
            user_id="user-123",
            started_at="2026-07-16T20:00:00Z",
            microphone_audio=_upload(),
            speaker_audio=None,
            language="fr",
        )

    assert client.audio.transcriptions.calls[0]["language"] == "fr"


def test_french_live_questions_are_detected_without_question_mark():
    questions, remainder = _extract_questions(
        "",
        "Pouvez-vous expliquer les prochaines étapes",
    )

    assert questions == ["Pouvez-vous expliquer les prochaines étapes?"]
    assert remainder == ""


def test_live_qa_prompt_requests_french_output():
    prompt = LiveQAService._build_prompt(
        "microphone",
        {"language": "fr"},
        None,
        {},
    )

    assert "Respond entirely in French" in prompt


def test_base_page_exposes_language_translation_layer(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["language"] = "fr"

    response = client.get("/login.html")

    assert response.status_code == 200
    assert b'<html lang="fr">' in response.data
    assert b'js/i18n.js' in response.data


def test_public_language_query_sets_french_session_and_page(app):
    client = app.test_client()

    response = client.get("/?lang=fr")

    assert response.status_code == 200
    assert b'<html lang="fr">' in response.data
    assert b'data-app-language="fr"' in response.data
    with client.session_transaction() as session:
        assert session["language"] == "fr"


def test_public_language_query_persists_without_repeating_parameter(app):
    client = app.test_client()

    client.get("/?lang=fr")
    response = client.get("/login.html")

    assert response.status_code == 200
    assert b'<html lang="fr">' in response.data
    assert b'data-app-language="fr"' in response.data


def test_public_language_query_can_switch_back_to_english(app):
    client = app.test_client()

    client.get("/?lang=fr")
    response = client.get("/login.html?lang=en")

    assert response.status_code == 200
    assert b'<html lang="en">' in response.data
    with client.session_transaction() as session:
        assert session["language"] == "en"


def test_invalid_public_language_query_is_ignored(app):
    client = app.test_client()

    response = client.get("/?lang=de")

    assert response.status_code == 200
    assert b'<html lang="en">' in response.data
    with client.session_transaction() as session:
        assert "language" not in session


def test_guest_language_switcher_appears_on_public_homepage(app):
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert response.data.count(b"data-guest-language-toggle") == 2
    assert b"data-guest-language-trigger" in response.data
    assert b"data-guest-language-menu" in response.data
    assert b'aria-expanded="false"' in response.data
    assert b'data-target-language="en"' in response.data
    assert b'data-target-language="fr"' in response.data
    assert b'class="guest-language-option active"' in response.data


def test_guest_language_switcher_reflects_french_public_session(app):
    client = app.test_client()

    response = client.get("/login.html?lang=fr")

    assert response.status_code == 200
    assert response.data.count(b"data-guest-language-toggle") == 2
    assert b"data-guest-language-trigger" in response.data
    assert b'data-target-language="fr"' in response.data
    assert b'class="guest-language-option active"' in response.data
    assert b'aria-label="Utiliser le fran' in response.data


def test_language_switcher_javascript_updates_url_parameter(app):
    client = app.test_client()

    response = client.get("/static/js/i18n.js")

    assert response.status_code == 200
    assert b"url.searchParams.set('lang', normalized)" in response.data
    assert b"[data-guest-language-dropdown]" in response.data
    assert b"[data-guest-language-trigger]" in response.data
    assert b"[data-guest-language-toggle]" in response.data


def test_translation_catalog_covers_newer_application_areas(app):
    client = app.test_client()

    response = client.get("/static/js/i18n.js")

    assert response.status_code == 200
    assert "Bibliothèque de documents".encode("utf-8") in response.data
    assert "Guide utilisateur".encode("utf-8") in response.data
    assert "Aide et assistance".encode("utf-8") in response.data
    assert "Analyses administrateur".encode("utf-8") in response.data
    assert "Centre d’actions".encode("utf-8") in response.data


def test_translation_layer_handles_dynamic_text_and_page_metadata(app):
    client = app.test_client()

    response = client.get("/static/js/i18n.js")

    assert response.status_code == 200
    assert b"frTemplates" in response.data
    assert b"frCaseInsensitive" in response.data
    assert b"document.documentElement" in response.data
    assert b"HTMLMetaElement" in response.data
    assert b"attributeFilter" in response.data
    assert b"locale" in response.data


def test_translation_layer_is_idempotent_for_observed_dynamic_text(app):
    response = app.test_client().get("/static/js/i18n.js")

    assert response.status_code == 200
    assert b"const normalizedValue = normalizeTranslationText(value);" in response.data
    assert b"lookupTranslation(normalizedValue) ?? normalizedValue" in response.data
    assert b"const nextValue = leading + translated + trailing;" in response.data
    assert b"if (nextValue === original) return;" in response.data
    assert b"node.nodeValue = nextValue;" in response.data
