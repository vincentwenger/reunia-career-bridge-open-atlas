from __future__ import annotations

import hashlib
import json
import threading
import time
from copy import deepcopy
from typing import Any


class MemoryRateLimiter:
    """Small process-local limiter used only for development and tests."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[int, float]] = {}
        self._lock = threading.RLock()

    def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.time()
        normalized = _safe_key(key)
        with self._lock:
            count, expires_at = self._items.get(normalized, (0, now + window_seconds))
            if expires_at <= now:
                count, expires_at = 0, now + window_seconds
            count += 1
            self._items[normalized] = (count, expires_at)
        return count <= limit, max(1, int(expires_at - now))


class RedisRateLimiter:
    """Atomic fixed-window limiter shared by all web and worker processes."""

    def __init__(self, redis_client, *, prefix: str = "reunia:rate-limit") -> None:
        self._redis = redis_client
        self._prefix = prefix.rstrip(":")

    def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        redis_key = f"{self._prefix}:{_safe_key(key)}"
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.incr(redis_key)
        pipeline.ttl(redis_key)
        count, ttl = pipeline.execute()
        count = int(count or 0)
        ttl = int(ttl or -1)
        if count == 1 or ttl < 0:
            self._redis.expire(redis_key, int(window_seconds))
            ttl = int(window_seconds)
        return count <= int(limit), max(1, ttl)


class MemoryTTLCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            value = self._items.get(key)
            if not value:
                return None
            expires_at, payload = value
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            return deepcopy(payload)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        with self._lock:
            self._items[key] = (time.time() + int(ttl_seconds), deepcopy(value))

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in list(self._items):
                if key.startswith(prefix):
                    self._items.pop(key, None)


class RedisTTLCache:
    def __init__(self, redis_client, *, prefix: str = "reunia:cache") -> None:
        self._redis = redis_client
        self._prefix = prefix.rstrip(":")

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{_safe_key(key)}"

    def get(self, key: str) -> Any | None:
        raw = self._redis.get(self._key(key))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._redis.setex(
            self._key(key),
            int(ttl_seconds),
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        )

    def delete_prefix(self, prefix: str) -> None:
        pattern = self._key(prefix) + "*"
        keys = list(self._redis.scan_iter(match=pattern, count=200))
        if keys:
            self._redis.delete(*keys)


def _safe_key(value: str) -> str:
    text = str(value or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
