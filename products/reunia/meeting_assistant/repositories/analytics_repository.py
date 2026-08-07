from __future__ import annotations

import math
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from flask import current_app

from meeting_assistant.repositories.base import DynamoRepository


def _dynamodb_safe(value: Any) -> Any:
    """Recursively convert Python floats into DynamoDB-supported Decimals."""

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Analytics values must not contain NaN or infinity.")
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _dynamodb_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dynamodb_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_dynamodb_safe(item) for item in value]
    if isinstance(value, set):
        return {_dynamodb_safe(item) for item in value}
    return value


class AnalyticsRepository(Protocol):
    def record_activity(self, event: dict[str, Any]) -> None: ...

    def list_activity(self, start_date: str | None = None) -> list[dict[str, Any]]: ...

    def record_usage_event(self, event: dict[str, Any]) -> bool: ...

    def list_usage_events(
        self,
        metric: str | None = None,
        user_id: str | None = None,
        start_date: str | None = None,
    ) -> list[dict[str, Any]]: ...


class InMemoryAnalyticsRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record_activity(self, event: dict[str, Any]) -> None:
        session_key = str(event["session_key"])
        with self._lock:
            existing = self._items.get(session_key, {})
            first_seen = int(existing.get("first_seen") or event["observed_at"])
            updated = {
                **existing,
                "session_key": session_key,
                "record_type": "activity",
                "session_id": str(event["session_id"]),
                "visitor_id": str(event["visitor_id"]),
                "activity_date": str(event["activity_date"]),
                "analytics_date": str(event.get("analytics_date") or event["activity_date"]),
                "identity_type": str(event["identity_type"]),
                "first_seen": min(first_seen, int(event["observed_at"])),
                "last_seen": max(
                    int(existing.get("last_seen") or 0),
                    int(event["observed_at"]),
                ),
                "last_page": str(event["page_path"]),
                "active_seconds": int(existing.get("active_seconds") or 0)
                + int(event["active_seconds"]),
                "page_views": int(existing.get("page_views") or 0)
                + int(event["page_views"]),
            }
            if event.get("user_id"):
                updated["user_id"] = str(event["user_id"])
            else:
                updated.pop("user_id", None)
            if event.get("country_code"):
                updated["country_code"] = str(event["country_code"])
            if event.get("feature"):
                updated["last_feature"] = str(event["feature"])
            self._items[session_key] = updated

    def list_activity(self, start_date: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [
                deepcopy(item)
                for item in self._items.values()
                if _is_activity_record(item)
            ]
        if start_date:
            items = [
                item for item in items
                if str(item.get("activity_date") or "") >= start_date
            ]
        return items

    def record_usage_event(self, event: dict[str, Any]) -> bool:
        event_key = str(event["session_key"])
        with self._lock:
            if event_key in self._items:
                return False
            self._items[event_key] = {
                **deepcopy(event),
                "session_key": event_key,
                "record_type": "usage_event",
            }
        return True

    def list_usage_events(
        self,
        metric: str | None = None,
        user_id: str | None = None,
        start_date: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = [
                deepcopy(item)
                for item in self._items.values()
                if item.get("record_type") == "usage_event"
            ]
        if metric:
            items = [item for item in items if item.get("metric") == metric]
        if user_id:
            items = [item for item in items if item.get("user_id") == user_id]
        if start_date:
            items = [
                item for item in items
                if str(item.get("analytics_date") or item.get("occurred_at") or "")[:10]
                >= start_date
            ]
        return items


class DynamoAnalyticsRepository(DynamoRepository):
    def _table(self):
        return self.table("ANALYTICS_TABLE_NAME")

    def record_activity(self, event: dict[str, Any]) -> None:
        names = {
            "#record_type": "record_type",
            "#visitor_id": "visitor_id",
            "#session_id": "session_id",
            "#activity_date": "activity_date",
            "#analytics_date": "analytics_date",
            "#identity_type": "identity_type",
            "#first_seen": "first_seen",
            "#last_seen": "last_seen",
            "#last_page": "last_page",
            "#active_seconds": "active_seconds",
            "#page_views": "page_views",
            "#user_id": "user_id",
        }
        values: dict[str, Any] = {
            ":record_type": "activity",
            ":visitor_id": event["visitor_id"],
            ":session_id": event["session_id"],
            ":activity_date": event["activity_date"],
            ":analytics_date": event.get("analytics_date") or event["activity_date"],
            ":identity_type": event["identity_type"],
            ":now": int(event["observed_at"]),
            ":last_page": event["page_path"],
            ":active_seconds": int(event["active_seconds"]),
            ":page_views": int(event["page_views"]),
        }
        set_parts = [
            "#record_type = :record_type",
            "#visitor_id = :visitor_id",
            "#session_id = :session_id",
            "#activity_date = :activity_date",
            "#analytics_date = :analytics_date",
            "#identity_type = :identity_type",
            "#first_seen = if_not_exists(#first_seen, :now)",
            "#last_seen = :now",
            "#last_page = :last_page",
        ]
        remove_part = ""
        if event.get("user_id"):
            values[":user_id"] = event["user_id"]
            set_parts.append("#user_id = :user_id")
        else:
            remove_part = " REMOVE #user_id"
        if event.get("country_code"):
            names["#country_code"] = "country_code"
            values[":country_code"] = event["country_code"]
            set_parts.append("#country_code = :country_code")
        if event.get("feature"):
            names["#last_feature"] = "last_feature"
            values[":last_feature"] = event["feature"]
            set_parts.append("#last_feature = :last_feature")

        update_expression = (
            "SET " + ", ".join(set_parts)
            + remove_part
            + " ADD #active_seconds :active_seconds, #page_views :page_views"
        )
        self._table().update_item(
            Key={"session_key": event["session_key"]},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def list_activity(self, start_date: str | None = None) -> list[dict[str, Any]]:
        if start_date:
            items = self._query_date_index(start_date)
            return [item for item in items if _is_activity_record(item)]
        return [item for item in self._scan_all() if _is_activity_record(item)]

    def record_usage_event(self, event: dict[str, Any]) -> bool:
        item = _dynamodb_safe(
            {
                **event,
                "record_type": "usage_event",
            }
        )
        try:
            self._table().put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(#session_key)",
                ExpressionAttributeNames={"#session_key": "session_key"},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def list_usage_events(
        self,
        metric: str | None = None,
        user_id: str | None = None,
        start_date: str | None = None,
    ) -> list[dict[str, Any]]:
        expression = Attr("record_type").eq("usage_event")
        if metric:
            expression &= Attr("metric").eq(metric)
        if user_id:
            expression &= Attr("user_id").eq(user_id)

        if start_date:
            return [
                item for item in self._query_date_index(start_date)
                if item.get("record_type") == "usage_event"
                and (not metric or item.get("metric") == metric)
                and (not user_id or item.get("user_id") == user_id)
            ]
        return self._scan_all(filter_expression=expression)

    def _query_date_index(self, start_date: str) -> list[dict[str, Any]]:
        """Query daily GSI partitions; fall back safely while an older table is migrated."""
        index_name = str(current_app.config.get("ANALYTICS_DATE_INDEX") or "").strip()
        if not index_name:
            return self._scan_since(start_date)
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError:
            return self._scan_since(start_date)

        today = datetime.now(timezone.utc).date()
        items: list[dict[str, Any]] = []
        day = start
        try:
            while day <= today:
                query_kwargs: dict[str, Any] = {
                    "IndexName": index_name,
                    "KeyConditionExpression": Key("analytics_date").eq(day.isoformat()),
                }
                while True:
                    response = self._table().query(**query_kwargs)
                    items.extend(response.get("Items", []))
                    last_key = response.get("LastEvaluatedKey")
                    if not last_key:
                        break
                    query_kwargs["ExclusiveStartKey"] = last_key
                day += timedelta(days=1)
            return items
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in {"ValidationException", "ResourceNotFoundException"}:
                raise
            current_app.logger.warning(
                "Analytics date index %s is unavailable; falling back to a table scan.",
                index_name,
            )
            return self._scan_since(start_date)

    def _scan_since(self, start_date: str) -> list[dict[str, Any]]:
        items = self._scan_all()
        return [
            item for item in items
            if str(
                item.get("analytics_date")
                or item.get("activity_date")
                or item.get("occurred_at")
                or ""
            )[:10] >= start_date
        ]

    def _scan_all(self, filter_expression: Any | None = None) -> list[dict[str, Any]]:
        scan_kwargs: dict[str, Any] = {}
        if filter_expression is not None:
            scan_kwargs["FilterExpression"] = filter_expression

        items: list[dict[str, Any]] = []
        while True:
            response = self._table().scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            scan_kwargs["ExclusiveStartKey"] = last_key


def _is_activity_record(item: dict[str, Any]) -> bool:
    record_type = str(item.get("record_type") or "")
    if record_type:
        return record_type == "activity"
    # Backward compatibility for records created before record_type existed.
    return bool(item.get("activity_date") and item.get("session_id"))
