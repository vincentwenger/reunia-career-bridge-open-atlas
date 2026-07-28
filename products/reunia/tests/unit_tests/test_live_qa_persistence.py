from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from meeting_assistant.extensions import init_extensions
from meeting_assistant.repositories.live_qa_repository import DynamoLiveQARepository
from meeting_assistant.services.live_qa_service import LiveQAService


class FakeDynamoTable:
    def __init__(self, query_pages=None):
        self.items = {}
        self.query_pages = list(query_pages or [])
        self.query_calls = []

    def put_item(self, **kwargs):
        item = dict(kwargs["Item"])
        self.items[(item["user_id"], item["entry_id"])] = item
        return {}

    def update_item(self, **kwargs):
        key = (kwargs["Key"]["user_id"], kwargs["Key"]["entry_id"])
        item = self.items[key]
        values = kwargs["ExpressionAttributeValues"]
        item["chatgpt_answer"] = values[":answer"]
        item["expires_at"] = values[":expires_at"]
        return {}

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.query_pages:
            return self.query_pages.pop(0)
        return {"Items": []}


class RecordingRepository:
    def __init__(self):
        self.updates = []

    def update_answer(self, user_id, entry_id, answer, ttl_seconds):
        self.updates.append((user_id, entry_id, answer, ttl_seconds))


class FakeCompletions:
    def create(self, **kwargs):
        return [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=token))]
            )
            for token in ("Hello", " ", "world")
        ]


def test_dynamo_repository_creates_and_updates_ttl(monkeypatch):
    table = FakeDynamoTable()
    repository = DynamoLiveQARepository()
    monkeypatch.setattr(repository, "_table", lambda: table)
    monkeypatch.setattr(
        "meeting_assistant.repositories.live_qa_repository.time.time",
        lambda: 1_000,
    )

    repository.create(
        {
            "id": "entry-1",
            "user_id": "user-1",
            "origin": "microphone",
            "content": "Question",
            "chatgpt_answer": "Thinking...",
            "timestamp": "2026-07-14T12:00:00+00:00",
        },
        3_600,
    )
    stored = table.items[("user-1", "entry-1")]
    assert stored["entry_id"] == "entry-1"
    assert stored["expires_at"] == 4_600

    repository.update_answer("user-1", "entry-1", "Final answer", 7_200)
    assert stored["chatgpt_answer"] == "Final answer"
    assert stored["expires_at"] == 8_200


def test_dynamo_repository_paginates_and_filters_expired_items(monkeypatch):
    table = FakeDynamoTable(
        query_pages=[
            {
                "Items": [
                    {
                        "user_id": "user-1",
                        "entry_id": "newer",
                        "timestamp": "2026-07-14T12:02:00+00:00",
                        "expires_at": 2_000,
                    },
                    {
                        "user_id": "user-1",
                        "entry_id": "expired",
                        "timestamp": "2026-07-14T12:01:00+00:00",
                        "expires_at": 999,
                    },
                ],
                "LastEvaluatedKey": {"user_id": "user-1", "entry_id": "newer"},
            },
            {
                "Items": [
                    {
                        "user_id": "user-1",
                        "entry_id": "older",
                        "timestamp": "2026-07-14T12:00:00+00:00",
                        "expires_at": 2_000,
                    }
                ]
            },
        ]
    )
    repository = DynamoLiveQARepository()
    monkeypatch.setattr(repository, "_table", lambda: table)
    monkeypatch.setattr(
        "meeting_assistant.repositories.live_qa_repository.time.time",
        lambda: 1_000,
    )

    entries = repository.list_for_user("user-1")

    assert [item["id"] for item in entries] == ["older", "newer"]
    assert len(table.query_calls) == 2
    assert table.query_calls[1]["ExclusiveStartKey"] == {
        "user_id": "user-1",
        "entry_id": "newer",
    }
    assert all(call["ConsistentRead"] is False for call in table.query_calls)


def test_extensions_select_dynamodb_for_live_qa():
    app = Flask(__name__)
    app.config.update(
        LIVE_QA_STORAGE_BACKEND="dynamodb",
        LIVE_QA_TABLE_NAME="dev_meeting_live_qa",
        AWS_REGION="us-west-2",
        ACTIONS_STORAGE_BACKEND="memory",
        SUPPORT_STORAGE_BACKEND="memory",
        LIVE_QA_DYNAMO_CACHE_TTL_SECONDS=7.5,
    )

    init_extensions(app)

    repository = app.extensions["live_qa_repository"]
    assert isinstance(repository, DynamoLiveQARepository)
    assert repository._cache_ttl_seconds == 7.5


def test_dynamo_repository_reuses_and_updates_cached_feed(monkeypatch):
    stored = {
        "user_id": "user-1",
        "entry_id": "entry-1",
        "id": "entry-1",
        "timestamp": "2026-07-14T12:00:00+00:00",
        "chatgpt_answer": "Thinking...",
        "expires_at": 2_000,
    }
    table = FakeDynamoTable(query_pages=[{"Items": [dict(stored)]}])
    table.items[("user-1", "entry-1")] = dict(stored)
    repository = DynamoLiveQARepository(cache_ttl_seconds=10)
    monkeypatch.setattr(repository, "_table", lambda: table)
    monkeypatch.setattr(
        "meeting_assistant.repositories.live_qa_repository.time.time",
        lambda: 1_000,
    )

    first = repository.list_for_user("user-1")
    first[0]["chatgpt_answer"] = "Caller mutation"
    second = repository.list_for_user("user-1")

    assert len(table.query_calls) == 1
    assert second[0]["chatgpt_answer"] == "Thinking..."

    repository.update_answer("user-1", "entry-1", "Final answer", 3_600)
    third = repository.list_for_user("user-1")

    assert len(table.query_calls) == 1
    assert third[0]["chatgpt_answer"] == "Final answer"


def test_stream_persists_complete_short_answer(app):
    repository = RecordingRepository()
    app.extensions["live_qa_repository"] = repository
    app.config["LIVE_QA_PERSIST_INTERVAL_SECONDS"] = 60

    with app.app_context():
        service = LiveQAService()
        service._client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )
        output = "".join(
            service._generate_stream(
                user_id="user-1",
                entry_id="entry-1",
                model="test-model",
                prompt="Prompt",
                content="Question",
                ttl_seconds=3_600,
            )
        )

    assert output == "Hello world"
    assert repository.updates[-1] == (
        "user-1",
        "entry-1",
        "Hello world",
        3_600,
    )
    usage_events = app.extensions["analytics_repository"].list_usage_events(
        "live_qa_answers",
        "user-1",
    )
    assert len(usage_events) == 1
    assert usage_events[0]["source_id"] == "entry-1"


def test_stream_requests_usage_and_records_live_qa_cost(app):
    class UsageCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Answer"))],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[],
                    usage=SimpleNamespace(
                        prompt_tokens=1_000,
                        completion_tokens=500,
                        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
                    ),
                ),
            ]

    repository = RecordingRepository()
    completions = UsageCompletions()
    app.extensions["live_qa_repository"] = repository

    with app.app_context():
        service = LiveQAService()
        service._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        output = "".join(
            service._generate_stream(
                user_id="user-1",
                entry_id="entry-priced",
                model="gpt-4o-mini",
                prompt="Prompt",
                content="Question",
                ttl_seconds=3_600,
            )
        )

    assert output == "Answer"
    assert completions.calls[0]["stream_options"] == {"include_usage": True}
    events = app.extensions["analytics_repository"].list_usage_events(
        "ai_request",
        "user-1",
    )
    event = next(item for item in events if item.get("feature") == "live_qa")
    assert event["input_tokens"] == 1_000
    assert event["output_tokens"] == 500
    assert event["estimated_cost_usd"] == 0.00045


def test_dynamo_repository_respects_per_user_max_cache_age(monkeypatch):
    stored = {
        "user_id": "user-1",
        "entry_id": "entry-1",
        "id": "entry-1",
        "timestamp": "2026-07-14T12:00:00+00:00",
        "chatgpt_answer": "Thinking...",
        "expires_at": 2_000,
    }
    table = FakeDynamoTable(
        query_pages=[
            {"Items": [dict(stored)]},
            {"Items": [dict(stored, chatgpt_answer="Updated elsewhere")]},
        ]
    )
    repository = DynamoLiveQARepository(cache_ttl_seconds=10)
    monkeypatch.setattr(repository, "_table", lambda: table)
    monkeypatch.setattr(
        "meeting_assistant.repositories.live_qa_repository.time.time",
        lambda: 1_000,
    )
    clock = {"now": 100.0}
    monkeypatch.setattr(
        "meeting_assistant.repositories.live_qa_repository.time.monotonic",
        lambda: clock["now"],
    )

    first = repository.list_for_user("user-1", max_cache_age_seconds=1.0)
    clock["now"] = 100.5
    second = repository.list_for_user("user-1", max_cache_age_seconds=1.0)
    clock["now"] = 101.1
    third = repository.list_for_user("user-1", max_cache_age_seconds=1.0)

    assert first[0]["chatgpt_answer"] == "Thinking..."
    assert second[0]["chatgpt_answer"] == "Thinking..."
    assert third[0]["chatgpt_answer"] == "Updated elsewhere"
    assert len(table.query_calls) == 2
