from __future__ import annotations

import json
import threading
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from flask import current_app

from meeting_assistant.repositories.base import DynamoRepository
from meeting_assistant.utils.exceptions import ResourceNotFoundError
from meeting_assistant.utils.json_parsing import to_json_compatible

_MEMORY_LOCK = threading.RLock()
_MEMORY_RECORDS: dict[str, dict[str, Any]] = {}
_LOCAL_LOCK = threading.RLock()


class MeetingShareRepository(DynamoRepository):
    """Persist public meeting-share snapshots using the configured backend."""

    def __init__(self) -> None:
        self.backend = str(
            current_app.config.get("MEETING_SHARES_STORAGE_BACKEND", "dynamodb")
        ).strip().lower()

    def create(self, item: dict[str, Any]) -> None:
        if self.backend == "memory":
            with _MEMORY_LOCK:
                if item["share_id"] in _MEMORY_RECORDS:
                    raise ValueError("Share link already exists.")
                _MEMORY_RECORDS[item["share_id"]] = deepcopy(item)
            return
        if self.backend == "local":
            with _LOCAL_LOCK:
                records = self._read_local()
                if item["share_id"] in records:
                    raise ValueError("Share link already exists.")
                records[item["share_id"]] = deepcopy(item)
                self._write_local(records)
            return

        self._table().put_item(
            Item=_for_dynamodb(item),
            ConditionExpression="attribute_not_exists(share_id)",
        )

    def get(self, share_id: str) -> dict[str, Any] | None:
        if self.backend == "memory":
            with _MEMORY_LOCK:
                record = _MEMORY_RECORDS.get(share_id)
                return deepcopy(record) if record else None
        if self.backend == "local":
            with _LOCAL_LOCK:
                record = self._read_local().get(share_id)
                return deepcopy(record) if record else None

        response = self._table().get_item(Key={"share_id": share_id})
        item = response.get("Item")
        return to_json_compatible(item) if item else None

    def list_for_meeting(
        self,
        user_id: str,
        meeting_id: str,
        meeting_timestamp: str,
    ) -> list[dict[str, Any]]:
        if self.backend == "memory":
            with _MEMORY_LOCK:
                records = list(_MEMORY_RECORDS.values())
        elif self.backend == "local":
            with _LOCAL_LOCK:
                records = list(self._read_local().values())
        else:
            records = self._scan_all(
                FilterExpression=(
                    Attr("user_id").eq(user_id)
                    & Attr("meeting_id").eq(meeting_id)
                    & Attr("meeting_timestamp").eq(meeting_timestamp)
                )
            )

        results = [
            deepcopy(record)
            for record in records
            if str(record.get("user_id") or "") == user_id
            and str(record.get("meeting_id") or "") == meeting_id
            and str(record.get("meeting_timestamp") or "") == meeting_timestamp
        ]
        return sorted(
            results,
            key=lambda record: str(record.get("created_at") or ""),
            reverse=True,
        )

    def update_owned(
        self,
        user_id: str,
        share_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        if self.backend == "memory":
            with _MEMORY_LOCK:
                record = _MEMORY_RECORDS.get(share_id)
                if not record or str(record.get("user_id") or "") != user_id:
                    raise ResourceNotFoundError("Share link not found.")
                record.update(deepcopy(fields))
                return deepcopy(record)
        if self.backend == "local":
            with _LOCAL_LOCK:
                records = self._read_local()
                record = records.get(share_id)
                if not record or str(record.get("user_id") or "") != user_id:
                    raise ResourceNotFoundError("Share link not found.")
                record.update(deepcopy(fields))
                records[share_id] = record
                self._write_local(records)
                return deepcopy(record)

        names = {"#owner": "user_id"}
        values: dict[str, Any] = {":owner": user_id}
        assignments: list[str] = []
        for index, (field, value) in enumerate(fields.items()):
            name_token = f"#field_{index}"
            value_token = f":value_{index}"
            names[name_token] = field
            values[value_token] = _for_dynamodb(value)
            assignments.append(f"{name_token} = {value_token}")

        try:
            response = self._table().update_item(
                Key={"share_id": share_id},
                UpdateExpression="SET " + ", ".join(assignments),
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ResourceNotFoundError("Share link not found.") from exc
            raise
        return to_json_compatible(response.get("Attributes", {}))

    def record_access(self, share_id: str, accessed_at: str) -> dict[str, Any] | None:
        if self.backend == "memory":
            with _MEMORY_LOCK:
                record = _MEMORY_RECORDS.get(share_id)
                if not record:
                    return None
                record["access_count"] = int(record.get("access_count") or 0) + 1
                record["last_accessed_at"] = accessed_at
                return deepcopy(record)
        if self.backend == "local":
            with _LOCAL_LOCK:
                records = self._read_local()
                record = records.get(share_id)
                if not record:
                    return None
                record["access_count"] = int(record.get("access_count") or 0) + 1
                record["last_accessed_at"] = accessed_at
                records[share_id] = record
                self._write_local(records)
                return deepcopy(record)

        response = self._table().update_item(
            Key={"share_id": share_id},
            UpdateExpression=(
                "SET last_accessed_at = :accessed_at "
                "ADD access_count :one"
            ),
            ExpressionAttributeValues={
                ":accessed_at": accessed_at,
                ":one": 1,
            },
            ReturnValues="ALL_NEW",
        )
        return to_json_compatible(response.get("Attributes", {}))

    def clear_memory(self) -> None:
        if self.backend == "memory":
            with _MEMORY_LOCK:
                _MEMORY_RECORDS.clear()

    def _table(self):
        return self.table("MEETING_SHARES_TABLE_NAME")

    def _scan_all(self, **kwargs) -> list[dict[str, Any]]:
        table = self._table()
        items: list[dict[str, Any]] = []
        scan_args = dict(kwargs)
        while True:
            response = table.scan(**scan_args)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_args["ExclusiveStartKey"] = last_key
        return [to_json_compatible(item) for item in items]

    def _local_path(self) -> Path:
        configured = Path(
            str(
                current_app.config.get(
                    "MEETING_SHARES_LOCAL_PATH",
                    "instance/meeting_shares.json",
                )
            )
        )
        if configured.is_absolute():
            return configured
        return Path(current_app.root_path).parent / configured

    def _read_local(self) -> dict[str, dict[str, Any]]:
        path = self._local_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_local(self, records: dict[str, dict[str, Any]]) -> None:
        path = self._local_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)


def _for_dynamodb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _for_dynamodb(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_for_dynamodb(item) for item in value]
    return value
