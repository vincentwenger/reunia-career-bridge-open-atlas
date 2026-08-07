"""DynamoDB-compatible in-memory test resources.

These test doubles exercise the production DynamoDB repository without requiring
network access or AWS credentials. They are activated only when Flask TESTING is
true.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class InMemoryApplicationTable:
    """Minimal owner-partitioned DynamoDB Table resource used by tests."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _key(value: dict[str, Any]) -> tuple[str, str]:
        return str(value["owner_id"]), str(value["storage_key"])

    def put_item(self, *, Item: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        key = self._key(Item)
        if kwargs.get("ConditionExpression") and key in self.items:
            from botocore.exceptions import ClientError

            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "conditional create conflict",
                    }
                },
                "PutItem",
            )
        self.items[key] = deepcopy(Item)
        return {}

    def get_item(self, *, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        item = self.items.get(self._key(Key))
        return {"Item": deepcopy(item)} if item is not None else {}

    def delete_item(self, *, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        item = self.items.pop(self._key(Key), None)
        if kwargs.get("ReturnValues") == "ALL_OLD" and item is not None:
            return {"Attributes": deepcopy(item)}
        return {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        values = kwargs["ExpressionAttributeValues"]
        owner_id = str(values[":owner_id"])
        prefix = str(values[":prefix"])
        items = [
            deepcopy(item)
            for (stored_owner, storage_key), item in self.items.items()
            if stored_owner == owner_id and storage_key.startswith(prefix)
        ]
        items.sort(key=lambda item: str(item["storage_key"]))
        return {"Items": items}
