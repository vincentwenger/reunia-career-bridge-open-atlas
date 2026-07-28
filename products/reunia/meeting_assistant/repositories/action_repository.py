from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any, Protocol

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from meeting_assistant.repositories.base import DynamoRepository
from meeting_assistant.utils.exceptions import ResourceNotFoundError


class ActionRepository(Protocol):
    def list_for_user(self, user_id: str) -> list[dict[str, Any]]: ...

    def list_all(self) -> list[dict[str, Any]]: ...

    def get(self, user_id: str, action_id: str) -> dict[str, Any] | None: ...

    def create(self, item: dict[str, Any]) -> None: ...

    def save(self, item: dict[str, Any]) -> None: ...

    def delete(self, user_id: str, action_id: str) -> None: ...


class InMemoryActionRepository:
    """Thread-safe action storage used by tests and optional local development."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(
                [
                    item
                    for (stored_user_id, _), item in self._items.items()
                    if stored_user_id == user_id
                ]
            )

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._items.values()))

    def get(self, user_id: str, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get((user_id, action_id))
            return deepcopy(item) if item is not None else None

    def create(self, item: dict[str, Any]) -> None:
        key = (str(item["user_id"]), str(item["action_id"]))
        with self._lock:
            if key in self._items:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ConditionalCheckFailedException",
                            "Message": "Action already exists.",
                        }
                    },
                    "PutItem",
                )
            self._items[key] = deepcopy(item)

    def save(self, item: dict[str, Any]) -> None:
        key = (str(item["user_id"]), str(item["action_id"]))
        with self._lock:
            self._items[key] = deepcopy(item)

    def delete(self, user_id: str, action_id: str) -> None:
        with self._lock:
            if self._items.pop((user_id, action_id), None) is None:
                raise ResourceNotFoundError("Action not found.")


class DynamoActionRepository(DynamoRepository):
    """DynamoDB action storage.

    The table must use `user_id` (String) as its partition key and `action_id`
    (String) as its sort key. This lets every query stay scoped to the signed-in
    user without requiring a secondary index or a table scan.
    """

    def _table(self):
        return self.table("ACTIONS_TABLE_NAME")

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        query_args: dict[str, Any] = {
            "KeyConditionExpression": Key("user_id").eq(user_id),
        }
        items: list[dict[str, Any]] = []

        while True:
            response = self._table().query(**query_args)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_args["ExclusiveStartKey"] = last_key

        return items

    def list_all(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        scan_args: dict[str, Any] = {}
        while True:
            response = self._table().scan(**scan_args)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            scan_args["ExclusiveStartKey"] = last_key

    def get(self, user_id: str, action_id: str) -> dict[str, Any] | None:
        response = self._table().get_item(
            Key={"user_id": user_id, "action_id": action_id},
            ConsistentRead=True,
        )
        return response.get("Item")

    def create(self, item: dict[str, Any]) -> None:
        self._table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(#action_id)",
            ExpressionAttributeNames={"#action_id": "action_id"},
        )

    def save(self, item: dict[str, Any]) -> None:
        self._table().put_item(Item=item)

    def delete(self, user_id: str, action_id: str) -> None:
        response = self._table().delete_item(
            Key={"user_id": user_id, "action_id": action_id},
            ReturnValues="ALL_OLD",
        )
        if not response.get("Attributes"):
            raise ResourceNotFoundError("Action not found.")
