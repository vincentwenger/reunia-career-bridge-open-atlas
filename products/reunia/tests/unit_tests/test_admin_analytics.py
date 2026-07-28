from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from meeting_assistant.repositories.user_repository import UserRepository
from meeting_assistant.services.admin_analytics_service import (
    AdminAnalyticsService,
    UsageMetricsService,
)
from meeting_assistant.services.authentication_service import AuthenticationService
from meeting_assistant.services.user_service import UserService


def _activity_payload(visitor_id, session_id, *, seconds=0, page_view=True):
    return {
        "visitor_id": visitor_id,
        "session_id": session_id,
        "activity_date": datetime.now(timezone.utc).date().isoformat(),
        "page_path": "/app",
        "active_seconds": seconds,
        "page_view": page_view,
    }


def test_admin_analytics_routes_are_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules() if not rule.build_only}
    assert "/admin/analytics" in rules
    assert "/api/admin/analytics" in rules
    assert "/api/admin/analytics/users.csv" in rules
    assert "/api/admin/analytics/users/<path:user_id>/usage" in rules
    assert "/api/admin/analytics/incidents" in rules
    assert "/api/admin/analytics/repeated-failures" in rules
    assert "/api/analytics/track" in rules
    assert "/api/admin/support-requests" in rules
    assert "/api/admin/support-requests/<request_id>" in rules
    assert "/api/admin/support-requests/<request_id>/attachment" in rules
    assert "/download/desktop-client" in rules


def test_admin_page_requires_an_administrator(app):
    client = app.test_client()

    signed_out = client.get("/admin/analytics", follow_redirects=False)
    assert signed_out.status_code == 302
    assert signed_out.headers["Location"].endswith("/login.html")

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["is_admin"] = False

    forbidden = client.get("/admin/analytics")
    assert forbidden.status_code == 403
    assert b"Administrator Access Required" in forbidden.data


def test_admin_page_renders_for_admin_session(app):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/admin/analytics")
    assert response.status_code == 200
    assert b"Admin Analytics" in response.data
    assert b"Unique guest browsers" in response.data
    assert b"View country distribution" in response.data
    assert b"Guest distribution by country" in response.data
    assert b"User activity" in response.data
    assert b"Web active days" in response.data
    assert b"Support inbox" in response.data
    assert b"New support messages" in response.data
    assert b"Documents stored" in response.data
    assert b"Saved meetings" in response.data
    assert b"Average recording" in response.data
    assert b"Longest recording" in response.data
    assert b"Shortest recording" in response.data
    assert b"Live Q&amp;A answers" in response.data
    assert b"Desktop downloads" in response.data
    assert b"Desktop client uses" in response.data
    assert b'data-admin-tab="overview"' in response.data
    assert b'data-admin-tab="product"' in response.data
    assert b'data-admin-tab="operations"' in response.data
    assert b'data-admin-tab="users"' in response.data
    assert b'data-admin-tab="support"' in response.data
    assert b'id="admin-user-activation-filter"' in response.data
    assert b'id="admin-user-activity-filter"' in response.data
    assert b'<option value="7" selected>Last 7 days</option>' in response.data
    assert b'/api/admin/analytics/users.csv?days=7' in response.data
    assert b"Activity and outcomes" in response.data
    assert b"Incidents" in response.data
    assert b'id="admin-incidents-card"' in response.data
    assert b'data-admin-operations-tab="health"' in response.data


def test_admin_analytics_defaults_to_seven_days(app):
    service = AdminAnalyticsService()

    assert service._normalize_period(None) == 7
    assert service._normalize_period("") == 7
    assert service._normalize_period("unsupported") == 7
    assert b'data-admin-operations-tab="incidents"' in response.data
    assert b'id="admin-failure-modal"' not in response.data


def test_support_inbox_hidden_states_remain_hidden():
    css_path = Path(__file__).resolve().parents[2] / "static/css/pages/admin-analytics.css"
    css = css_path.read_text(encoding="utf-8")

    assert ".admin-support-card [hidden]" in css
    assert "display: none !important;" in css


def test_tracking_records_guest_and_registered_activity(app):
    client = app.test_client()
    guest = _activity_payload("a" * 32, "b" * 32, seconds=30)
    assert client.post("/api/analytics/track", json=guest).status_code == 204

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["is_admin"] = False

    registered = _activity_payload("a" * 32, "c" * 32, seconds=20)
    assert client.post("/api/analytics/track", json=registered).status_code == 204

    items = app.extensions["analytics_repository"].list_activity()
    assert len(items) == 2
    assert {item["identity_type"] for item in items} == {"guest", "registered"}
    assert sum(item["active_seconds"] for item in items) == 50


def test_admin_activity_is_excluded_by_default(app):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    payload = _activity_payload("d" * 32, "e" * 32, seconds=30)
    assert client.post("/api/analytics/track", json=payload).status_code == 204
    assert app.extensions["analytics_repository"].list_activity() == []


def test_admin_api_summarizes_users_and_active_time(app, monkeypatch):
    monkeypatch.setattr(
        UserRepository,
        "list_all",
        lambda self: [
            {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "full_name": "Member User",
            },
            {
                "user_id": "quiet@example.com",
                "email": "quiet@example.com",
                "full_name": "Quiet User",
            },
        ],
    )
    client = app.test_client()

    guest = _activity_payload("f" * 32, "g" * 32, seconds=10)
    client.post("/api/analytics/track", json=guest)

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["is_admin"] = False
    registered = _activity_payload("f" * 32, "h" * 32, seconds=45)
    client.post("/api/analytics/track", json=registered)

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/api/admin/analytics?days=30")
    assert response.status_code == 200
    data = response.get_json()
    assert data["summary"]["unique_guests"] == 1
    assert data["summary"]["registered_users"] == 2
    assert data["summary"]["active_registered_users"] == 1
    assert data["summary"]["registered_active_seconds"] == 45
    assert data["comparisons"]["unique_guests"]["current"] == 1
    assert data["comparisons"]["active_registered_users"]["current"] == 1
    assert data["comparisons"]["registered_active_seconds"]["current"] == 45
    assert data["users"][0]["email"] == "member@example.com"
    assert data["users"][0]["lifetime_active_seconds"] == 45
    assert data["users"][0]["recording_duration_sample_count"] == 0
    assert data["users"][0]["average_recording_duration_seconds"] is None
    assert data["users"][0]["maximum_recording_duration_seconds"] is None
    assert data["users"][0]["minimum_recording_duration_seconds"] is None


def test_guest_country_distribution_uses_only_configured_proxy_header(app):
    app.config["ANALYTICS_GEO_COUNTRY_HEADER"] = "X-Visitor-Country"

    for country_code in ("US", "US", "CA", None):
        guest_client = app.test_client()
        headers = {"X-Visitor-Country": country_code} if country_code else {}
        response = guest_client.post(
            "/api/analytics/track",
            json=_activity_payload("ignored", "ignored", seconds=5),
            headers=headers,
        )
        assert response.status_code == 204

    registered_client = app.test_client()
    with registered_client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["is_admin"] = False
    assert registered_client.post(
        "/api/analytics/track",
        json=_activity_payload("ignored", "ignored", seconds=5),
        headers={"X-Visitor-Country": "FR"},
    ).status_code == 204

    admin_client = app.test_client()
    with admin_client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = admin_client.get("/api/admin/analytics?days=30")
    assert response.status_code == 200
    geography = response.get_json()["guest_geography"]
    assert geography == {
        "tracking_configured": True,
        "total_guests": 4,
        "located_guests": 3,
        "unknown_guests": 1,
        "coverage_percentage": 75.0,
        "countries": [
            {"country_code": "US", "guest_count": 2, "percentage": 66.7},
            {"country_code": "CA", "guest_count": 1, "percentage": 33.3},
        ],
    }

    activity = app.extensions["analytics_repository"].list_activity()
    assert {item.get("country_code") for item in activity} == {"US", "CA", None}
    assert all("city" not in item and "ip_address" not in item for item in activity)
    registered = next(item for item in activity if item.get("user_id") == "member@example.com")
    assert "country_code" not in registered


def test_guest_country_header_is_ignored_until_explicitly_configured(app):
    client = app.test_client()
    response = client.post(
        "/api/analytics/track",
        json=_activity_payload("ignored", "ignored", seconds=5),
        headers={"X-Visitor-Country": "US"},
    )
    assert response.status_code == 204
    activity = app.extensions["analytics_repository"].list_activity()
    assert len(activity) == 1
    assert "country_code" not in activity[0]


def test_admin_support_inbox_requires_an_administrator(app):
    client = app.test_client()
    response = client.get("/api/admin/support-requests")
    assert response.status_code == 401

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["is_admin"] = False

    response = client.get("/api/admin/support-requests")
    assert response.status_code == 403


def test_admin_can_read_and_resolve_support_messages(app):
    client = app.test_client()
    submitted = client.post(
        "/api/support",
        data={
            "name": "Support User",
            "email": "support-user@example.com",
            "topic": "technical",
            "area": "meeting-review",
            "subject": "Meeting review did not open",
            "message": "I selected my meeting but the review page stayed blank.",
            "page_url": "https://reunia.app/meeting-review.html",
        },
    )
    assert submitted.status_code == 201
    request_id = submitted.get_json()["request_id"]

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    inbox = client.get("/api/admin/support-requests")
    assert inbox.status_code == 200
    inbox_data = inbox.get_json()
    assert inbox_data["summary"] == {
        "total": 1,
        "new": 1,
        "read": 0,
        "resolved": 0,
    }
    assert inbox_data["requests"][0]["subject"] == "Meeting review did not open"
    assert "message" not in inbox_data["requests"][0]

    detail = client.get(f"/api/admin/support-requests/{request_id}")
    assert detail.status_code == 200
    detail_data = detail.get_json()["request"]
    assert detail_data["status"] == "read"
    assert detail_data["message"].startswith("I selected my meeting")
    assert detail_data["email"] == "support-user@example.com"

    resolved = client.patch(
        f"/api/admin/support-requests/{request_id}",
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200
    assert resolved.get_json()["request"]["status"] == "resolved"

    refreshed = client.get("/api/admin/support-requests").get_json()
    assert refreshed["summary"]["new"] == 0
    assert refreshed["summary"]["resolved"] == 1


def test_admin_support_status_rejects_invalid_values(app):
    repository = app.extensions["support_repository"]
    repository.create({
        "request_id": "SUP-20260717-ABCDEF12",
        "created_at": "2026-07-17T10:00:00+00:00",
        "status": "new",
        "name": "Test User",
        "email": "test@example.com",
        "subject": "Question",
        "message": "Please help.",
    })
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.patch(
        "/api/admin/support-requests/SUP-20260717-ABCDEF12",
        json={"status": "deleted"},
    )
    assert response.status_code == 400
    assert "new, read, or resolved" in response.get_json()["error"]


class _FakeTranscriptUsageRepository:
    def __init__(self, meetings):
        self.meetings = list(meetings)

    def list_all_summaries(self):
        return list(self.meetings)

    def list_summaries_for_user(self, user_id):
        return [item for item in self.meetings if item.get("user_id") == user_id]


def test_last_active_uses_durable_product_activity_and_matches_user_ids_case_insensitively(
    app,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        UserRepository,
        "list_all",
        lambda self: [
            {
                "user_id": "Member@Example.com",
                "email": "Member@Example.com",
                "full_name": "Member User",
            }
        ],
    )

    with app.app_context():
        assert UsageMetricsService().record_desktop_client_use(
            "member@example.com",
            event_id="recent-desktop-session",
            occurred_at=now.isoformat(),
        ) is True

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/api/admin/analytics?days=30")
    assert response.status_code == 200
    data = response.get_json()
    assert data["summary"]["active_registered_users"] == 1
    assert data["users"][0]["period_has_activity"] is True
    assert data["users"][0]["desktop_use_count"] == 1
    assert abs(data["users"][0]["last_active"] - int(now.timestamp())) <= 1
    assert data["users"][0]["active_day_count"] == 0
    assert data["users"][0]["period_active_seconds"] == 0


def test_last_active_falls_back_to_saved_meeting_timestamp_without_browser_heartbeat(
    app,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        UserRepository,
        "list_all",
        lambda self: [
            {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "full_name": "Member User",
            }
        ],
    )
    app.extensions["admin_transcript_repository"] = _FakeTranscriptUsageRepository([
        {
            "user_id": "MEMBER@example.com",
            "meeting_name": "Recent meeting",
            "timestamp": now.isoformat(),
        }
    ])

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/api/admin/analytics?days=30")
    assert response.status_code == 200
    data = response.get_json()
    assert data["summary"]["active_registered_users"] == 1
    assert data["users"][0]["period_has_activity"] is True
    assert data["users"][0]["saved_meeting_count"] == 1
    assert abs(data["users"][0]["last_active"] - int(now.timestamp())) <= 1


def test_admin_api_includes_per_user_product_usage(app, monkeypatch):
    monkeypatch.setattr(
        UserRepository,
        "list_all",
        lambda self: [
            {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "full_name": "Member User",
            }
        ],
    )
    knowledge = app.extensions["knowledge_repository"]
    knowledge.create_file({
        "user_id": "member@example.com",
        "item_id": "file#doc-1",
        "entity_type": "file",
        "file_id": "doc-1",
        "filename": "proposal.pdf",
        "display_name": "proposal.pdf",
        "extension": "pdf",
        "collection_id": "uncategorized",
        "size_bytes": 1_250,
        "created_at": "2026-07-17T10:00:00+00:00",
    })
    knowledge.create_file({
        "user_id": "member@example.com",
        "item_id": "file#doc-2",
        "entity_type": "file",
        "file_id": "doc-2",
        "filename": "budget.xlsx",
        "display_name": "budget.xlsx",
        "extension": "xlsx",
        "collection_id": "uncategorized",
        "size_bytes": 2_750,
        "created_at": "2026-07-17T11:00:00+00:00",
    })
    app.extensions["admin_transcript_repository"] = _FakeTranscriptUsageRepository([
        {
            "user_id": "member@example.com",
            "meeting_id": "meeting-1",
            "meeting_name": "Project review",
            "timestamp": "2026-07-17T12:00:00+00:00",
        },
        {
            "user_id": "member@example.com",
            "meeting_id": "meeting-2",
            "prepared_meeting_title": "Client call",
            "timestamp": "2026-07-17T13:00:00+00:00",
        },
    ])

    with app.app_context():
        metrics = UsageMetricsService()
        assert metrics.record_live_qa_answer("member@example.com", "entry-1") is True
        assert metrics.record_live_qa_answer("member@example.com", "entry-1") is False
        assert metrics.record_live_qa_answer("member@example.com", "entry-2") is True
        assert metrics.record_desktop_client_download("member@example.com") is True
        assert metrics.record_desktop_client_download("member@example.com") is True
        assert metrics.record_desktop_client_use(
            "member@example.com",
            event_id="desktop-session-1",
        ) is True
        assert metrics.record_desktop_client_use(
            "member@example.com",
            event_id="desktop-session-1",
        ) is False
        assert metrics.record_desktop_client_use(
            "member@example.com",
            event_id="desktop-session-2",
        ) is True
        for index, duration_seconds in enumerate((60, 120, 300), start=1):
            assert metrics.record_product_event(
                "recording_completed",
                "member@example.com",
                event_id=f"completed-recording-{index}",
                metadata={"duration_seconds": duration_seconds},
            ) is True

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/api/admin/analytics?days=30")
    assert response.status_code == 200
    data = response.get_json()
    assert data["summary"]["document_count"] == 2
    assert data["summary"]["document_total_bytes"] == 4_000
    assert data["summary"]["saved_meeting_count"] == 2
    assert data["summary"]["live_qa_answer_count"] == 2
    assert data["summary"]["desktop_download_count"] == 2
    assert data["summary"]["desktop_use_count"] == 2
    user = data["users"][0]
    assert user["document_count"] == 2
    assert user["document_total_bytes"] == 4_000
    assert user["saved_meeting_count"] == 2
    assert user["recording_duration_sample_count"] == 3
    assert user["average_recording_duration_seconds"] == 160
    assert user["maximum_recording_duration_seconds"] == 300
    assert user["minimum_recording_duration_seconds"] == 60
    assert user["live_qa_answer_count"] == 2
    assert user["desktop_download_count"] == 2
    assert user["desktop_use_count"] == 2

    detail = client.get(
        "/api/admin/analytics/users/member%40example.com/usage"
    )
    assert detail.status_code == 200
    usage = detail.get_json()["usage"]
    assert usage["summary"] == {
        "document_count": 2,
        "document_total_bytes": 4_000,
        "saved_meeting_count": 2,
        "recording_duration_sample_count": 3,
        "average_recording_duration_seconds": 160,
        "maximum_recording_duration_seconds": 300,
        "minimum_recording_duration_seconds": 60,
        "live_qa_answer_count": 2,
        "desktop_download_count": 2,
        "desktop_use_count": 2,
    }
    assert [item["filename"] for item in usage["documents"]] == [
        "budget.xlsx",
        "proposal.pdf",
    ]
    assert [item["title"] for item in usage["meetings"]] == [
        "Client call",
        "Project review",
    ]


def test_successful_desktop_authentication_is_counted(app, monkeypatch):
    monkeypatch.setattr(
        AuthenticationService,
        "authenticate",
        lambda self, user_id, password: {
            "user_id": "member@example.com",
            "email": "member@example.com",
            "full_name": "Member User",
        },
    )
    monkeypatch.setattr(
        UserService,
        "get_settings",
        lambda self, user_id: {"language": "en"},
    )

    response = app.test_client().post(
        "/api/user",
        json={"user_id": "member@example.com", "password": "correct-password"},
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True

    events = app.extensions["analytics_repository"].list_usage_events(
        "desktop_client_uses",
        "member@example.com",
    )
    assert len(events) == 1


def test_signed_in_desktop_download_is_counted(app):
    installer = Path(app.static_folder) / "ReuniaSetup.exe"
    original = installer.read_bytes() if installer.exists() else None
    installer.write_bytes(b"test-installer")
    try:
        client = app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["user_id"] = "member@example.com"
            flask_session["is_admin"] = False

        response = client.get("/download/desktop-client")
        assert response.status_code == 200
        assert response.headers["Content-Disposition"].startswith("attachment;")
        assert "ReuniaSetup.exe" in response.headers["Content-Disposition"]

        events = app.extensions["analytics_repository"].list_usage_events(
            "desktop_client_downloads",
            "member@example.com",
        )
        assert len(events) == 1
    finally:
        if original is None:
            installer.unlink(missing_ok=True)
        else:
            installer.write_bytes(original)


def test_desktop_use_counter_is_idempotent_for_same_session(app):
    with app.app_context():
        metrics = UsageMetricsService()
        assert metrics.record_desktop_client_use(
            "member@example.com",
            event_id="same-desktop-session",
        ) is True
        assert metrics.record_desktop_client_use(
            "member@example.com",
            event_id="same-desktop-session",
        ) is False

    events = app.extensions["analytics_repository"].list_usage_events(
        "desktop_client_uses",
        "member@example.com",
    )
    assert len(events) == 1


def test_admin_user_usage_detail_requires_admin(app):
    client = app.test_client()
    assert client.get(
        "/api/admin/analytics/users/member%40example.com/usage"
    ).status_code == 401

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["is_admin"] = False

    assert client.get(
        "/api/admin/analytics/users/member%40example.com/usage"
    ).status_code == 403


def test_expanded_admin_metrics_are_available(app, monkeypatch):
    monkeypatch.setattr(
        UserRepository,
        "list_all",
        lambda self: [
            {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "full_name": "Member User",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["is_admin"] = False

    response = client.post(
        "/api/analytics/event",
        json={
            "metric": "feature_used",
            "event_id": "feature-event-1",
            "metadata": {"feature": "meeting_review"},
        },
    )
    assert response.status_code == 204

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    dashboard = client.get("/api/admin/analytics?days=30")
    assert dashboard.status_code == 200
    data = dashboard.get_json()
    assert "growth" in data
    assert "activation" in data
    assert "retention" in data
    assert "meeting_funnel" in data
    assert "feature_adoption" in data
    assert "reliability" in data
    assert "document_health" in data
    assert "action_outcomes" in data
    assert "support_health" in data
    assert "live_qa_health" in data
    assert "ai_usage" in data
    assert "alerts" in data
    meeting_review = next(
        item for item in data["feature_adoption"]
        if item["feature"] == "meeting_review"
    )
    assert meeting_review["users"] == 1


def test_action_completion_average_reports_its_timed_sample_size(app, monkeypatch):
    monkeypatch.setattr(UserRepository, "list_all", lambda self: [])
    repository = app.extensions["action_repository"]
    repository.create({
        "user_id": "member@example.com",
        "action_id": "manual-completed-at",
        "status": "done",
        "created_at": "2026-07-01T08:00:00+00:00",
        "completed_at": "2026-07-02T20:00:00+00:00",
    })
    repository.create({
        "user_id": "member@example.com",
        "action_id": "manual-updated-at",
        "status": "done",
        "created_at": "2026-07-03T08:00:00+00:00",
        "updated_at": "2026-07-03T20:00:00+00:00",
    })
    repository.create({
        "user_id": "member@example.com",
        "action_id": "manual-open",
        "status": "in_progress",
        "created_at": "2026-07-04T08:00:00+00:00",
    })

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/api/admin/analytics?days=7")
    assert response.status_code == 200
    outcomes = response.get_json()["action_outcomes"]
    assert outcomes["average_completion_hours"] == 24.0
    assert outcomes["completion_time_sample_size"] == 2


def test_product_event_does_not_store_content_fields(app):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"
        flask_session["is_admin"] = False

    response = client.post(
        "/api/analytics/event",
        json={
            "metric": "ai_request",
            "event_id": "ai-event-1",
            "metadata": {
                "feature": "knowledge_search",
                "input_tokens": 100,
                "prompt": "private prompt",
                "response": "private response",
                "transcript": "private transcript",
            },
        },
    )
    assert response.status_code == 204
    events = app.extensions["analytics_repository"].list_usage_events(
        "ai_request",
        "member@example.com",
    )
    assert len(events) == 1
    assert events[0]["feature"] == "knowledge_search"
    assert events[0]["input_tokens"] == 100
    assert "prompt" not in events[0]
    assert "response" not in events[0]
    assert "transcript" not in events[0]


def test_ai_usage_uses_model_specific_pricing_and_cached_tokens(app):
    usage = SimpleNamespace(
        prompt_tokens=1_000,
        completion_tokens=500,
        prompt_tokens_details=SimpleNamespace(cached_tokens=400),
    )
    response = SimpleNamespace(usage=usage)

    with app.app_context():
        recorded = UsageMetricsService().record_ai_response(
            "member@example.com",
            response,
            feature="knowledge_search",
            model="gpt-4o-mini",
            event_id="priced-ai-event",
        )

    assert recorded is True
    events = app.extensions["analytics_repository"].list_usage_events(
        "ai_request",
        "member@example.com",
    )
    event = next(item for item in events if item.get("source_id"))
    assert event["input_tokens"] == 1_000
    assert event["cached_input_tokens"] == 400
    assert event["output_tokens"] == 500
    assert event["cost_calculated"] is True
    assert event["estimated_cost_usd"] == 0.00042


def test_transcription_usage_records_duration_cost(app):
    with app.app_context():
        recorded = UsageMetricsService().record_transcription_usage(
            "member@example.com",
            feature="meeting_transcription",
            model="whisper-1",
            audio_seconds=120,
            event_id="transcription-cost-event",
            source="microphone",
        )

    assert recorded is True
    events = app.extensions["analytics_repository"].list_usage_events(
        "ai_request",
        "member@example.com",
    )
    event = next(
        item for item in events
        if item.get("request_type") == "transcription"
    )
    assert event["audio_seconds"] == 120
    assert event["estimated_cost_usd"] == 0.012
    assert event["cost_calculated"] is True


def test_historical_zero_cost_event_is_recalculated_from_tokens(app):
    event = {
        "metric": "ai_request",
        "model": "gpt-4o-mini",
        "input_tokens": 1_000,
        "output_tokens": 500,
        "estimated_cost_usd": 0,
    }

    with app.app_context():
        summary = AdminAnalyticsService.__new__(AdminAnalyticsService)._ai_cost_summary(
            [event]
        )

    assert summary["priced_requests"] == 1
    assert summary["unpriced_requests"] == 0
    assert summary["estimated_cost_usd"] == 0.00045


def test_admin_page_contains_expanded_metric_sections(app):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/admin/analytics")
    assert response.status_code == 200
    assert b"Registration conversion" in response.data
    assert b"Activation and retention" in response.data
    assert b"Meeting funnel" in response.data
    assert b"Feature adoption" in response.data
    assert b"System health" in response.data
    assert b"Action outcomes" in response.data

def test_admin_users_csv_formats_last_active_as_readable_utc(app, monkeypatch):
    from meeting_assistant.blueprints.admin_analytics.routes import AdminAnalyticsService

    monkeypatch.setattr(
        AdminAnalyticsService,
        "dashboard",
        lambda self, days: {
            "period_days": 30,
            "users": [
                {
                    "full_name": "Example User",
                    "email": "user@example.com",
                    "last_active": 1784301465,
                }
            ],
        },
    )

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/api/admin/analytics/users.csv?days=30")

    assert response.status_code == 200
    csv_text = response.get_data(as_text=True)
    assert "Last active (UTC)" in csv_text
    assert "2026-07-17 15:17:45 UTC" in csv_text
    assert "1784301465" not in csv_text



def test_admin_users_csv_exports_document_storage_in_megabytes(app, monkeypatch):
    from meeting_assistant.blueprints.admin_analytics.routes import AdminAnalyticsService

    monkeypatch.setattr(
        AdminAnalyticsService,
        "dashboard",
        lambda self, days: {
            "period_days": 30,
            "users": [
                {
                    "full_name": "Storage User",
                    "email": "storage@example.com",
                    "document_total_bytes": 2_621_440,
                    "average_recording_duration_seconds": 1_500,
                    "maximum_recording_duration_seconds": 3_600,
                    "minimum_recording_duration_seconds": 45,
                }
            ],
        },
    )

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    response = client.get("/api/admin/analytics/users.csv?days=30")

    assert response.status_code == 200
    csv_text = response.get_data(as_text=True)
    assert "Document storage (MB)" in csv_text
    assert "Document storage (bytes)" not in csv_text
    assert "Average recording length (seconds)" in csv_text
    assert "Maximum recording length (seconds)" in csv_text
    assert "Minimum recording length (seconds)" in csv_text
    assert "2.50" in csv_text
    assert "1500,3600,45" in csv_text
    assert "2621440" not in csv_text



def test_admin_can_review_repeated_failure_users_and_error_details(app, monkeypatch):
    monkeypatch.setattr(
        UserRepository,
        "list_all",
        lambda self: [
            {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "full_name": "Member User",
            },
            {
                "user_id": "healthy@example.com",
                "email": "healthy@example.com",
                "full_name": "Healthy User",
            },
        ],
    )

    with app.app_context():
        metrics = UsageMetricsService()
        for index in range(3):
            assert metrics.record_product_event(
                "meeting_processing_failed",
                "member@example.com",
                event_id=f"failure-{index}",
                occurred_at=f"2026-07-2{index + 1}T12:00:00+00:00",
                metadata={
                    "source": "browser_recorder",
                    "stage": "uploading_segment",
                    "http_status": 413,
                    "status_text": "Payload Too Large",
                    "reference_id": f"recorder-{index}",
                    "error_summary": "One audio segment exceeded the safe upload limit.",
                },
            ) is True
        assert metrics.record_product_event(
            "ai_failure",
            "healthy@example.com",
            event_id="single-failure",
            metadata={"feature": "knowledge_search"},
        ) is True

    app.extensions["support_repository"].create({
        "request_id": "SUP-20260721-ABCDEF12",
        "created_at": "2026-07-21T12:05:00+00:00",
        "status": "new",
        "name": "Member User",
        "email": "member@example.com",
        "user_id": "member@example.com",
        "subject": "Browser Recorder error · recorder-2",
        "message": "Diagnostic details:\nReference ID: recorder-2\nFailed stage: Uploading segment",
        "source": "browser_recorder_error",
        "page_url": "https://www.reunia.app/meeting-recorder",
    })

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "admin@example.com"
        flask_session["is_admin"] = True

    dashboard = client.get("/api/admin/analytics?days=30")
    assert dashboard.status_code == 200
    repeated_alert = next(
        alert for alert in dashboard.get_json()["alerts"]
        if alert["title"] == "Users encountered repeated failures"
    )
    assert repeated_alert["action"] == "view_incidents"
    assert repeated_alert["action_label"] == "View incidents"

    incidents_response = client.get("/api/admin/analytics/incidents")
    assert incidents_response.status_code == 200
    incidents_data = incidents_response.get_json()
    assert incidents_data["incident_count"] == 4
    assert incidents_data["affected_user_count"] == 2
    assert incidents_data["repeated_user_count"] == 1
    member_incidents = [
        incident for incident in incidents_data["incidents"]
        if incident["user_id"] == "member@example.com"
    ]
    assert len(member_incidents) == 3
    latest_incident = member_incidents[0]
    assert latest_incident["full_name"] == "Member User"
    assert latest_incident["feature"] == "Browser Recorder"
    assert latest_incident["error_type"] == "Meeting processing failed"
    assert latest_incident["status"] == "open"
    assert latest_incident["repeated_user"] is True
    assert "upload-size limit" in latest_incident["cause"]
    assert latest_incident["support_reports"][0]["request_id"] == "SUP-20260721-ABCDEF12"
    assert "Browser Recorder" in incidents_data["filters"]["features"]

    response = client.get("/api/admin/analytics/repeated-failures")
    assert response.status_code == 200
    data = response.get_json()
    assert data["affected_user_count"] == 1
    assert data["total_failure_count"] == 3
    assert data["events_available"] is True
    assert data["support_reports_available"] is True
    user = data["users"][0]
    assert user["full_name"] == "Member User"
    assert user["email"] == "member@example.com"
    assert user["failure_count"] == 3
    assert user["failures"][0]["reference_id"] == "recorder-2"
    assert user["failures"][0]["http_status"] == "413"
    assert user["failures"][0]["error_summary"].startswith("One audio segment")
    assert user["support_reports"][0]["request_id"] == "SUP-20260721-ABCDEF12"
    assert "Diagnostic details" in user["support_reports"][0]["message"]
