from __future__ import annotations

import hashlib
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import current_app

from meeting_assistant.utils.exceptions import RateLimitError

_MICRODOLLARS_PER_DOLLAR = 1_000_000
_MEMORY_LOCK = threading.RLock()
_MEMORY_COUNTERS: dict[str, tuple[int, float]] = {}


@dataclass
class AICostReservation:
    """Conservative budget reservation made before an OpenAI request."""

    service: "AICostControlService"
    entries: tuple[tuple[str, int, int], ...]
    reserved_cost_usd: float
    usage_entries: tuple[tuple[str, int, int], ...] = ()
    active: bool = True

    def release(self) -> None:
        if not self.active:
            return
        self.service._release_entries(self.entries + self.usage_entries)
        self.active = False

    def settle(self, actual_cost_usd: float | None) -> None:
        """Release unused reservation while retaining the actual estimated spend."""
        if not self.active:
            return
        if actual_cost_usd is None:
            self.active = False
            return
        actual_units = max(0, int(math.ceil(float(actual_cost_usd) * _MICRODOLLARS_PER_DOLLAR)))
        reserved_units = max(0, int(math.ceil(self.reserved_cost_usd * _MICRODOLLARS_PER_DOLLAR)))
        unused_units = max(0, reserved_units - actual_units)
        if unused_units:
            adjusted = tuple((key, min(delta, unused_units), ttl) for key, delta, ttl in self.entries)
            self.service._release_entries(adjusted)
        self.active = False


def raise_if_openai_limited(error: Exception) -> None:
    """Translate provider quota/rate-limit failures into a non-retryable HTTP 429."""
    status_code = getattr(error, "status_code", None)
    code = ""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error") if isinstance(body.get("error"), dict) else body
        code = str(nested.get("code") or nested.get("type") or "").lower()
    text = f"{error} {code}".lower()
    if status_code != 429 and not any(
        marker in text
        for marker in ("insufficient_quota", "rate_limit_exceeded", "exceeded your current quota")
    ):
        return
    if "insufficient_quota" in text or "current quota" in text:
        message = (
            "The OpenAI project has no remaining API quota. Add billing credit or "
            "increase the OpenAI project budget before retrying."
        )
    else:
        message = (
            "OpenAI temporarily rate-limited this request. Wait before retrying or "
            "lower the configured Réunia request rate."
        )
    raise RateLimitError(message) from error


class AICostControlService:
    """Applies application-level OpenAI cost and usage ceilings.

    Production uses Redis when available so limits are shared across web and worker
    processes. Development and tests use a process-local fallback.
    """

    def ensure_enabled(self) -> None:
        if not bool(current_app.config.get("AI_ENABLED", True)):
            raise RateLimitError(
                "AI features are temporarily disabled by the Réunia spending safeguard."
            )

    def reserve_text_request(
        self,
        user_id: str,
        *,
        feature: str,
        model: str,
        prompt_characters: int,
        max_output_tokens: int,
    ) -> AICostReservation:
        self.ensure_enabled()
        estimated_input_tokens = max(1, int(math.ceil(max(0, prompt_characters) / 4)))
        estimated_cost = self.text_cost_usd(
            model,
            input_tokens=estimated_input_tokens,
            output_tokens=max(1, int(max_output_tokens or 1)),
        )
        if estimated_cost is None:
            estimated_cost = float(
                current_app.config.get("AI_UNPRICED_TEXT_REQUEST_RESERVE_USD", 0.05)
                or 0.05
            )
        return self._reserve_cost(user_id, feature=feature, estimated_cost_usd=estimated_cost)

    def reserve_transcription_request(
        self,
        user_id: str,
        *,
        feature: str,
        model: str,
        audio_seconds: float,
    ) -> AICostReservation:
        self.ensure_enabled()
        seconds = max(1.0, float(audio_seconds or 0))
        transcription_limit_minutes = float(
            current_app.config.get("AI_USER_DAILY_TRANSCRIPTION_MINUTES", 180) or 0
        )
        minute_units = int(math.ceil(seconds))
        transcription_entry_with_limit: tuple[str, int, int, int] | None = None
        if transcription_limit_minutes > 0:
            transcription_entry_with_limit = (
                self._key("transcription-seconds", self._day_key(), self._user_key(user_id)),
                minute_units,
                int(transcription_limit_minutes * 60),
                self._seconds_until_tomorrow(),
            )
            self._reserve_entries(
                (transcription_entry_with_limit,),
                message=(
                    "Your daily transcription allowance has been reached. "
                    "Try again tomorrow or increase AI_USER_DAILY_TRANSCRIPTION_MINUTES."
                ),
            )

        estimated_cost = self.transcription_cost_usd(model, seconds)
        if estimated_cost is None:
            fallback_rate = float(
                current_app.config.get("AI_UNPRICED_TRANSCRIPTION_RESERVE_PER_MINUTE_USD", 0.02)
                or 0.02
            )
            estimated_cost = seconds / 60.0 * fallback_rate
        try:
            reservation = self._reserve_cost(
                user_id,
                feature=feature,
                estimated_cost_usd=estimated_cost,
            )
        except Exception:
            if transcription_entry_with_limit is not None:
                key, delta, _limit, ttl = transcription_entry_with_limit
                self._release_entries(((key, delta, ttl),))
            raise
        if transcription_entry_with_limit is not None:
            key, delta, _limit, ttl = transcription_entry_with_limit
            reservation.usage_entries = ((key, delta, ttl),)
        return reservation

    def text_cost_usd(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> float | None:
        pricing = self._model_pricing(model)
        if not pricing:
            return None
        input_tokens = max(0, int(input_tokens or 0))
        cached_input_tokens = min(input_tokens, max(0, int(cached_input_tokens or 0)))
        uncached = input_tokens - cached_input_tokens
        return (
            uncached * pricing["input"]
            + cached_input_tokens * pricing["cached_input"]
            + max(0, int(output_tokens or 0)) * pricing["output"]
        ) / 1_000_000

    def usage_cost_usd(self, model: str, usage: Any) -> float | None:
        if usage is None:
            return None
        input_tokens = self._usage_value(usage, "prompt_tokens", "input_tokens")
        output_tokens = self._usage_value(usage, "completion_tokens", "output_tokens")
        cached_tokens = self._nested_usage_value(
            usage,
            ("prompt_tokens_details", "cached_tokens"),
            ("input_tokens_details", "cached_tokens"),
        )
        return self.text_cost_usd(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
        )

    def transcription_cost_usd(self, model: str, audio_seconds: float) -> float | None:
        table = current_app.config.get("ANALYTICS_TRANSCRIPTION_MODEL_PRICING") or {}
        normalized = str(model or "").strip().lower()
        value = table.get(normalized)
        if value is None:
            for key, candidate in table.items():
                if normalized.startswith(str(key).lower()):
                    value = candidate
                    break
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, float(audio_seconds or 0)) / 60.0 * max(0.0, rate)

    def _reserve_cost(
        self,
        user_id: str,
        *,
        feature: str,
        estimated_cost_usd: float,
    ) -> AICostReservation:
        units = max(1, int(math.ceil(max(0.0, estimated_cost_usd) * _MICRODOLLARS_PER_DOLLAR)))
        day = self._day_key()
        month = day[:7]
        day_ttl = self._seconds_until_tomorrow()
        month_ttl = self._seconds_until_next_month()
        user_key = self._user_key(user_id)

        entries_with_limits: list[tuple[str, int, int, int]] = []
        global_day = self._budget_units("AI_GLOBAL_DAILY_BUDGET_USD", 10.0)
        global_month = self._budget_units("AI_GLOBAL_MONTHLY_BUDGET_USD", 100.0)
        user_day = self._budget_units("AI_USER_DAILY_BUDGET_USD", 2.0)
        if global_day > 0:
            entries_with_limits.append((self._key("cost", "day", day, "global"), units, global_day, day_ttl))
        if global_month > 0:
            entries_with_limits.append((self._key("cost", "month", month, "global"), units, global_month, month_ttl))
        if user_day > 0:
            entries_with_limits.append((self._key("cost", "day", day, "user", user_key), units, user_day, day_ttl))

        self._reserve_entries(
            tuple(entries_with_limits),
            message=(
                f"The Réunia AI spending limit was reached before starting {feature.replace('_', ' ')}. "
                "Try again after the limit resets or increase the configured AI budget."
            ),
        )
        return AICostReservation(
            service=self,
            entries=tuple((key, delta, ttl) for key, delta, _limit, ttl in entries_with_limits),
            reserved_cost_usd=units / _MICRODOLLARS_PER_DOLLAR,
        )

    def _reserve_entries(
        self,
        entries: tuple[tuple[str, int, int, int], ...],
        *,
        message: str,
    ) -> None:
        if not entries:
            return
        redis_client = current_app.extensions.get("redis_client")
        if redis_client is not None:
            script = """
            for i = 1, #KEYS do
                local base = (i - 1) * 3
                local delta = tonumber(ARGV[base + 1])
                local limit = tonumber(ARGV[base + 2])
                local current = tonumber(redis.call('get', KEYS[i]) or '0')
                if limit > 0 and current + delta > limit then
                    return {0, i, current}
                end
            end
            for i = 1, #KEYS do
                local base = (i - 1) * 3
                local delta = tonumber(ARGV[base + 1])
                local ttl = tonumber(ARGV[base + 3])
                redis.call('incrby', KEYS[i], delta)
                if redis.call('ttl', KEYS[i]) < 0 then
                    redis.call('expire', KEYS[i], ttl)
                end
            end
            return {1, 0, 0}
            """
            argv: list[int] = []
            for _key, delta, limit, ttl in entries:
                argv.extend((int(delta), int(limit), max(1, int(ttl))))
            result = redis_client.eval(script, len(entries), *[item[0] for item in entries], *argv)
            if not result or int(result[0]) != 1:
                raise RateLimitError(message)
            return

        now = time.time()
        with _MEMORY_LOCK:
            self._cleanup_memory(now)
            for key, delta, limit, _ttl in entries:
                current = _MEMORY_COUNTERS.get(key, (0, now))[0]
                if limit > 0 and current + delta > limit:
                    raise RateLimitError(message)
            for key, delta, _limit, ttl in entries:
                current, expires_at = _MEMORY_COUNTERS.get(key, (0, now + ttl))
                if expires_at <= now:
                    current, expires_at = 0, now + ttl
                _MEMORY_COUNTERS[key] = (current + delta, expires_at)

    def _release_entries(self, entries: tuple[tuple[str, int, int], ...]) -> None:
        if not entries:
            return
        redis_client = current_app.extensions.get("redis_client")
        if redis_client is not None:
            script = """
            for i = 1, #KEYS do
                local delta = tonumber(ARGV[i])
                local current = tonumber(redis.call('get', KEYS[i]) or '0')
                local updated = current - delta
                if updated <= 0 then
                    redis.call('del', KEYS[i])
                else
                    redis.call('set', KEYS[i], updated, 'KEEPTTL')
                end
            end
            return 1
            """
            redis_client.eval(script, len(entries), *[item[0] for item in entries], *[int(item[1]) for item in entries])
            return

        now = time.time()
        with _MEMORY_LOCK:
            self._cleanup_memory(now)
            for key, delta, _ttl in entries:
                current = _MEMORY_COUNTERS.get(key)
                if not current:
                    continue
                updated = current[0] - int(delta)
                if updated <= 0:
                    _MEMORY_COUNTERS.pop(key, None)
                else:
                    _MEMORY_COUNTERS[key] = (updated, current[1])

    def _model_pricing(self, model: str) -> dict[str, float] | None:
        table = current_app.config.get("ANALYTICS_AI_MODEL_PRICING") or {}
        normalized = str(model or "").strip().lower()
        value = table.get(normalized)
        if value is None:
            for key, candidate in table.items():
                if normalized.startswith(str(key).lower()):
                    value = candidate
                    break
        if not isinstance(value, dict):
            return None
        try:
            input_rate = max(0.0, float(value.get("input", 0)))
            output_rate = max(0.0, float(value.get("output", 0)))
            cached_rate = max(0.0, float(value.get("cached_input", input_rate)))
        except (TypeError, ValueError):
            return None
        if input_rate <= 0 and output_rate <= 0:
            return None
        return {"input": input_rate, "cached_input": cached_rate, "output": output_rate}

    @staticmethod
    def _usage_value(usage: Any, *names: str) -> int:
        for name in names:
            value = getattr(usage, name, None) if usage is not None else None
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            try:
                if value is not None:
                    return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    @classmethod
    def _nested_usage_value(cls, usage: Any, *paths: tuple[str, str]) -> int:
        for parent_name, child_name in paths:
            parent = getattr(usage, parent_name, None) if usage is not None else None
            if parent is None and isinstance(usage, dict):
                parent = usage.get(parent_name)
            value = getattr(parent, child_name, None) if parent is not None else None
            if value is None and isinstance(parent, dict):
                value = parent.get(child_name)
            try:
                if value is not None:
                    return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    @staticmethod
    def _budget_units(config_key: str, default: float) -> int:
        try:
            dollars = float(current_app.config.get(config_key, default) or 0)
        except (TypeError, ValueError):
            dollars = default
        return max(0, int(math.floor(dollars * _MICRODOLLARS_PER_DOLLAR)))

    @staticmethod
    def _user_key(user_id: str) -> str:
        return hashlib.sha256(str(user_id or "anonymous").encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _key(*parts: str) -> str:
        return "reunia:ai-budget:" + ":".join(str(part) for part in parts)

    @staticmethod
    def _day_key() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _seconds_until_tomorrow() -> int:
        now = datetime.now(timezone.utc)
        tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        return max(60, int((tomorrow - now).total_seconds()) + 3600)

    @staticmethod
    def _seconds_until_next_month() -> int:
        now = datetime.now(timezone.utc)
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        return max(60, int((next_month - now).total_seconds()) + 24 * 60 * 60)

    @staticmethod
    def _cleanup_memory(now: float) -> None:
        for key, (_value, expires_at) in list(_MEMORY_COUNTERS.items()):
            if expires_at <= now:
                _MEMORY_COUNTERS.pop(key, None)
