from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from scripts.delete_dynamodb_user_records import (
    TABLES,
    TableMatch,
    collect_table_matches,
    configured_tables,
    delete_matches,
)


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeClient:
    def __init__(self, description: dict[str, Any], pages: list[dict[str, Any]]):
        self.description = description
        self.pages = list(pages)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[dict[str, Any]] = []

    def describe_table(self, **kwargs):
        self.calls.append(("describe_table", kwargs))
        return {"Table": self.description}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self.pages.pop(0)

    def scan(self, **kwargs):
        self.calls.append(("scan", kwargs))
        return self.pages.pop(0)

    def delete_item(self, **kwargs):
        self.calls.append(("delete_item", kwargs))
        self.deleted.append(kwargs)
        return {}


def test_configured_tables_use_only_explicit_environment_values(monkeypatch):
    expected = []
    for index, variable in enumerate(TABLES):
        table_name = f"configured-table-{index}"
        monkeypatch.setenv(variable, table_name)
        expected.append(table_name)

    assert configured_tables() == expected


def test_configured_tables_require_explicit_names_in_every_environment(monkeypatch):
    for variable in TABLES:
        monkeypatch.delenv(variable, raising=False)

    with pytest.raises(RuntimeError, match="TRANSCRIPTS_TABLE_NAME"):
        configured_tables()


def test_collect_uses_partition_query_and_paginates():
    client = FakeClient(
        {
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "action_id", "KeyType": "RANGE"},
            ]
        },
        [
            {
                "Items": [
                    {"user_id": {"S": "test@example.com"}, "action_id": {"S": "a1"}}
                ],
                "LastEvaluatedKey": {
                    "user_id": {"S": "test@example.com"},
                    "action_id": {"S": "a1"},
                },
            },
            {
                "Items": [
                    {"user_id": {"S": "test@example.com"}, "action_id": {"S": "a2"}}
                ]
            },
        ],
    )

    match = collect_table_matches(client, "actions", "test@example.com")

    assert match.lookup_method == "partition-key query"
    assert len(match.keys) == 2
    query_calls = [kwargs for operation, kwargs in client.calls if operation == "query"]
    assert len(query_calls) == 2
    assert query_calls[0]["ConsistentRead"] is True
    assert "ExclusiveStartKey" in query_calls[1]


def test_collect_uses_user_index_when_available():
    client = FakeClient(
        {
            "KeySchema": [
                {"AttributeName": "meeting_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "user_id-index",
                    "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
                }
            ],
        },
        [
            {
                "Items": [
                    {
                        "meeting_id": {"S": "meeting-1"},
                        "timestamp": {"S": "2026-07-18T00:00:00Z"},
                        "user_id": {"S": "test@example.com"},
                    }
                ]
            }
        ],
    )

    match = collect_table_matches(client, "transcripts", "test@example.com")

    assert match.lookup_method == "index query (user_id-index)"
    query = next(kwargs for operation, kwargs in client.calls if operation == "query")
    assert query["IndexName"] == "user_id-index"
    assert "ConsistentRead" not in query
    assert match.keys == (
        {
            "meeting_id": {"S": "meeting-1"},
            "timestamp": {"S": "2026-07-18T00:00:00Z"},
        },
    )


def test_collect_scans_when_user_id_is_not_indexed():
    client = FakeClient(
        {"KeySchema": [{"AttributeName": "share_id", "KeyType": "HASH"}]},
        [
            {
                "Items": [
                    {
                        "share_id": {"S": "share-1"},
                        "user_id": {"S": "test@example.com"},
                    }
                ]
            }
        ],
    )

    match = collect_table_matches(client, "shares", "test@example.com")

    assert match.lookup_method == "filtered table scan"
    scan = next(kwargs for operation, kwargs in client.calls if operation == "scan")
    assert scan["ConsistentRead"] is True
    assert scan["FilterExpression"].endswith(" = :user_id")


def test_delete_uses_user_id_condition():
    client = FakeClient({}, [])
    match = TableMatch(
        "shares",
        ({"share_id": {"S": "share-1"}},),
        "filtered table scan",
    )

    deleted, changed = delete_matches(client, match, "test@example.com")

    assert (deleted, changed) == (1, 0)
    assert client.deleted[0]["ConditionExpression"] == "#user_id = :user_id"
    assert client.deleted[0]["ExpressionAttributeValues"] == {
        ":user_id": {"S": "test@example.com"}
    }


def test_delete_skips_record_if_owner_changed():
    class ChangedClient(FakeClient):
        def delete_item(self, **kwargs):
            raise _client_error("ConditionalCheckFailedException", "DeleteItem")

    client = ChangedClient({}, [])
    match = TableMatch(
        "shares",
        ({"share_id": {"S": "share-1"}},),
        "filtered table scan",
    )

    assert delete_matches(client, match, "test@example.com") == (0, 1)
