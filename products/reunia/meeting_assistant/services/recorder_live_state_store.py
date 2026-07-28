from __future__ import annotations

import hashlib
import threading
import time
from typing import Protocol


class RecorderLiveStateStore(Protocol):
    def cancel(self, user_id: str, recording_id: str, ttl_seconds: int) -> None: ...
    def is_cancelled(self, user_id: str, recording_id: str) -> bool: ...
    def get_context(self, user_id: str, recording_id: str, source: str) -> str: ...
    def set_context(self, user_id: str, recording_id: str, source: str, context: str, ttl_seconds: int) -> None: ...
    def reserve_question(self, user_id: str, recording_id: str, source: str, question_key: str, ttl_seconds: int) -> bool: ...
    def release_question(self, user_id: str, recording_id: str, source: str, question_key: str) -> None: ...
    def remove_recording(self, user_id: str, recording_id: str) -> None: ...


class MemoryRecorderLiveStateStore:
    """Testing/development implementation. Production uses Redis."""

    def __init__(self) -> None:
        self._cancelled: dict[str, float] = {}
        self._contexts: dict[str, tuple[str, float]] = {}
        self._questions: dict[str, float] = {}
        self._lock = threading.RLock()

    def cancel(self, user_id: str, recording_id: str, ttl_seconds: int) -> None:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            self._cancelled[_recording_key(user_id, recording_id)] = now + ttl_seconds
            self._remove_recording_locked(user_id, recording_id)

    def is_cancelled(self, user_id: str, recording_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            return _recording_key(user_id, recording_id) in self._cancelled

    def get_context(self, user_id: str, recording_id: str, source: str) -> str:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            return str(self._contexts.get(_source_key(user_id, recording_id, source), ("", 0))[0])

    def set_context(self, user_id: str, recording_id: str, source: str, context: str, ttl_seconds: int) -> None:
        with self._lock:
            self._contexts[_source_key(user_id, recording_id, source)] = (
                str(context or ""),
                time.time() + ttl_seconds,
            )

    def reserve_question(self, user_id: str, recording_id: str, source: str, question_key: str, ttl_seconds: int) -> bool:
        now = time.time()
        key = _question_key(user_id, recording_id, source, question_key)
        with self._lock:
            self._cleanup(now)
            if key in self._questions:
                return False
            self._questions[key] = now + ttl_seconds
            return True

    def release_question(self, user_id: str, recording_id: str, source: str, question_key: str) -> None:
        with self._lock:
            self._questions.pop(_question_key(user_id, recording_id, source, question_key), None)

    def remove_recording(self, user_id: str, recording_id: str) -> None:
        with self._lock:
            self._remove_recording_locked(user_id, recording_id)

    def _remove_recording_locked(self, user_id: str, recording_id: str) -> None:
        marker = _recording_key(user_id, recording_id)
        for collection in (self._contexts, self._questions):
            for key in list(collection):
                if key.startswith(marker + ":"):
                    collection.pop(key, None)

    def _cleanup(self, now: float) -> None:
        for collection in (self._cancelled, self._questions):
            for key, expires_at in list(collection.items()):
                if expires_at <= now:
                    collection.pop(key, None)
        for key, (_, expires_at) in list(self._contexts.items()):
            if expires_at <= now:
                self._contexts.pop(key, None)


class RedisRecorderLiveStateStore:
    """Cross-worker recorder state with atomic question reservation."""

    def __init__(self, redis_client, *, prefix: str = "reunia:recorder-live") -> None:
        self._redis = redis_client
        self._prefix = prefix.rstrip(":")

    def cancel(self, user_id: str, recording_id: str, ttl_seconds: int) -> None:
        self._redis.setex(self._cancel_key(user_id, recording_id), int(ttl_seconds), "1")
        self.remove_recording(user_id, recording_id)

    def is_cancelled(self, user_id: str, recording_id: str) -> bool:
        return bool(self._redis.exists(self._cancel_key(user_id, recording_id)))

    def get_context(self, user_id: str, recording_id: str, source: str) -> str:
        return str(self._redis.get(self._context_key(user_id, recording_id, source)) or "")

    def set_context(self, user_id: str, recording_id: str, source: str, context: str, ttl_seconds: int) -> None:
        self._redis.setex(
            self._context_key(user_id, recording_id, source),
            int(ttl_seconds),
            str(context or ""),
        )

    def reserve_question(self, user_id: str, recording_id: str, source: str, question_key: str, ttl_seconds: int) -> bool:
        return bool(
            self._redis.set(
                self._question_key(user_id, recording_id, source, question_key),
                "1",
                nx=True,
                ex=int(ttl_seconds),
            )
        )

    def release_question(self, user_id: str, recording_id: str, source: str, question_key: str) -> None:
        self._redis.delete(self._question_key(user_id, recording_id, source, question_key))

    def remove_recording(self, user_id: str, recording_id: str) -> None:
        marker = self._recording_digest(user_id, recording_id)
        pattern = f"{self._prefix}:*:{marker}:*"
        keys = list(self._redis.scan_iter(match=pattern, count=200))
        if keys:
            self._redis.delete(*keys)

    def _recording_digest(self, user_id: str, recording_id: str) -> str:
        return hashlib.sha256(f"{user_id}\x1f{recording_id}".encode("utf-8")).hexdigest()

    def _cancel_key(self, user_id: str, recording_id: str) -> str:
        return f"{self._prefix}:cancel:{self._recording_digest(user_id, recording_id)}"

    def _context_key(self, user_id: str, recording_id: str, source: str) -> str:
        return f"{self._prefix}:context:{self._recording_digest(user_id, recording_id)}:{source}"

    def _question_key(self, user_id: str, recording_id: str, source: str, question_key: str) -> str:
        digest = hashlib.sha256(str(question_key).encode("utf-8")).hexdigest()
        return f"{self._prefix}:question:{self._recording_digest(user_id, recording_id)}:{source}:{digest}"


def _recording_key(user_id: str, recording_id: str) -> str:
    return hashlib.sha256(f"{user_id}\x1f{recording_id}".encode("utf-8")).hexdigest()


def _source_key(user_id: str, recording_id: str, source: str) -> str:
    return f"{_recording_key(user_id, recording_id)}:{source}"


def _question_key(user_id: str, recording_id: str, source: str, question_key: str) -> str:
    digest = hashlib.sha256(str(question_key).encode("utf-8")).hexdigest()
    return f"{_source_key(user_id, recording_id, source)}:{digest}"
