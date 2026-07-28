from __future__ import annotations

from datetime import datetime, timezone

import pytest
from flask import Flask
from werkzeug.security import generate_password_hash

from meeting_assistant import _validate_production_configuration
from meeting_assistant.services.authentication_service import AuthenticationService
from meeting_assistant.utils.exceptions import ValidationError


class _UserRepository:
    def __init__(self, users=None):
        self.users = dict(users or {})
        self.created = None
        self.lookups = []

    def get_by_id(self, user_id):
        self.lookups.append(user_id)
        return self.users.get(user_id)

    def create(self, user):
        self.created = dict(user)
        self.users[user["user_id"]] = dict(user)


def _valid_production_config() -> dict[str, str]:
    values = {
        "SECRET_KEY": "s" * 48,
        "REDIS_URL": "redis://private-redis:6379/0",
        "KNOWLEDGE_FILES_BUCKET": "prod-documents",
        "RECORDER_JOBS_BUCKET": "prod-recorder-jobs",
        "RATE_LIMIT_STORAGE_BACKEND": "redis",
        "ADMIN_ANALYTICS_CACHE_BACKEND": "redis",
        "RECORDER_LIVE_STATE_BACKEND": "redis",
        "RECORDER_JOB_QUEUE_BACKEND": "redis",
        "RECORDER_JOB_STORAGE_BACKEND": "s3",
        "KNOWLEDGE_FILE_STORAGE_BACKEND": "s3",
        "ANALYTICS_STORAGE_BACKEND": "dynamodb",
        "LIVE_QA_STORAGE_BACKEND": "dynamodb",
        "ACTIONS_STORAGE_BACKEND": "dynamodb",
        "SUPPORT_STORAGE_BACKEND": "dynamodb",
        "KNOWLEDGE_STORAGE_BACKEND": "dynamodb",
        "MEETING_SHARES_STORAGE_BACKEND": "dynamodb",
    }
    for key in (
        "USERS_TABLE_NAME",
        "TRANSCRIPTS_TABLE_NAME",
        "ACTIONS_TABLE_NAME",
        "ANALYTICS_TABLE_NAME",
        "MEETING_SHARES_TABLE_NAME",
        "LIVE_QA_TABLE_NAME",
        "SUPPORT_REQUESTS_TABLE_NAME",
        "KNOWLEDGE_TABLE_NAME",
    ):
        values[key] = f"prod-{key.lower()}"
    return values


def test_production_configuration_rejects_missing_or_unsafe_values():
    app = Flask(__name__)
    app.config.update(_valid_production_config())
    _validate_production_configuration(app)

    app.config.update(_valid_production_config())
    app.config["SECRET_KEY"] = "short"
    with pytest.raises(RuntimeError, match="at least 32"):
        _validate_production_configuration(app)

    app.config.update(_valid_production_config())
    app.config["RECORDER_JOB_STORAGE_BACKEND"] = "local"
    with pytest.raises(RuntimeError, match="Unsafe production storage"):
        _validate_production_configuration(app)


def test_registration_normalizes_email_and_authentication_keeps_legacy_compatibility(app):
    repository = _UserRepository()
    with app.app_context():
        user = AuthenticationService(repository).register(
            "  Example User  ",
            "  Mixed.Case@Example.COM ",
            "correct horse battery staple",
            language="fr",
        )
    assert user["user_id"] == "mixed.case@example.com"
    assert user["email"] == "mixed.case@example.com"
    assert user["full_name"] == "Example User"
    assert user["settings"]["aiModel"] == app.config["AI_MODEL_PRESETS"]["fast"]
    assert user["settings"]["retentionHours"] == 1
    assert user["settings"]["liveQaAnswerUpdateFrequency"] == "efficient"
    assert user["settings"]["meetingSummaryDetail"] == "brief"
    assert user["settings"]["meetingRetentionDays"] == 7
    assert user["settings"]["documentRetentionDays"] == 7
    assert user["settings"]["aiSpeaker"] is False
    assert user["settings"]["aiMicrophone"] is False
    assert user["settings"]["scorecard_source"] == "all"
    assert user["settings"]["language"] == "fr"

    legacy = {
        "user_id": "Legacy@Example.com",
        "email": "Legacy@Example.com",
        "password_hash": generate_password_hash("password123"),
    }
    legacy_repository = _UserRepository({legacy["user_id"]: legacy})
    authenticated = AuthenticationService(legacy_repository).authenticate(
        "Legacy@Example.com", "password123"
    )
    assert authenticated["user_id"] == "Legacy@Example.com"
    assert legacy_repository.lookups == ["legacy@example.com", "Legacy@Example.com"]


def test_csrf_security_headers_and_post_only_logout(app):
    app.config["CSRF_ENABLED"] = True
    client = app.test_client()

    page = client.get("/login.html")
    assert page.status_code == 200
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    assert page.headers["X-Frame-Options"] == "DENY"

    missing_token = client.post(
        "/api/login",
        json={"email": "member@example.com", "password": "wrong"},
    )
    assert missing_token.status_code == 400
    assert "security token" in missing_token.get_json()["error"].lower()

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        csrf_token = flask_session["_csrf_token"]

    get_logout = client.get("/logout")
    assert get_logout.status_code == 405
    with client.session_transaction() as flask_session:
        assert flask_session["user_id"] == "member@example.com"

    post_logout = client.post("/logout", data={"csrf_token": csrf_token})
    assert post_logout.status_code == 302
    with client.session_transaction() as flask_session:
        assert dict(flask_session) == {}


def test_analytics_uses_server_identity_and_time(app):
    client = app.test_client()
    response = client.post(
        "/api/analytics/track",
        json={
            "visitor_id": "a" * 32,
            "session_id": "b" * 32,
            "activity_date": "2000-01-01",
            "page_path": "/app?forged=true",
            "active_seconds": 10,
            "page_view": True,
        },
    )
    assert response.status_code == 204
    assert "reunia_visitor=" in response.headers.get("Set-Cookie", "")

    events = app.extensions["analytics_repository"].list_activity()
    assert len(events) == 1
    event = events[0]
    assert event["visitor_id"] != "a" * 32
    assert event["session_id"] != "b" * 32
    assert event["activity_date"] == datetime.now(timezone.utc).date().isoformat()
    assert event["last_page"] == "/app"


def test_profile_update_refreshes_session_name(app, monkeypatch):
    from meeting_assistant.blueprints.users import routes as user_routes

    class _UserService:
        def update_profile(self, user_id, data):
            assert user_id == "member@example.com"
            assert data == {"full_name": "Updated Name"}
            return {"full_name": "Updated Name"}

    monkeypatch.setattr(user_routes, "UserService", _UserService)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["full_name"] = "Old Name"

    response = client.post("/update-profile", json={"full_name": "Updated Name"})
    assert response.status_code == 200
    with client.session_transaction() as flask_session:
        assert flask_session["full_name"] == "Updated Name"


def test_profile_validation_rejects_invalid_date(app):
    from meeting_assistant.services.user_service import UserService

    class _Repository:
        def update_fields(self, user_id, fields):
            raise AssertionError("Invalid profile data must not reach persistence")

    with app.app_context(), pytest.raises(ValidationError, match="Date of Birth"):
        UserService(repository=_Repository()).update_profile(
            "member@example.com", {"dob": "not-a-date"}
        )


def test_recorder_job_ids_are_deterministic_for_durable_retries():
    from meeting_assistant.services.browser_recorder_service import _build_meeting_id

    timestamp = "2026-07-17T12:00:00+00:00"
    first = _build_meeting_id(timestamp, reference_id="job-reference-123456")
    second = _build_meeting_id(timestamp, reference_id="job-reference-123456")
    different = _build_meeting_id(timestamp, reference_id="job-reference-654321")
    assert first == second
    assert first != different


def test_local_recorder_store_lists_recoverable_jobs(tmp_path):
    from meeting_assistant.repositories.recorder_job_store import LocalRecorderJobStore

    store = LocalRecorderJobStore(tmp_path)
    for job_id, status in (
        ("queued-job-12345678", "queued"),
        ("processing-job-1234", "processing"),
        ("complete-job-123456", "complete"),
    ):
        store.create(job_id)
        store.write({"job_id": job_id, "status": status})

    assert store.list_recoverable_jobs() == [
        "processing-job-1234",
        "queued-job-12345678",
    ]
