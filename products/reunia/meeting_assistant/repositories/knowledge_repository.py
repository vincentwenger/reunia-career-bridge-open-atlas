from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from meeting_assistant.repositories.base import DynamoRepository
from meeting_assistant.utils.exceptions import ResourceNotFoundError


class KnowledgeRepository(Protocol):
    def list_collections(self, user_id: str) -> list[dict[str, Any]]: ...

    def get_collection(self, user_id: str, collection_id: str) -> dict[str, Any] | None: ...

    def create_collection(self, item: dict[str, Any]) -> None: ...

    def delete_collection(self, user_id: str, collection_id: str) -> dict[str, Any]: ...

    def list_files(self, user_id: str) -> list[dict[str, Any]]: ...

    def list_all_files(self) -> list[dict[str, Any]]: ...

    def get_file(self, user_id: str, file_id: str) -> dict[str, Any] | None: ...

    def create_file(self, item: dict[str, Any]) -> None: ...

    def delete_file(self, user_id: str, file_id: str) -> dict[str, Any]: ...

    def list_meetings(self, user_id: str) -> list[dict[str, Any]]: ...

    def get_meeting(self, user_id: str, meeting_id: str) -> dict[str, Any] | None: ...

    def upsert_meeting(self, item: dict[str, Any]) -> None: ...

    def delete_meeting(self, user_id: str, meeting_id: str) -> dict[str, Any]: ...

    def get_active_meeting_id(self, user_id: str) -> str: ...

    def set_active_meeting_id(self, user_id: str, meeting_id: str) -> None: ...


class InMemoryKnowledgeRepository:
    """Thread-safe knowledge metadata storage for tests."""

    def __init__(self) -> None:
        self._collections: dict[tuple[str, str], dict[str, Any]] = {}
        self._files: dict[tuple[str, str], dict[str, Any]] = {}
        self._meetings: dict[tuple[str, str], dict[str, Any]] = {}
        self._active_meetings: dict[str, str] = {}
        self._lock = threading.RLock()

    def list_collections(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(
                [
                    item
                    for (stored_user_id, _), item in self._collections.items()
                    if stored_user_id == user_id
                ]
            )

    def get_collection(self, user_id: str, collection_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._collections.get((user_id, collection_id))
            return deepcopy(item) if item is not None else None

    def create_collection(self, item: dict[str, Any]) -> None:
        key = (str(item["user_id"]), str(item["collection_id"]))
        with self._lock:
            if key in self._collections:
                raise _duplicate_error("Collection already exists.")
            self._collections[key] = deepcopy(item)

    def delete_collection(self, user_id: str, collection_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._collections.pop((user_id, collection_id), None)
            if item is None:
                raise ResourceNotFoundError("Collection not found.")
            return deepcopy(item)

    def list_files(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(
                [
                    item
                    for (stored_user_id, _), item in self._files.items()
                    if stored_user_id == user_id
                ]
            )

    def list_all_files(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._files.values()))

    def get_file(self, user_id: str, file_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._files.get((user_id, file_id))
            return deepcopy(item) if item is not None else None

    def create_file(self, item: dict[str, Any]) -> None:
        key = (str(item["user_id"]), str(item["file_id"]))
        with self._lock:
            if key in self._files:
                raise _duplicate_error("Document already exists.")
            self._files[key] = deepcopy(item)

    def delete_file(self, user_id: str, file_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._files.pop((user_id, file_id), None)
            if item is None:
                raise ResourceNotFoundError("Document not found.")
            return deepcopy(item)

    def list_meetings(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy([item for (owner, _), item in self._meetings.items() if owner == user_id])

    def get_meeting(self, user_id: str, meeting_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._meetings.get((user_id, meeting_id))
            return deepcopy(item) if item is not None else None

    def upsert_meeting(self, item: dict[str, Any]) -> None:
        with self._lock:
            self._meetings[(str(item["user_id"]), str(item["meeting_id"]))] = deepcopy(item)

    def delete_meeting(self, user_id: str, meeting_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._meetings.pop((user_id, meeting_id), None)
            if item is None:
                raise ResourceNotFoundError("Meeting package not found.")
            return deepcopy(item)

    def get_active_meeting_id(self, user_id: str) -> str:
        with self._lock:
            return self._active_meetings.get(user_id, "")

    def set_active_meeting_id(self, user_id: str, meeting_id: str) -> None:
        with self._lock:
            self._active_meetings[user_id] = str(meeting_id or "")


class LocalKnowledgeRepository:
    """Small JSON metadata repository for local development.

    This backend deliberately targets a single local Flask process. Production
    deployments should use DynamoDB so metadata is shared across workers and
    survives container replacement.
    """

    def __init__(self, metadata_path: str | Path) -> None:
        self._path = Path(metadata_path).expanduser().resolve()
        self._lock = threading.RLock()

    def list_collections(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read()
            return deepcopy(
                [item for item in data["collections"] if item.get("user_id") == user_id]
            )

    def get_collection(self, user_id: str, collection_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._read()["collections"]:
                if item.get("user_id") == user_id and item.get("collection_id") == collection_id:
                    return deepcopy(item)
        return None

    def create_collection(self, item: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            if any(
                existing.get("user_id") == item.get("user_id")
                and existing.get("collection_id") == item.get("collection_id")
                for existing in data["collections"]
            ):
                raise _duplicate_error("Collection already exists.")
            data["collections"].append(deepcopy(item))
            self._write(data)

    def delete_collection(self, user_id: str, collection_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            for index, item in enumerate(data["collections"]):
                if item.get("user_id") == user_id and item.get("collection_id") == collection_id:
                    deleted = data["collections"].pop(index)
                    self._write(data)
                    return deepcopy(deleted)
        raise ResourceNotFoundError("Collection not found.")

    def list_files(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read()
            return deepcopy([item for item in data["files"] if item.get("user_id") == user_id])

    def list_all_files(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._read()["files"])

    def get_file(self, user_id: str, file_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._read()["files"]:
                if item.get("user_id") == user_id and item.get("file_id") == file_id:
                    return deepcopy(item)
        return None

    def create_file(self, item: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            if any(
                existing.get("user_id") == item.get("user_id")
                and existing.get("file_id") == item.get("file_id")
                for existing in data["files"]
            ):
                raise _duplicate_error("Document already exists.")
            data["files"].append(deepcopy(item))
            self._write(data)

    def delete_file(self, user_id: str, file_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            for index, item in enumerate(data["files"]):
                if item.get("user_id") == user_id and item.get("file_id") == file_id:
                    deleted = data["files"].pop(index)
                    self._write(data)
                    return deepcopy(deleted)
        raise ResourceNotFoundError("Document not found.")

    def list_meetings(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy([item for item in self._read()["meetings"] if item.get("user_id") == user_id])

    def get_meeting(self, user_id: str, meeting_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._read()["meetings"]:
                if item.get("user_id") == user_id and item.get("meeting_id") == meeting_id:
                    return deepcopy(item)
        return None

    def upsert_meeting(self, item: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            for index, existing in enumerate(data["meetings"]):
                if existing.get("user_id") == item.get("user_id") and existing.get("meeting_id") == item.get("meeting_id"):
                    data["meetings"][index] = deepcopy(item)
                    self._write(data)
                    return
            data["meetings"].append(deepcopy(item))
            self._write(data)

    def delete_meeting(self, user_id: str, meeting_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            for index, item in enumerate(data["meetings"]):
                if item.get("user_id") == user_id and item.get("meeting_id") == meeting_id:
                    deleted = data["meetings"].pop(index)
                    self._write(data)
                    return deepcopy(deleted)
        raise ResourceNotFoundError("Meeting package not found.")

    def get_active_meeting_id(self, user_id: str) -> str:
        with self._lock:
            return str(self._read()["active_meetings"].get(user_id) or "")

    def set_active_meeting_id(self, user_id: str, meeting_id: str) -> None:
        with self._lock:
            data = self._read()
            data["active_meetings"][user_id] = str(meeting_id or "")
            self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"collections": [], "files": [], "meetings": [], "active_meetings": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError:
            raise
        except json.JSONDecodeError as exc:
            raise OSError(f"Knowledge metadata file is invalid: {self._path}") from exc
        return {
            "collections": raw.get("collections", []) if isinstance(raw, dict) else [],
            "files": raw.get("files", []) if isinstance(raw, dict) else [],
            "meetings": raw.get("meetings", []) if isinstance(raw, dict) else [],
            "active_meetings": raw.get("active_meetings", {}) if isinstance(raw, dict) else {},
        }

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary_path.replace(self._path)


class DynamoKnowledgeRepository(DynamoRepository):
    """DynamoDB metadata storage for Document Library.

    The table uses `user_id` (String) as partition key and `item_id` (String) as
    sort key. Collection records use `collection#<id>` and file records use
    `file#<id>`.
    """

    def _table(self):
        return self.table("KNOWLEDGE_TABLE_NAME")

    def list_collections(self, user_id: str) -> list[dict[str, Any]]:
        return self._query_prefix(user_id, "collection#")

    def get_collection(self, user_id: str, collection_id: str) -> dict[str, Any] | None:
        return self._get(user_id, f"collection#{collection_id}")

    def create_collection(self, item: dict[str, Any]) -> None:
        self._create(item)

    def delete_collection(self, user_id: str, collection_id: str) -> dict[str, Any]:
        response = self._table().delete_item(
            Key={"user_id": user_id, "item_id": f"collection#{collection_id}"},
            ReturnValues="ALL_OLD",
        )
        item = response.get("Attributes")
        if not item:
            raise ResourceNotFoundError("Collection not found.")
        return item

    def list_files(self, user_id: str) -> list[dict[str, Any]]:
        return self._query_prefix(user_id, "file#")

    def list_all_files(self) -> list[dict[str, Any]]:
        args: dict[str, Any] = {
            "FilterExpression": Attr("entity_type").eq("file"),
            "ProjectionExpression": (
                "#user_id, #file_id, #filename, #display_name, #extension, "
                "#collection_id, #size_bytes, #created_at"
            ),
            "ExpressionAttributeNames": {
                "#user_id": "user_id",
                "#file_id": "file_id",
                "#filename": "filename",
                "#display_name": "display_name",
                "#extension": "extension",
                "#collection_id": "collection_id",
                "#size_bytes": "size_bytes",
                "#created_at": "created_at",
            },
        }
        items: list[dict[str, Any]] = []
        while True:
            response = self._table().scan(**args)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            args["ExclusiveStartKey"] = last_key

    def get_file(self, user_id: str, file_id: str) -> dict[str, Any] | None:
        return self._get(user_id, f"file#{file_id}")

    def create_file(self, item: dict[str, Any]) -> None:
        self._create(item)

    def delete_file(self, user_id: str, file_id: str) -> dict[str, Any]:
        response = self._table().delete_item(
            Key={"user_id": user_id, "item_id": f"file#{file_id}"},
            ReturnValues="ALL_OLD",
        )
        item = response.get("Attributes")
        if not item:
            raise ResourceNotFoundError("Document not found.")
        return item

    def list_meetings(self, user_id: str) -> list[dict[str, Any]]:
        return self._query_prefix(user_id, "meeting#")

    def get_meeting(self, user_id: str, meeting_id: str) -> dict[str, Any] | None:
        return self._get(user_id, f"meeting#{meeting_id}")

    def upsert_meeting(self, item: dict[str, Any]) -> None:
        self._table().put_item(Item=item)

    def delete_meeting(self, user_id: str, meeting_id: str) -> dict[str, Any]:
        response = self._table().delete_item(
            Key={"user_id": user_id, "item_id": f"meeting#{meeting_id}"},
            ReturnValues="ALL_OLD",
        )
        item = response.get("Attributes")
        if not item:
            raise ResourceNotFoundError("Meeting package not found.")
        return item

    def get_active_meeting_id(self, user_id: str) -> str:
        item = self._get(user_id, "state#active_meeting") or {}
        return str(item.get("meeting_id") or "")

    def set_active_meeting_id(self, user_id: str, meeting_id: str) -> None:
        self._table().put_item(Item={
            "user_id": user_id,
            "item_id": "state#active_meeting",
            "entity_type": "active_meeting",
            "meeting_id": str(meeting_id or ""),
        })

    def _query_prefix(self, user_id: str, prefix: str) -> list[dict[str, Any]]:
        args: dict[str, Any] = {
            "KeyConditionExpression": Key("user_id").eq(user_id)
            & Key("item_id").begins_with(prefix),
        }
        items: list[dict[str, Any]] = []
        while True:
            response = self._table().query(**args)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            args["ExclusiveStartKey"] = last_key

    def _get(self, user_id: str, item_id: str) -> dict[str, Any] | None:
        response = self._table().get_item(
            Key={"user_id": user_id, "item_id": item_id},
            ConsistentRead=True,
        )
        return response.get("Item")

    def _create(self, item: dict[str, Any]) -> None:
        self._table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(#item_id)",
            ExpressionAttributeNames={"#item_id": "item_id"},
        )


def _duplicate_error(message: str) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": message,
            }
        },
        "PutItem",
    )
