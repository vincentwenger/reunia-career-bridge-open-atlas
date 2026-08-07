from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from .models import (
    DEFAULT_MAX_POSTING_AGE_DAYS,
    DiscoveredJob,
    normalize_iso_timestamp,
)


_METADATA_DATE_KEYS = (
    "posted_at",
    "date_posted",
    "published_at",
    "created_at",
    "updated_at",
)
_EVERGREEN_KEYS = (
    "evergreen",
    "is_evergreen",
    "continuous_hiring",
    "is_continuous_hiring",
)


@dataclass(frozen=True, slots=True)
class PostingAgeDecision:
    eligible: bool
    reason: str
    age_days: int | None = None


def partition_jobs_by_posting_age(
    jobs: Iterable[DiscoveredJob],
    *,
    maximum_age_days: int | None = DEFAULT_MAX_POSTING_AGE_DAYS,
    evaluated_at: datetime | str | None = None,
) -> tuple[list[DiscoveredJob], list[DiscoveredJob]]:
    """Split normalized jobs using one source-independent freshness policy.

    Unknown-date postings are retained because excluding them would silently
    eliminate sources that do not expose a reliable publication timestamp.
    A future valid-through date and explicit evergreen metadata override the
    age limit.
    """

    eligible: list[DiscoveredJob] = []
    filtered: list[DiscoveredJob] = []
    for job in jobs:
        decision = evaluate_posting_age(
            job,
            maximum_age_days=maximum_age_days,
            evaluated_at=evaluated_at,
        )
        (eligible if decision.eligible else filtered).append(job)
    return eligible, filtered


def evaluate_posting_age(
    job: DiscoveredJob,
    *,
    maximum_age_days: int | None = DEFAULT_MAX_POSTING_AGE_DAYS,
    evaluated_at: datetime | str | None = None,
) -> PostingAgeDecision:
    if maximum_age_days is None:
        return PostingAgeDecision(True, "Posting-age filtering is disabled")
    try:
        limit = int(maximum_age_days)
    except (TypeError, ValueError) as exc:
        raise ValueError("maximum_age_days must be an integer or None") from exc
    if limit < 1:
        raise ValueError("maximum_age_days must be at least 1")

    reference = _as_utc_datetime(evaluated_at) or datetime.now(timezone.utc)
    if _is_evergreen(job.metadata):
        return PostingAgeDecision(True, "Evergreen or continuous-hiring posting")

    valid_through = _safe_datetime(job.valid_through)
    if valid_through is not None and valid_through >= reference:
        return PostingAgeDecision(True, "Application deadline is still in the future")

    published = _effective_posting_datetime(job)
    if published is None:
        return PostingAgeDecision(True, "Posting date is unavailable")

    age_days = max(0, int((reference - published).total_seconds() // 86400))
    if age_days <= limit:
        return PostingAgeDecision(
            True,
            f"Posting is {age_days} day{'s' if age_days != 1 else ''} old",
            age_days,
        )
    return PostingAgeDecision(
        False,
        f"Posting is older than the configured {limit}-day limit",
        age_days,
    )


def _effective_posting_datetime(job: DiscoveredJob) -> datetime | None:
    posted = _safe_datetime(job.posted_at)
    if posted is not None:
        return posted
    metadata = job.metadata if isinstance(job.metadata, Mapping) else {}
    for key in _METADATA_DATE_KEYS:
        parsed = _safe_datetime(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _is_evergreen(metadata: Mapping[str, object] | object) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    for key in _EVERGREEN_KEYS:
        value = metadata.get(key)
        if isinstance(value, bool) and value:
            return True
        if str(value or "").strip().casefold() in {"1", "true", "yes", "evergreen"}:
            return True
    return False


def _safe_datetime(value: object) -> datetime | None:
    try:
        return _as_utc_datetime(value)
    except (TypeError, ValueError):
        return None


def _as_utc_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = normalize_iso_timestamp(str(value))
        if not normalized:
            return None
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("posting-age timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)
