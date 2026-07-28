from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any, Protocol

from botocore.exceptions import ClientError

from meeting_assistant.repositories.base import DynamoRepository


class SupportRepository(Protocol):
    def create(self, request_item: dict[str, Any]) -> None: ...

    def list_all(self) -> list[dict[str, Any]]: ...

    def get_by_id(self, request_id: str) -> dict[str, Any] | None: ...

    def update_status(
        self,
        request_id: str,
        status: str,
        updated_at: str,
    ) -> dict[str, Any] | None: ...


class InMemorySupportRepository:
    """Thread-safe support request store for development and tests."""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def create(self, request_item: dict[str, Any]) -> None:
        with self._lock:
            self._items.append(deepcopy(request_item))

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._items)

    def get_by_id(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._items:
                if str(item.get("request_id")) == request_id:
                    return deepcopy(item)
        return None

    def update_status(
        self,
        request_id: str,
        status: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            for item in self._items:
                if str(item.get("request_id")) != request_id:
                    continue
                item["status"] = status
                item["status_updated_at"] = updated_at
                if status == "read" and not item.get("read_at"):
                    item["read_at"] = updated_at
                if status == "resolved":
                    item["resolved_at"] = updated_at
                elif "resolved_at" in item:
                    item.pop("resolved_at", None)
                return deepcopy(item)
        return None


class DynamoSupportRepository(DynamoRepository):
    """Persistent support request storage backed by DynamoDB."""

    def _table(self):
        return self.table("SUPPORT_REQUESTS_TABLE_NAME")

    def create(self, request_item: dict[str, Any]) -> None:
        self._table().put_item(
            Item=request_item,
            ConditionExpression="attribute_not_exists(request_id)",
        )

    def list_all(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {}
        while True:
            response = self._table().scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        return items

    def get_by_id(self, request_id: str) -> dict[str, Any] | None:
        response = self._table().get_item(Key={"request_id": request_id})
        return response.get("Item")

    def update_status(
        self,
        request_id: str,
        status: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        names = {
            "#status": "status",
            "#status_updated_at": "status_updated_at",
        }
        values: dict[str, Any] = {
            ":status": status,
            ":updated_at": updated_at,
        }
        set_parts = [
            "#status = :status",
            "#status_updated_at = :updated_at",
        ]
        remove_parts: list[str] = []
        if status == "read":
            names["#read_at"] = "read_at"
            names["#resolved_at"] = "resolved_at"
            set_parts.append("#read_at = if_not_exists(#read_at, :updated_at)")
            remove_parts.append("#resolved_at")
        elif status == "resolved":
            names["#resolved_at"] = "resolved_at"
            set_parts.append("#resolved_at = :updated_at")
        else:
            names["#resolved_at"] = "resolved_at"
            remove_parts.append("#resolved_at")

        update_expression = "SET " + ", ".join(set_parts)
        if remove_parts:
            update_expression += " REMOVE " + ", ".join(remove_parts)

        try:
            response = self._table().update_item(
                Key={"request_id": request_id},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(request_id)",
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise
        return response.get("Attributes")
