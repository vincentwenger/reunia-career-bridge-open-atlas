from __future__ import annotations

import struct
from urllib.parse import parse_qs, urlparse

from meeting_assistant.repositories.meeting_share_repository import MeetingShareRepository
from meeting_assistant.repositories.transcript_repository import TranscriptRepository
from meeting_assistant.services.user_service import UserService


def _login(client, user_id="user-1"):
    with client.session_transaction() as session:
        session["user_id"] = user_id


def _meeting(summary="Original summary", transcript="Private transcript"):
    return {
        "meeting_id": "meeting-1",
        "user_id": "user-1",
        "timestamp": "2026-07-16T18:00:00+00:00",
        "meeting_name": "Planning review",
        "summary": summary,
        "key_wins": ["Aligned on launch scope"],
        "improvement_areas": ["Clarify ownership earlier"],
        "action_items": [{"task": "Prepare launch brief", "owner": "Vincent"}],
        "open_questions": ["Who approves the final budget?"],
        "final_grade": 88,
        "content_average_score": 91,
        "form_average_score": 85,
        "content_grades": [
            {
                "question": "What is the launch date?",
                "relevance_analysis": "The answer was direct and complete.",
                "grade": "A",
            }
        ],
        "form_metrics": {"overall_assessment": "Clear and well paced."},
        "transcript": transcript,
    }


def _clear_share_store(app):
    with app.app_context():
        MeetingShareRepository().clear_memory()


def test_meeting_review_exposes_share_controls(app):
    client = app.test_client()
    _login(client)
    response = client.get("/meeting-review.html")
    assert response.status_code == 200
    assert b'id="meeting-share-trigger"' in response.data
    assert b"Create share link" in response.data
    assert b"Full transcript" in response.data
    assert b"After 30 days" in response.data


def test_create_public_summary_snapshot_without_login(app, monkeypatch):
    _clear_share_store(app)
    monkeypatch.setattr(TranscriptRepository, "get_owned", lambda *args: _meeting())
    client = app.test_client()
    _login(client)

    response = client.post(
        "/api/meetings/meeting-1/shares",
        json={
            "timestamp": "2026-07-16T18:00:00+00:00",
            "expires_in_days": 30,
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    public_url = urlparse(payload["public_url"])
    assert public_url.path.endswith(payload["share_id"])
    assert parse_qs(public_url.query) == {"lang": ["en"]}
    assert payload["include_scorecard"] is False
    assert payload["include_transcript"] is False

    public = app.test_client().get(
        f"/shared/meeting/{payload['share_id']}",
        base_url="http://meetings.example.test",
        headers={"X-Forwarded-Proto": "https"},
    )
    assert public.status_code == 200
    assert b"Original summary" in public.data
    assert b"Aligned on launch scope" in public.data
    assert b"Private transcript" not in public.data
    assert b"Securely shared through" in public.data
    assert b"icons/favicon-64.png" in public.data
    assert b"Turn meetings into clear summaries and action items" in public.data
    assert "Explore Réunia".encode("utf-8") in public.data
    assert public.data.count("Powered by Réunia".encode("utf-8")) == 1
    assert b"utm_source=shared_meeting" in public.data
    assert b"utm_medium=public_link" in public.data
    assert b"utm_campaign=meeting_share" in public.data
    assert b"No account is required to view this shared meeting." in public.data
    assert b'<meta property="og:type" content="website">' in public.data
    assert '<meta property="og:site_name" content="Réunia">'.encode("utf-8") in public.data
    assert 'Planning review — Shared meeting'.encode() in public.data
    assert b'<meta name="twitter:card" content="summary_large_image">' in public.data
    assert b'https://meetings.example.test/shared/meeting/' in public.data
    assert b'https://meetings.example.test/static/images/shared-meeting-preview.png' in public.data
    assert 'View a read-only meeting summary securely shared through Réunia.'.encode("utf-8") in public.data
    assert public.headers["X-Robots-Tag"].startswith("noindex")
    assert public.headers["Cache-Control"].startswith("private, no-store")


def test_french_share_preserves_language_across_public_page_and_download(app, monkeypatch):
    _clear_share_store(app)
    french_meeting = _meeting(
        summary="Résumé original",
        transcript="Transcription privée",
    )
    french_meeting["key_wins"] = ["Portée du lancement confirmée"]
    monkeypatch.setattr(TranscriptRepository, "get_owned", lambda *args: french_meeting)
    client = app.test_client()
    _login(client)

    response = client.post(
        "/api/meetings/meeting-1/shares",
        json={
            "timestamp": "2026-07-16T18:00:00+00:00",
            "language": "fr",
            "include_transcript": True,
            "allow_download": True,
            "expires_in_days": 30,
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["language"] == "fr"
    assert parse_qs(urlparse(payload["public_url"]).query) == {"lang": ["fr"]}

    public = app.test_client().get(
        f"/shared/meeting/{payload['share_id']}?lang=fr",
        base_url="https://meetings.example.test",
    )
    assert public.status_code == 200
    assert b'<html lang="fr">' in public.data
    assert b'data-app-language="fr"' in public.data
    assert b"js/i18n.js" in public.data
    assert "Résumé original".encode("utf-8") in public.data
    assert "Planning review — Réunion partagée".encode("utf-8") in public.data
    assert "Consultez en toute sécurité".encode("utf-8") in public.data
    assert b"lang=fr" in public.data

    download = app.test_client().get(
        f"/shared/meeting/{payload['share_id']}/download?lang=fr"
    )
    assert download.status_code == 200
    assert "RÉSUMÉ".encode("utf-8") in download.data
    assert "POINTS FORTS".encode("utf-8") in download.data
    assert "TRANSCRIPTION".encode("utf-8") in download.data
    assert "Transcription privée".encode("utf-8") in download.data


def test_legacy_share_uses_current_owner_language(app, monkeypatch):
    _clear_share_store(app)
    monkeypatch.setattr(TranscriptRepository, "get_owned", lambda *args: _meeting())
    monkeypatch.setattr(
        UserService,
        "get_settings",
        lambda self, user_id: {"language": "fr"},
    )
    client = app.test_client()
    _login(client)
    created = client.post(
        "/api/meetings/meeting-1/shares",
        json={
            "timestamp": "2026-07-16T18:00:00+00:00",
            "language": "en",
        },
    ).get_json()

    with app.app_context():
        MeetingShareRepository().update_owned(
            "user-1",
            created["share_id"],
            {"language": ""},
        )

    public = app.test_client().get(f"/shared/meeting/{created['share_id']}")
    assert public.status_code == 200
    assert b'<html lang="fr">' in public.data
    assert b'data-app-language="fr"' in public.data

    listed = client.get(
        "/api/meetings/meeting-1/shares",
        query_string={"timestamp": "2026-07-16T18:00:00+00:00"},
    ).get_json()[0]
    assert listed["language"] == "fr"
    assert parse_qs(urlparse(listed["public_url"]).query) == {"lang": ["fr"]}


def test_optional_scorecard_transcript_password_and_download(app, monkeypatch):
    _clear_share_store(app)
    monkeypatch.setattr(TranscriptRepository, "get_owned", lambda *args: _meeting())
    client = app.test_client()
    _login(client)
    response = client.post(
        "/api/meetings/meeting-1/shares",
        json={
            "timestamp": "2026-07-16T18:00:00+00:00",
            "include_scorecard": True,
            "include_transcript": True,
            "allow_download": True,
            "password": "secret-pass",
            "expires_in_days": 30,
        },
    )
    share_id = response.get_json()["share_id"]

    public_client = app.test_client()
    protected = public_client.get(f"/shared/meeting/{share_id}")
    assert protected.status_code == 200
    assert b"password protected" in protected.data
    assert b"Original summary" not in protected.data
    assert b"Planning review" not in protected.data
    assert "Protected meeting shared through Réunia".encode("utf-8") in protected.data
    assert b"shared-meeting-preview.png" in protected.data

    wrong = public_client.post(
        f"/shared/meeting/{share_id}/unlock",
        data={"password": "wrong"},
    )
    assert wrong.status_code == 401
    assert b"incorrect" in wrong.data

    unlocked = public_client.post(
        f"/shared/meeting/{share_id}/unlock",
        data={"password": "secret-pass"},
    )
    assert unlocked.status_code == 200
    assert b"Scorecard" in unlocked.data
    assert b"Private transcript" in unlocked.data
    assert b"Download" in unlocked.data

    download = public_client.get(f"/shared/meeting/{share_id}/download")
    assert download.status_code == 200
    assert b"TRANSCRIPT" in download.data
    assert b"Private transcript" in download.data


def test_share_snapshot_can_be_refreshed_and_revoked(app, monkeypatch):
    _clear_share_store(app)
    state = {"meeting": _meeting(summary="Snapshot version one")}
    monkeypatch.setattr(
        TranscriptRepository,
        "get_owned",
        lambda *args: state["meeting"],
    )
    client = app.test_client()
    _login(client)
    created = client.post(
        "/api/meetings/meeting-1/shares",
        json={"timestamp": "2026-07-16T18:00:00+00:00"},
    ).get_json()
    share_id = created["share_id"]

    state["meeting"] = _meeting(summary="Snapshot version two")
    public_before = app.test_client().get(f"/shared/meeting/{share_id}")
    assert b"Snapshot version one" in public_before.data
    assert b"Snapshot version two" not in public_before.data

    refreshed = client.patch(
        f"/api/meeting-shares/{share_id}",
        json={"refresh_snapshot": True},
    )
    assert refreshed.status_code == 200
    public_after = app.test_client().get(f"/shared/meeting/{share_id}")
    assert b"Snapshot version two" in public_after.data

    revoked = client.delete(f"/api/meeting-shares/{share_id}")
    assert revoked.status_code == 200
    unavailable = app.test_client().get(f"/shared/meeting/{share_id}")
    assert unavailable.status_code == 404
    assert "Shared meeting unavailable | Réunia".encode("utf-8") in unavailable.data
    assert b"shared-meeting-preview.png" in unavailable.data


def test_owner_can_list_change_expiration_and_view_access_audit(app, monkeypatch):
    _clear_share_store(app)
    monkeypatch.setattr(TranscriptRepository, "get_owned", lambda *args: _meeting())
    client = app.test_client()
    _login(client)
    created = client.post(
        "/api/meetings/meeting-1/shares",
        json={"timestamp": "2026-07-16T18:00:00+00:00"},
    ).get_json()
    share_id = created["share_id"]

    app.test_client().get(f"/shared/meeting/{share_id}")
    listed = client.get(
        "/api/meetings/meeting-1/shares",
        query_string={"timestamp": "2026-07-16T18:00:00+00:00"},
    )
    assert listed.status_code == 200
    record = listed.get_json()[0]
    assert record["access_count"] == 1
    assert record["last_accessed_at"]

    updated = client.patch(
        f"/api/meeting-shares/{share_id}",
        json={"expires_in_days": "never"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["expires_at"] == ""
    assert updated.get_json()["is_expired"] is False


def test_shared_meeting_preview_asset_is_social_card_size(app):
    response = app.test_client().get("/static/images/shared-meeting-preview.png")
    assert response.status_code == 200
    assert response.content_type == "image/png"
    assert response.data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", response.data[16:24])
    assert (width, height) == (1200, 630)


def test_share_management_is_owner_scoped(app, monkeypatch):
    _clear_share_store(app)
    monkeypatch.setattr(TranscriptRepository, "get_owned", lambda *args: _meeting())
    owner = app.test_client()
    _login(owner, "user-1")
    share_id = owner.post(
        "/api/meetings/meeting-1/shares",
        json={"timestamp": "2026-07-16T18:00:00+00:00"},
    ).get_json()["share_id"]

    other_user = app.test_client()
    _login(other_user, "user-2")
    response = other_user.delete(f"/api/meeting-shares/{share_id}")
    assert response.status_code == 404

    public = app.test_client().get(f"/shared/meeting/{share_id}")
    assert public.status_code == 200
