from __future__ import annotations

import csv
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from werkzeug.security import generate_password_hash

from meeting_assistant.repositories.transcript_repository import TranscriptRepository
from meeting_assistant.services.authentication_service import AuthenticationService
from meeting_assistant.services.browser_recorder_job_service import BrowserRecorderJobService
from meeting_assistant.services.live_qa_service import LiveQAService
from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.utils.exceptions import (
    AuthenticationError,
    DatabaseError,
    ExternalServiceError,
    ResourceNotFoundError,
    ValidationError,
)


def _client_error(code: str, operation: str = "TestOperation") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


def _login(client, user_id: str = "member@example.com", *, is_admin: bool = False) -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
        flask_session["email"] = user_id
        flask_session["is_admin"] = is_admin


# ---------------------------------------------------------------------------
# Transcript ownership and failure handling
# ---------------------------------------------------------------------------


def test_transcript_patch_uses_authenticated_owner(app, monkeypatch):
    from meeting_assistant.blueprints.transcripts import routes as transcript_routes

    calls = {}

    class _Service:
        def update(self, user_id, meeting_id, timestamp, data):
            calls.update(
                user_id=user_id,
                meeting_id=meeting_id,
                timestamp=timestamp,
                data=data,
            )
            return {"message": "updated"}

    monkeypatch.setattr(transcript_routes, "TranscriptService", _Service)
    client = app.test_client()
    _login(client, "owner@example.com")

    response = client.patch(
        "/api/transcripts/meeting-123?timestamp=2026-07-18T10:00:00Z",
        json={"meeting_name": "Updated meeting"},
    )

    assert response.status_code == 200
    assert calls == {
        "user_id": "owner@example.com",
        "meeting_id": "meeting-123",
        "timestamp": "2026-07-18T10:00:00Z",
        "data": {"meeting_name": "Updated meeting"},
    }


def test_transcript_delete_uses_authenticated_owner(app, monkeypatch):
    from meeting_assistant.blueprints.transcripts import routes as transcript_routes

    calls = {}

    class _Service:
        def delete(self, user_id, meeting_id, timestamp):
            calls.update(user_id=user_id, meeting_id=meeting_id, timestamp=timestamp)
            return {"message": "deleted"}

    monkeypatch.setattr(transcript_routes, "TranscriptService", _Service)
    client = app.test_client()
    _login(client, "owner@example.com")

    response = client.delete(
        "/api/transcripts/meeting-123?timestamp=2026-07-18T10:00:00Z"
    )

    assert response.status_code == 200
    assert calls == {
        "user_id": "owner@example.com",
        "meeting_id": "meeting-123",
        "timestamp": "2026-07-18T10:00:00Z",
    }


def test_transcript_update_rejects_non_owner(monkeypatch):
    class _Table:
        def update_item(self, **kwargs):
            assert kwargs["ExpressionAttributeValues"][":owner"] == "attacker@example.com"
            raise _client_error("ConditionalCheckFailedException", "UpdateItem")

    repository = TranscriptRepository()
    monkeypatch.setattr(repository, "_table", lambda: _Table())

    with pytest.raises(ResourceNotFoundError, match="Meeting not found"):
        repository.update_owned(
            "attacker@example.com",
            "meeting-123",
            "2026-07-18T10:00:00Z",
            {"meeting_name": "Stolen title"},
        )


def test_transcript_delete_rejects_non_owner(monkeypatch):
    class _Table:
        def delete_item(self, **kwargs):
            assert kwargs["ExpressionAttributeValues"][":owner"] == "attacker@example.com"
            raise _client_error("ConditionalCheckFailedException", "DeleteItem")

    repository = TranscriptRepository()
    monkeypatch.setattr(repository, "_table", lambda: _Table())

    with pytest.raises(ResourceNotFoundError, match="Meeting not found"):
        repository.delete_owned(
            "attacker@example.com",
            "meeting-123",
            "2026-07-18T10:00:00Z",
        )


def test_duplicate_transcript_is_reported_as_validation_error(app):
    class _Repository:
        def create(self, item):
            raise _client_error("ConditionalCheckFailedException", "PutItem")

    class _Analysis:
        def analyze(self, **kwargs):
            return {"meeting_name": "Generated title"}

    class _Users:
        def get_settings(self, user_id):
            return {"aiModel": "gpt-test"}

    with app.app_context():
        service = TranscriptService(
            repository=_Repository(),
            analysis_service=_Analysis(),
            user_service=_Users(),
        )
        with pytest.raises(ValidationError, match="already exists"):
            service.create(
                "owner@example.com",
                {
                    "meeting_id": "duplicate-meeting",
                    "timestamp": "2026-07-18T10:00:00Z",
                    "transcript": "A valid transcript.",
                },
            )


def test_transcript_list_database_failure_is_wrapped(app):
    class _Repository:
        def list_for_user(self, user_id):
            raise _client_error("ProvisionedThroughputExceededException", "Query")

    with app.app_context():
        service = TranscriptService(repository=_Repository())
        with pytest.raises(DatabaseError, match="retrieve transcripts"):
            service.list_for_user("owner@example.com")


def test_invalid_api_token_cannot_access_transcripts(app):
    response = app.test_client().get(
        "/api/transcripts",
        headers={"Authorization": "Bearer altered-or-expired-token"},
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication required."}


# ---------------------------------------------------------------------------
# Authentication and session safety
# ---------------------------------------------------------------------------


def test_authentication_database_failure_is_wrapped():
    class _Repository:
        def get_by_id(self, user_id):
            raise _client_error("AccessDeniedException", "GetItem")

    with pytest.raises(DatabaseError, match="database communication"):
        AuthenticationService(repository=_Repository()).authenticate(
            "member@example.com", "correct-password"
        )


def test_authentication_rejects_incorrect_password():
    class _Repository:
        def get_by_id(self, user_id):
            return {
                "user_id": user_id,
                "password_hash": generate_password_hash("correct-password"),
            }

    with pytest.raises(AuthenticationError, match="Invalid user_id"):
        AuthenticationService(repository=_Repository()).authenticate(
            "member@example.com", "wrong-password"
        )


def test_login_clears_stale_session_values(app, monkeypatch):
    from meeting_assistant.blueprints.auth import routes as auth_routes

    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "authenticate",
        lambda self, email, password: {
            "user_id": "new@example.com",
            "email": "new@example.com",
            "full_name": "New User",
            "settings": {"language": "fr"},
        },
    )

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "old@example.com"
        flask_session["stale_secret"] = "must-disappear"

    response = client.post(
        "/api/login",
        data={"email": "new@example.com", "password": "correct-password"},
    )

    assert response.status_code == 302
    with client.session_transaction() as flask_session:
        assert flask_session["user_id"] == "new@example.com"
        assert flask_session["language"] == "fr"
        assert "stale_secret" not in flask_session


def test_logout_clears_entire_session(app):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(
            {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "is_admin": True,
                "language": "fr",
            }
        )

    response = client.post("/logout")

    assert response.status_code == 302
    with client.session_transaction() as flask_session:
        assert dict(flask_session) == {}


def test_signup_succeeds_when_registration_analytics_fails(app, monkeypatch):
    from meeting_assistant.blueprints.auth import routes as auth_routes

    registered = {}

    def _register(self, full_name, email, password, language=None):
        registered["language"] = language
        return {
            "user_id": email,
            "email": email,
            "full_name": full_name,
        }

    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "register",
        _register,
    )

    def _fail_analytics(*args, **kwargs):
        raise RuntimeError("analytics unavailable")

    monkeypatch.setattr(
        auth_routes.UsageMetricsService,
        "record_product_event",
        _fail_analytics,
    )

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["language"] = "fr"
    response = client.post(
        "/api/signup",
        data={
            "full_name": "Member User",
            "email": "member@example.com",
            "password": "long-enough-password",
        },
    )

    assert response.status_code == 302
    assert registered["language"] == "fr"
    with client.session_transaction() as flask_session:
        assert flask_session["user_id"] == "member@example.com"
        assert flask_session["language"] == "fr"


def test_desktop_login_succeeds_when_usage_analytics_fails(app, monkeypatch):
    from meeting_assistant.blueprints.auth import routes as auth_routes

    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "authenticate",
        lambda self, user_id, password: {
            "user_id": user_id,
            "email": user_id,
            "full_name": "Desktop User",
        },
    )
    monkeypatch.setattr(
        auth_routes.UserService,
        "get_settings",
        lambda self, user_id: {"language": "en"},
    )
    monkeypatch.setattr(
        auth_routes.UsageMetricsService,
        "record_desktop_client_use",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("analytics down")),
    )

    response = app.test_client().post(
        "/api/user",
        json={"user_id": "desktop@example.com", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert response.get_json()["api_token"]


# ---------------------------------------------------------------------------
# Live Q&A isolation and failure recovery
# ---------------------------------------------------------------------------


def test_live_qa_submission_requires_authentication(app):
    response = app.test_client().post(
        "/submit-live-qa",
        json={"origin": "raw_text", "file_content": "What is the status?"},
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication required."}


def test_live_qa_repository_isolates_users(app):
    with app.app_context():
        repository = app.extensions["live_qa_repository"]
        repository.create(
            {
                "id": "entry-owner",
                "user_id": "owner@example.com",
                "content": "Owner question",
                "chatgpt_answer": "Owner answer",
            },
            3600,
        )
        repository.create(
            {
                "id": "entry-other",
                "user_id": "other@example.com",
                "content": "Other question",
                "chatgpt_answer": "Other answer",
            },
            3600,
        )

        entries = LiveQAService().list_entries("owner@example.com", 3)

    assert [entry["id"] for entry in entries] == ["entry-owner"]


def test_live_qa_openai_failure_is_persisted_and_streamed(app):
    class _Completions:
        def create(self, **kwargs):
            raise RuntimeError("upstream timeout")

    with app.app_context():
        repository = app.extensions["live_qa_repository"]
        repository.create(
            {
                "id": "entry-failure",
                "user_id": "member@example.com",
                "content": "Question",
                "chatgpt_answer": "Thinking...",
            },
            3600,
        )
        service = LiveQAService()
        service._client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions())
        )

        chunks = list(
            service._generate_stream(
                user_id="member@example.com",
                entry_id="entry-failure",
                model="gpt-test",
                prompt="Answer clearly.",
                content="Question",
                ttl_seconds=3600,
            )
        )
        stored = repository.list_for_user("member@example.com")[0]

    assert chunks == ["Error: upstream timeout"]
    assert stored["chatgpt_answer"] == "Error: upstream timeout"


def test_live_qa_analytics_failure_does_not_interrupt_answer(app, monkeypatch):
    from meeting_assistant.services import live_qa_service as live_qa_module

    class _Completions:
        def create(self, **kwargs):
            return [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))]
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=" world"))]
                ),
            ]

    def _fail(*args, **kwargs):
        raise RuntimeError("analytics unavailable")

    monkeypatch.setattr(
        live_qa_module.UsageMetricsService,
        "record_product_event",
        _fail,
    )
    monkeypatch.setattr(
        live_qa_module.UsageMetricsService,
        "record_live_qa_answer",
        _fail,
    )

    with app.app_context():
        repository = app.extensions["live_qa_repository"]
        repository.create(
            {
                "id": "entry-success",
                "user_id": "member@example.com",
                "content": "Question",
                "chatgpt_answer": "Thinking...",
            },
            3600,
        )
        service = LiveQAService()
        service._client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions())
        )

        answer = "".join(
            service._generate_stream(
                user_id="member@example.com",
                entry_id="entry-success",
                model="gpt-test",
                prompt="Answer clearly.",
                content="Question",
                ttl_seconds=3600,
            )
        )
        stored = repository.list_for_user("member@example.com")[0]

    assert answer == "Hello world"
    assert stored["chatgpt_answer"] == "Hello world"


def test_live_qa_sse_initial_payload_contains_only_current_user(app, monkeypatch):
    from meeting_assistant.blueprints.live_qa import routes as live_qa_routes

    monkeypatch.setattr(
        live_qa_routes.UserService,
        "get_settings",
        lambda self, user_id: {
            "retentionHours": 3,
            "liveQaAnswerUpdateFrequency": "balanced",
        },
    )

    with app.app_context():
        repository = app.extensions["live_qa_repository"]
        repository.create(
            {
                "id": "mine",
                "user_id": "member@example.com",
                "origin": "raw_text",
                "content": "My question",
                "chatgpt_answer": "My answer",
                "timestamp": "2026-07-18T10:00:00Z",
            },
            3600,
        )
        repository.create(
            {
                "id": "theirs",
                "user_id": "other@example.com",
                "origin": "raw_text",
                "content": "Other question",
                "chatgpt_answer": "Other answer",
                "timestamp": "2026-07-18T10:00:00Z",
            },
            3600,
        )

    client = app.test_client()
    _login(client)
    response = client.get("/stream-ui", buffered=False)
    try:
        first_event = next(response.response).decode("utf-8")
    finally:
        response.close()

    assert '"id": "mine"' in first_event
    assert '"id": "theirs"' not in first_event
    assert "Other question" not in first_event


# ---------------------------------------------------------------------------
# Browser recorder job recovery
# ---------------------------------------------------------------------------


def test_recorder_queue_without_audio_removes_partial_job(app, tmp_path):
    app.config["RECORDER_JOB_DIR"] = str(tmp_path)
    service = BrowserRecorderJobService()

    with app.app_context(), pytest.raises(ValidationError, match="No browser audio"):
        service.queue_meeting(
            user_id="member@example.com",
            started_at="2026-07-18T10:00:00Z",
            microphone_audio=None,
            speaker_audio=None,
            requested_reference_id="empty-audio-job-1234",
        )

    assert not (tmp_path / "empty-audio-job-1234").exists()


def test_recorder_processing_failure_marks_job_and_deletes_audio(app, tmp_path):
    class _FailingRecorder:
        def create_meeting_from_paths(self, **kwargs):
            kwargs["progress_callback"]("transcribing_microphone", "Transcribing.")
            raise ExternalServiceError("Transcription service unavailable.")

    app.config["RECORDER_JOB_DIR"] = str(tmp_path)
    service = BrowserRecorderJobService(_FailingRecorder())
    job_id = "failed-recorder-job-1234"
    audio_path = tmp_path / job_id / "microphone.webm"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")

    job = {
        "job_id": job_id,
        "reference_id": job_id,
        "user_id": "member@example.com",
        "status": "queued",
        "stage": "queued",
        "stage_message": "Queued",
        "created_at": "2026-07-18T10:00:00Z",
        "updated_at": "2026-07-18T10:00:00Z",
        "started_at": "2026-07-18T10:00:00Z",
        "sources": [
            {
                "source": "MICROPHONE",
                "path": str(audio_path),
                "filename": "microphone.webm",
                "mime_type": "audio/webm",
                "size_bytes": 5,
            }
        ],
        "events": [],
    }

    with app.app_context():
        service._write_job(job)
        service.process_job(job_id)
        result = service.get_job(job_id=job_id, user_id="member@example.com")

    assert result["status"] == "failed"
    assert result["error"] == "Transcription service unavailable."
    assert result["failure_status_code"] == 502
    assert result["events"][-1]["stage"] == "failed"
    assert not audio_path.exists()


def test_recorder_terminal_job_is_not_processed_again(app, tmp_path):
    class _Recorder:
        def __init__(self):
            self.calls = 0

        def create_meeting_from_paths(self, **kwargs):
            self.calls += 1
            raise AssertionError("A completed job must not be reprocessed")

    recorder = _Recorder()
    app.config["RECORDER_JOB_DIR"] = str(tmp_path)
    service = BrowserRecorderJobService(recorder)
    job_id = "complete-job-12345678"

    with app.app_context():
        service._write_job(
            {
                "job_id": job_id,
                "reference_id": job_id,
                "user_id": "member@example.com",
                "status": "complete",
                "stage": "complete",
                "sources": [],
                "events": [],
            }
        )
        service.process_job(job_id)

    assert recorder.calls == 0


def test_recorder_job_is_hidden_from_other_users(app, tmp_path):
    app.config["RECORDER_JOB_DIR"] = str(tmp_path)
    service = BrowserRecorderJobService()
    job_id = "private-job-12345678"

    with app.app_context():
        service._write_job(
            {
                "job_id": job_id,
                "reference_id": job_id,
                "user_id": "owner@example.com",
                "status": "complete",
                "stage": "complete",
                "sources": [],
                "events": [],
            }
        )

    client = app.test_client()
    _login(client, "other@example.com")
    response = client.get(f"/api/meeting-recorder/jobs/{job_id}")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Recorder job not found."}


def test_recorder_upload_unexpected_failure_has_structured_response(app, monkeypatch):
    from meeting_assistant.blueprints.recorder import routes as recorder_routes

    class _FailingJobs:
        def queue_meeting(self, **kwargs):
            raise RuntimeError("disk unavailable")

    monkeypatch.setattr(recorder_routes, "BrowserRecorderJobService", _FailingJobs)
    client = app.test_client()
    _login(client)

    response = client.post(
        "/api/meeting-recorder",
        data={"client_reference_id": "upload-failure-123456"},
        headers={"X-Recorder-Reference": "upload-failure-123456"},
    )

    assert response.status_code == 500
    assert response.get_json() == {
        "error": "An unexpected server error occurred while uploading the recording.",
        "reference_id": "upload-failure-123456",
        "stage": "uploading",
    }


# ---------------------------------------------------------------------------
# CSV spreadsheet safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dangerous_value",
    [
        "=HYPERLINK(\"https://example.invalid\")",
        "+SUM(1,1)",
        "-2+3",
        "@SUM(1,1)",
    ],
)
def test_admin_csv_escapes_spreadsheet_formula_values(
    app, monkeypatch, dangerous_value
):
    from meeting_assistant.blueprints.admin_analytics import routes as admin_routes

    monkeypatch.setattr(
        admin_routes.AdminAnalyticsService,
        "dashboard",
        lambda self, days: {
            "period_days": 30,
            "users": [
                {
                    "full_name": dangerous_value,
                    "email": dangerous_value,
                    "document_total_bytes": 0,
                }
            ],
        },
    )

    client = app.test_client()
    _login(client, "admin@example.com", is_admin=True)
    response = client.get("/api/admin/analytics/users.csv?days=30")

    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert rows[1][0] == "'" + dangerous_value
    assert rows[1][1] == "'" + dangerous_value


def test_admin_csv_preserves_unicode_commas_and_newlines(app, monkeypatch):
    from meeting_assistant.blueprints.admin_analytics import routes as admin_routes

    monkeypatch.setattr(
        admin_routes.AdminAnalyticsService,
        "dashboard",
        lambda self, days: {
            "period_days": 30,
            "users": [
                {
                    "full_name": "Zoë, Dupont\nDirection",
                    "email": "zoe@example.com",
                    "document_total_bytes": 2_621_440,
                }
            ],
        },
    )

    client = app.test_client()
    _login(client, "admin@example.com", is_admin=True)
    response = client.get("/api/admin/analytics/users.csv")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))

    assert rows[1][0] == "Zoë, Dupont\nDirection"
    storage_index = rows[0].index("Document storage (MB)")
    assert rows[1][storage_index] == "2.50"
