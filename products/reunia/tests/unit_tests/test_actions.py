from __future__ import annotations

from meeting_assistant.repositories.action_repository import InMemoryActionRepository
from meeting_assistant.services.action_service import ActionService


class FakeTranscriptService:
    def __init__(self, meetings=None):
        self.meetings = meetings or []

    def list_for_user(self, user_id: str):
        return [dict(item, user_id=user_id) for item in self.meetings]


def sample_meetings():
    return [
        {
            "meeting_id": "meeting-123",
            "meeting_name": "Project kickoff",
            "timestamp": "2026-07-12T18:30:00+00:00",
            "action_items": [
                "Send the revised proposal",
                {
                    "description": "Confirm the implementation owner",
                    "owner": "Vincent",
                    "due_date": "2026-07-20",
                    "priority": "high",
                },
            ],
        }
    ]


def make_service(meetings=None):
    return ActionService(
        repository=InMemoryActionRepository(),
        transcript_service=FakeTranscriptService(meetings if meetings is not None else sample_meetings()),
    )


def test_list_derives_actions_from_transcripts(app):
    with app.app_context():
        actions = make_service().list_for_user("user-1")

    assert len(actions) == 2
    assert actions[0]["source"] == "meeting"
    assert {item["description"] for item in actions} == {
        "Send the revised proposal",
        "Confirm the implementation owner",
    }
    detailed = next(item for item in actions if item["priority"] == "high")
    assert detailed["owner"] == "Vincent"
    assert detailed["due_date"] == "2026-07-20"
    assert detailed["meeting_name"] == "Project kickoff"


def test_update_persists_meeting_action_override(app):
    service = make_service()
    with app.app_context():
        original = service.list_for_user("user-1")[0]
        updated = service.update(
            "user-1",
            original["action_id"],
            {"priority": "urgent", "status": "done", "owner": "Fatima"},
        )
        reloaded = next(
            item
            for item in service.list_for_user("user-1")
            if item["action_id"] == original["action_id"]
        )

    assert updated["priority"] == "urgent"
    assert updated["status"] == "done"
    assert updated["completed_at"]
    assert reloaded["owner"] == "Fatima"


def test_delete_meeting_action_creates_tombstone(app):
    service = make_service()
    with app.app_context():
        action = service.list_for_user("user-1")[0]
        service.delete("user-1", action["action_id"])
        remaining_ids = {
            item["action_id"] for item in service.list_for_user("user-1")
        }

    assert action["action_id"] not in remaining_ids


def test_manual_action_crud(app):
    service = make_service([])
    with app.app_context():
        created = service.create(
            "user-1",
            {
                "description": "Prepare the status report",
                "owner": "Vincent",
                "priority": "medium",
            },
        )
        assert created["action_id"].startswith("manual-")

        updated = service.update(
            "user-1",
            created["action_id"],
            {"status": "in_progress"},
        )
        assert updated["status"] == "in_progress"

        service.delete("user-1", created["action_id"])
        assert service.list_for_user("user-1") == []


def test_action_api_routes_use_authenticated_user(app, monkeypatch):
    service = make_service([])
    monkeypatch.setattr(
        "meeting_assistant.blueprints.actions.routes.ActionService",
        lambda: service,
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "user-1"

    create_response = client.post(
        "/api/actions",
        json={"description": "Send follow-up notes", "priority": "high"},
    )
    assert create_response.status_code == 201
    created = create_response.get_json()

    patch_response = client.patch(
        f"/api/actions/{created['action_id']}",
        json={"status": "done"},
    )
    assert patch_response.status_code == 200
    assert patch_response.get_json()["status"] == "done"

    list_response = client.get("/api/actions")
    assert list_response.status_code == 200
    assert len(list_response.get_json()) == 1

    delete_response = client.delete(f"/api/actions/{created['action_id']}")
    assert delete_response.status_code == 200
    assert client.get("/api/actions").get_json() == []

def test_action_center_includes_assign_to_me_control(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "user-1"
        session["full_name"] = "Vincent Wenger"

    response = client.get("/action-center.html")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="action-assign-me"' in page
    assert "Assign to me" in page
    assert "makes it appear under My actions" in page


def test_assign_to_me_script_uses_current_user_identity():
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[2]
        / "static"
        / "js"
        / "pages"
        / "action-center.js"
    ).read_text(encoding="utf-8")

    assert "elements.formOwner.value = state.currentUser" in script
    assert "elements.assignMe?.addEventListener('click', assignActionToCurrentUser)" in script
    assert "isCurrentUserOwner(elements.formOwner?.value)" in script



def test_action_center_filters_follow_table_order_and_include_due_date(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "user-1"

    response = client.get("/action-center.html")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    expected_ids = [
        'id="action-search"',
        'id="action-meeting-filter"',
        'id="action-owner-filter"',
        'id="action-due-filter"',
        'id="action-priority-filter"',
        'id="action-status-filter"',
        'id="action-sort"',
    ]
    positions = [page.index(item) for item in expected_ids]
    assert positions == sorted(positions)
    assert "Due today" in page
    assert "Due within 7 days" in page
    assert "No due date" in page


def test_due_date_filter_is_applied_by_action_center_script():
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[2]
        / "static"
        / "js"
        / "pages"
        / "action-center.js"
    ).read_text(encoding="utf-8")

    assert "elements.dueFilter = document.getElementById('action-due-filter')" in script
    assert "matchesDueDateFilter(action, due)" in script
    assert "if (filter === 'today') return isDueToday(action)" in script
    assert "if (filter === 'later') return isDueLater(action)" in script
