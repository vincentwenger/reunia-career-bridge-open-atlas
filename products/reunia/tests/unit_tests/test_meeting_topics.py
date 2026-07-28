from __future__ import annotations

from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.utils.json_parsing import parse_meeting_insights


class _TranscriptRepository:
    def __init__(self):
        self.records = [
            {
                "meeting_id": "m-1",
                "timestamp": "2026-07-20T10:00:00+00:00",
                "user_id": "user@example.com",
                "topics": ["Product Roadmap", "Hiring"],
            },
            {
                "meeting_id": "m-2",
                "timestamp": "2026-07-19T10:00:00+00:00",
                "user_id": "user@example.com",
                "topics": ["product roadmap", "Budget"],
            },
        ]

    def list_for_user(self, user_id):
        return [dict(record) for record in self.records if record["user_id"] == user_id]

    def update_owned(self, user_id, meeting_id, timestamp, fields):
        for record in self.records:
            if (
                record["user_id"] == user_id
                and record["meeting_id"] == meeting_id
                and record["timestamp"] == timestamp
            ):
                record.update(fields)
                return
        raise AssertionError("meeting not found")


def _service(repository):
    return TranscriptService(
        repository=repository,
        analysis_service=object(),
        user_service=object(),
    )


def test_meeting_insights_parse_topics_deduplicates_and_limits_suggestions():
    result = parse_meeting_insights(
        '{"meeting_name":"Planning","summary":"Summary","topics":'
        '["Product Roadmap"," product roadmap ","Hiring","Budget"],'
        '"action_items":[],"open_questions":[]}'
    )

    assert result["topics"] == ["Product Roadmap", "Hiring", "Budget"]


def test_update_meeting_topics_normalizes_values():
    repository = _TranscriptRepository()
    result = _service(repository).update(
        "user@example.com",
        "m-1",
        "2026-07-20T10:00:00+00:00",
        {"topics": [" Sales ", "sales", "Customer Feedback"]},
    )

    assert result["topics"] == ["Sales", "Customer Feedback"]
    assert repository.records[0]["topics"] == ["Sales", "Customer Feedback"]


def test_merge_topics_updates_every_owned_meeting_case_insensitively():
    repository = _TranscriptRepository()
    result = _service(repository).manage_topics(
        "user@example.com",
        {"operation": "merge", "source": "Product Roadmap", "target": "Product"},
    )

    assert result["updated_meetings"] == 2
    assert repository.records[0]["topics"] == ["Product", "Hiring"]
    assert repository.records[1]["topics"] == ["Product", "Budget"]


def test_delete_topic_keeps_meetings_and_removes_only_the_topic():
    repository = _TranscriptRepository()
    result = _service(repository).manage_topics(
        "user@example.com",
        {"operation": "delete", "source": "Hiring"},
    )

    assert result["updated_meetings"] == 1
    assert repository.records[0]["topics"] == ["Product Roadmap"]
    assert len(repository.records) == 2


def test_topic_management_endpoint_uses_authenticated_user(app, monkeypatch):
    captured = {}

    def _manage_topics(self, user_id, data):
        captured["user_id"] = user_id
        captured["data"] = data
        return {"updated_meetings": 2}

    monkeypatch.setattr(TranscriptService, "manage_topics", _manage_topics)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "member@example.com"

    response = client.patch(
        "/api/transcript-topics",
        json={"operation": "merge", "source": "Roadmap", "target": "Product"},
    )

    assert response.status_code == 200
    assert response.get_json()["updated_meetings"] == 2
    assert captured == {
        "user_id": "member@example.com",
        "data": {"operation": "merge", "source": "Roadmap", "target": "Product"},
    }
