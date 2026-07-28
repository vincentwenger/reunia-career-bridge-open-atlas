from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from meeting_assistant.repositories.base import DynamoRepository


class LiveQARepository(Protocol):
    def create(self, entry: dict[str, Any], ttl_seconds: int) -> None: ...
    def update_answer(self, user_id: str, entry_id: str, answer: str, ttl_seconds: int) -> None: ...
    def list_for_user(
        self,
        user_id: str,
        max_cache_age_seconds: float | None = None,
    ) -> list[dict[str, Any]]: ...
    def cleanup(self) -> None: ...


class InMemoryLiveQARepository:
    """Thread-safe development/test store.

    This backend is intentionally process-local and must not be used for a
    multi-worker or redeployable production service.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create(self, entry: dict[str, Any], ttl_seconds: int) -> None:
        stored = dict(entry)
        stored["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._entries[(entry["user_id"], entry["id"])] = stored

    def update_answer(self, user_id: str, entry_id: str, answer: str, ttl_seconds: int) -> None:
        with self._lock:
            entry = self._entries.get((user_id, entry_id))
            if entry:
                entry["chatgpt_answer"] = answer
                entry["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    def list_for_user(
        self,
        user_id: str,
        max_cache_age_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        self.cleanup()
        with self._lock:
            items = [
                {key: value for key, value in entry.items() if key != "expires_at"}
                for (owner, _), entry in self._entries.items()
                if owner == user_id
            ]
        return sorted(items, key=lambda item: item.get("timestamp", ""))

    def cleanup(self) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            expired = [key for key, entry in self._entries.items() if entry["expires_at"] <= now]
            for key in expired:
                self._entries.pop(key, None)


class DynamoLiveQARepository(DynamoRepository):
    """Persistent Live Q&A storage backed by DynamoDB.

    Required table schema:
      * partition key: ``user_id`` (String)
      * sort key: ``entry_id`` (String)
      * TTL attribute: ``expires_at`` (Number, Unix epoch seconds)

    DynamoDB TTL removes expired items asynchronously. ``list_for_user`` also
    filters them immediately so an expired feed entry is never displayed while
    DynamoDB is waiting to perform the physical deletion.
    """

    def __init__(self, cache_ttl_seconds: float = 2.0) -> None:
        # Cache the latest per-user feed inside each Gunicorn worker. DynamoDB
        # remains authoritative, while concurrent browser tabs handled by the
        # same worker reuse one query result instead of querying independently.
        self._cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._cache_lock = threading.RLock()
        self._query_locks: dict[str, threading.Lock] = {}

    def _table(self):
        return self.table("LIVE_QA_TABLE_NAME")

    @staticmethod
    def _expiry(ttl_seconds: int) -> int:
        return int(time.time()) + max(1, int(ttl_seconds))

    def _get_cached(
        self,
        user_id: str,
        max_cache_age_seconds: float | None = None,
    ) -> list[dict[str, Any]] | None:
        if self._cache_ttl_seconds <= 0:
            return None
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(user_id)
            if not cached:
                return None
            expires_at, items = cached
            cached_at = expires_at - self._cache_ttl_seconds
            cache_too_old_for_caller = (
                max_cache_age_seconds is not None
                and now - cached_at >= max(0.0, float(max_cache_age_seconds))
            )
            if expires_at <= now or cache_too_old_for_caller:
                self._cache.pop(user_id, None)
                return None
            return copy.deepcopy(items)

    def _store_cached(self, user_id: str, items: list[dict[str, Any]]) -> None:
        if self._cache_ttl_seconds <= 0:
            return
        with self._cache_lock:
            self._cache[user_id] = (
                time.monotonic() + self._cache_ttl_seconds,
                copy.deepcopy(items),
            )

    def _query_lock(self, user_id: str) -> threading.Lock:
        with self._cache_lock:
            return self._query_locks.setdefault(user_id, threading.Lock())

    def _upsert_cached_item(self, item: dict[str, Any]) -> None:
        user_id = str(item.get("user_id") or "")
        if not user_id or self._cache_ttl_seconds <= 0:
            return
        with self._cache_lock:
            cached = self._cache.get(user_id)
            if not cached or cached[0] <= time.monotonic():
                return
            items = copy.deepcopy(cached[1])
            entry_id = str(item.get("entry_id") or item.get("id") or "")
            replacement = copy.deepcopy(item)
            replacement.setdefault("id", entry_id)
            for index, existing in enumerate(items):
                existing_id = str(existing.get("entry_id") or existing.get("id") or "")
                if existing_id == entry_id:
                    items[index] = replacement
                    break
            else:
                items.append(replacement)
            items.sort(key=lambda value: value.get("timestamp", ""))
            self._cache[user_id] = (cached[0], items)

    def _update_cached_answer(
        self,
        user_id: str,
        entry_id: str,
        answer: str,
        expires_at: int,
    ) -> None:
        if self._cache_ttl_seconds <= 0:
            return
        with self._cache_lock:
            cached = self._cache.get(user_id)
            if not cached or cached[0] <= time.monotonic():
                return
            items = copy.deepcopy(cached[1])
            for item in items:
                stored_id = str(item.get("entry_id") or item.get("id") or "")
                if stored_id == entry_id:
                    item["chatgpt_answer"] = answer
                    item["expires_at"] = expires_at
                    break
            self._cache[user_id] = (cached[0], items)

    def create(self, entry: dict[str, Any], ttl_seconds: int) -> None:
        item = dict(entry)
        item["entry_id"] = str(entry["id"])
        item["expires_at"] = self._expiry(ttl_seconds)

        try:
            self._table().put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(#entry_id)",
                ExpressionAttributeNames={"#entry_id": "entry_id"},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                # Browser live chunks use deterministic entry IDs so a network retry
                # updates the same feed item instead of creating a duplicate.
                return
            raise

        self._upsert_cached_item(item)

    def update_answer(self, user_id: str, entry_id: str, answer: str, ttl_seconds: int) -> None:
        expires_at = self._expiry(ttl_seconds)
        try:
            self._table().update_item(
                Key={"user_id": user_id, "entry_id": entry_id},
                UpdateExpression=(
                    "SET chatgpt_answer = :answer, expires_at = :expires_at"
                ),
                ExpressionAttributeValues={
                    ":answer": answer,
                    ":expires_at": expires_at,
                },
                ConditionExpression=(
                    "attribute_exists(user_id) AND attribute_exists(entry_id)"
                ),
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                # The item may have been manually deleted or expired while a stream
                # was still open. Do not recreate a partial record.
                return
            raise

        self._update_cached_answer(user_id, entry_id, answer, expires_at)

    def list_for_user(
        self,
        user_id: str,
        max_cache_age_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        cached = self._get_cached(user_id, max_cache_age_seconds)
        if cached is not None:
            return cached

        # Prevent a cache stampede when several open Live Q&A tabs for the same
        # user refresh at nearly the same time. Other users can still query in parallel.
        with self._query_lock(user_id):
            cached = self._get_cached(user_id, max_cache_age_seconds)
            if cached is not None:
                return cached

            query_args: dict[str, Any] = {
                "KeyConditionExpression": Key("user_id").eq(user_id),
                # Live Q&A tolerates a brief propagation delay. Eventually consistent
                # reads cost less and avoid unnecessary coordination in DynamoDB.
                "ConsistentRead": False,
                "ProjectionExpression": (
                    "#user_id, #entry_id, #id, #origin, #content, #answer, "
                    "#timestamp, #answer_source, #answer_origin, #meeting_id, "
                    "#meeting_title, #expires_at"
                ),
                "ExpressionAttributeNames": {
                    "#user_id": "user_id",
                    "#entry_id": "entry_id",
                    "#id": "id",
                    "#origin": "origin",
                    "#content": "content",
                    "#answer": "chatgpt_answer",
                    "#timestamp": "timestamp",
                    "#answer_source": "answer_source",
                    "#answer_origin": "answer_origin",
                    "#meeting_id": "meeting_id",
                    "#meeting_title": "meeting_title",
                    "#expires_at": "expires_at",
                },
            }
            items: list[dict[str, Any]] = []

            while True:
                response = self._table().query(**query_args)
                items.extend(response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                query_args["ExclusiveStartKey"] = last_key

            now = int(time.time())
            active_items: list[dict[str, Any]] = []
            for stored_item in items:
                try:
                    expires_at = int(stored_item.get("expires_at", 0))
                except (TypeError, ValueError):
                    expires_at = 0

                if expires_at <= now:
                    continue

                item = dict(stored_item)
                item.setdefault("id", str(item.get("entry_id") or ""))
                active_items.append(item)

            active_items.sort(key=lambda item: item.get("timestamp", ""))
            self._store_cached(user_id, active_items)
            return copy.deepcopy(active_items)

    def cleanup(self) -> None:
        # DynamoDB TTL performs physical cleanup. Expired records are filtered
        # synchronously by list_for_user so the UI observes retention immediately.
        return None


class RedisLiveQARepository:
    def __init__(self, redis_url: str, redis_client=None) -> None:
        try:
            import redis
        except ImportError as exc:
            raise ImportError("Install the 'redis' package to use Redis storage.") from exc

        if not redis_url:
            raise ValueError("REDIS_URL is required for Redis Live Q&A storage.")

        self._redis = redis_client or redis.Redis.from_url(redis_url, decode_responses=True)
        self._prefix = "meeting_assistant:live_qa"

    def _key(self, user_id: str, entry_id: str) -> str:
        return f"{self._prefix}:{user_id}:{entry_id}"

    def create(self, entry: dict[str, Any], ttl_seconds: int) -> None:
        self._redis.setex(
            self._key(entry["user_id"], entry["id"]),
            ttl_seconds,
            json.dumps(entry),
        )

    def update_answer(self, user_id: str, entry_id: str, answer: str, ttl_seconds: int) -> None:
        key = self._key(user_id, entry_id)
        raw = self._redis.get(key)
        if not raw:
            return
        entry = json.loads(raw)
        entry["chatgpt_answer"] = answer
        self._redis.setex(key, ttl_seconds, json.dumps(entry))

    def list_for_user(
        self,
        user_id: str,
        max_cache_age_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        items = []
        for key in self._redis.scan_iter(match=f"{self._prefix}:{user_id}:*"):
            raw = self._redis.get(key)
            if raw:
                items.append(json.loads(raw))
        return sorted(items, key=lambda item: item.get("timestamp", ""))

    def cleanup(self) -> None:
        # Redis automatically removes entries after their TTL expires.
        return None
