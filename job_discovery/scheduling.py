"""External entry points for scheduled public-job discovery scans.

This module deliberately contains no Flask, APScheduler, background thread, or
Gunicorn lifecycle hook. Invoke it from AWS Lambda, a scheduled container, or a
controlled cron process outside the web workers.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Callable, Iterable, Mapping, Protocol

from .models import DiscoveryScanSchedule, DiscoveryScheduleCadence, normalize_iso_timestamp
from .public_catalog import SHARED_CATALOG_SOURCE_OWNER_ID
from .ranking import CandidateJobProfile
from .service import DiscoveryResult, JobDiscoveryService
from .storage import (
    DiscoveryStore,
    DynamoDBDiscoveryStore,
    JsonFileDiscoveryStore,
)


class CandidateProfileProvider(Protocol):
    def __call__(self, owner_id: str) -> CandidateJobProfile | None:
        ...


@dataclass(frozen=True, slots=True)
class OwnerScanSummary:
    owner_id: str
    enabled_sources: int
    profile_available: bool
    collected_jobs: int
    ranked_jobs: int
    filtered_jobs: int
    source_errors: int
    analysis_errors: int
    scan_performed: bool = True
    skip_reason: str = ""


@dataclass(frozen=True, slots=True)
class ScheduledScanSummary:
    owners: tuple[OwnerScanSummary, ...]

    @property
    def succeeded(self) -> bool:
        return all(
            item.source_errors == 0 and item.analysis_errors == 0
            for item in self.owners
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "succeeded": self.succeeded,
            "owners": [asdict(item) for item in self.owners],
        }


class ExternalJobDiscoveryRunner:
    """Run scans in a process controlled independently from the web service."""

    def __init__(
        self,
        store: DiscoveryStore,
        *,
        profile_provider: CandidateProfileProvider | None = None,
        service_factory: Callable[[DiscoveryStore], JobDiscoveryService] | None = None,
    ) -> None:
        self.store = store
        self.profile_provider = profile_provider
        self.service_factory = service_factory or (
            lambda discovery_store: JobDiscoveryService(
                store=discovery_store, use_shared_public_catalog=True
            )
        )

    def run_owner(self, owner_id: str) -> OwnerScanSummary:
        normalized_owner = str(owner_id or "").strip()
        if not normalized_owner:
            raise ValueError("owner_id is required")
        sources = self.store.list_company_sources(normalized_owner, enabled_only=True)
        profile = (
            self.profile_provider(normalized_owner)
            if self.profile_provider is not None
            else None
        )
        result = self.service_factory(self.store).discover(
            sources,
            candidate_profile=profile,
        )
        return _owner_summary(
            normalized_owner,
            len(sources),
            result,
            profile_available=profile is not None,
        )

    def run_owners(self, owner_ids: Iterable[str]) -> ScheduledScanSummary:
        unique_owner_ids = tuple(
            dict.fromkeys(
                normalized
                for value in owner_ids
                if (normalized := str(value or "").strip())
            )
        )
        if not unique_owner_ids:
            raise ValueError("At least one owner_id is required")
        return ScheduledScanSummary(
            owners=tuple(self.run_owner(owner_id) for owner_id in unique_owner_ids)
        )

    def run_scheduled_owner(
        self,
        owner_id: str,
        *,
        now: datetime | str | None = None,
    ) -> OwnerScanSummary:
        normalized_owner = str(owner_id or "").strip()
        if not normalized_owner:
            raise ValueError("owner_id is required")
        schedule = self.store.get_scan_schedule(normalized_owner)
        if schedule is None or schedule.cadence is DiscoveryScheduleCadence.MANUAL:
            return _skipped_owner_summary(
                normalized_owner,
                "Scheduled scanning is not enabled for this owner.",
            )
        current = _coerce_datetime(now)
        if not schedule_is_due(schedule, now=current):
            return _skipped_owner_summary(
                normalized_owner,
                "The next configured scan time has not arrived.",
            )
        summary = self.run_owner(normalized_owner)
        self.store.put_scan_schedule(schedule.marked_run(current))
        return summary

    def run_scheduled_owners(
        self,
        owner_ids: Iterable[str],
        *,
        now: datetime | str | None = None,
    ) -> ScheduledScanSummary:
        unique_owner_ids = tuple(
            dict.fromkeys(
                normalized
                for value in owner_ids
                if (normalized := str(value or "").strip())
            )
        )
        if not unique_owner_ids:
            raise ValueError("At least one owner_id is required")
        return ScheduledScanSummary(
            owners=tuple(
                self.run_scheduled_owner(owner_id, now=now)
                for owner_id in unique_owner_ids
            )
        )


def create_external_discovery_store(
    config: Mapping[str, Any] | None = None,
) -> DiscoveryStore:
    """Create storage for Lambda, scheduled containers, or cron commands."""

    values: Mapping[str, Any] = config or os.environ
    backend = str(
        values.get("CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND", "dynamodb")
    ).strip().lower()
    if backend == "dynamodb":
        return DynamoDBDiscoveryStore(values)
    if backend == "json":
        path = str(
            values.get("CAREER_BRIDGE_JOB_DISCOVERY_JSON_PATH")
            or "instance/job_discovery.json"
        ).strip()
        return JsonFileDiscoveryStore(path)
    raise RuntimeError(
        "External discovery scans require "
        "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb or json. "
        "An in-memory store would discard results when the process exits."
    )


def lambda_handler(event: Mapping[str, Any] | None, _context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for an EventBridge schedule.

    Event payload example::

        {"owner_ids": ["user-1", "user-2"]}

    Optional ``profiles`` may contain serialized ``CandidateJobProfile`` values
    keyed by owner ID. Production integrations should normally replace this with
    a profile provider backed by the application's durable Career Profile and
    Evidence Library repositories.
    """

    payload = dict(event or {})
    owner_ids = payload.get("owner_ids") or _environment_owner_ids()
    if isinstance(owner_ids, str):
        owner_ids = [owner_ids]
    profiles = payload.get("profiles") or {}
    provider = _mapping_profile_provider(profiles) if profiles else None
    runner = ExternalJobDiscoveryRunner(
        create_external_discovery_store(),
        profile_provider=provider,
    )
    if bool(payload.get("force")):
        return runner.run_owners(owner_ids).to_dict()
    return runner.run_scheduled_owners(owner_ids).to_dict()


def schedule_is_due(
    schedule: DiscoveryScanSchedule,
    *,
    now: datetime | str | None = None,
) -> bool:
    if schedule.cadence is DiscoveryScheduleCadence.MANUAL:
        return False
    current = _coerce_datetime(now)
    anchor = _latest_schedule_anchor(schedule, current)
    baseline = (
        _coerce_datetime(schedule.last_run_at)
        if schedule.last_run_at
        else _coerce_datetime(schedule.updated_at)
    )
    return baseline < anchor


def next_scheduled_run(
    schedule: DiscoveryScanSchedule,
    *,
    now: datetime | str | None = None,
) -> datetime | None:
    if schedule.cadence is DiscoveryScheduleCadence.MANUAL:
        return None
    current = _coerce_datetime(now)
    anchor = _latest_schedule_anchor(schedule, current)
    baseline = (
        _coerce_datetime(schedule.last_run_at)
        if schedule.last_run_at
        else _coerce_datetime(schedule.updated_at)
    )
    if baseline < anchor:
        return current
    if schedule.cadence is DiscoveryScheduleCadence.DAILY:
        return anchor + timedelta(days=1)
    return anchor + timedelta(days=7)


def _latest_schedule_anchor(
    schedule: DiscoveryScanSchedule, current: datetime
) -> datetime:
    try:
        zone = ZoneInfo(schedule.timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown time zone: {schedule.timezone_name}") from exc
    local_now = current.astimezone(zone)
    if schedule.cadence is DiscoveryScheduleCadence.DAILY:
        anchor_local = local_now.replace(
            hour=schedule.local_hour, minute=0, second=0, microsecond=0
        )
        if local_now < anchor_local:
            anchor_local -= timedelta(days=1)
    else:
        days_since = (local_now.weekday() - schedule.weekday) % 7
        anchor_local = (local_now - timedelta(days=days_since)).replace(
            hour=schedule.local_hour, minute=0, second=0, microsecond=0
        )
        if local_now < anchor_local:
            anchor_local -= timedelta(days=7)
    return anchor_local.astimezone(timezone.utc)


def _coerce_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(normalize_iso_timestamp(value).replace("Z", "+00:00"))


def _skipped_owner_summary(owner_id: str, reason: str) -> OwnerScanSummary:
    return OwnerScanSummary(
        owner_id=owner_id,
        enabled_sources=0,
        profile_available=False,
        collected_jobs=0,
        ranked_jobs=0,
        filtered_jobs=0,
        source_errors=0,
        analysis_errors=0,
        scan_performed=False,
        skip_reason=reason,
    )


def _owner_summary(
    owner_id: str,
    enabled_sources: int,
    result: DiscoveryResult,
    *,
    profile_available: bool,
) -> OwnerScanSummary:
    return OwnerScanSummary(
        owner_id=owner_id,
        enabled_sources=enabled_sources,
        profile_available=profile_available,
        collected_jobs=len(result.jobs),
        ranked_jobs=len(result.ranked_jobs),
        filtered_jobs=len(result.filtered_jobs),
        source_errors=len(result.errors),
        analysis_errors=len(result.analysis_errors),
    )


def _mapping_profile_provider(
    profiles: Mapping[str, Any],
) -> CandidateProfileProvider:
    normalized = {
        str(owner_id): (
            value
            if isinstance(value, CandidateJobProfile)
            else CandidateJobProfile(**dict(value))
        )
        for owner_id, value in profiles.items()
    }
    return lambda owner_id: normalized.get(owner_id)


def _environment_owner_ids() -> list[str]:
    raw = str(os.environ.get("JOB_DISCOVERY_OWNER_IDS") or "").strip()
    configured = [item.strip() for item in raw.split(",") if item.strip()]
    return configured or [SHARED_CATALOG_SOURCE_OWNER_ID]


def _load_profiles(path: str) -> Mapping[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("profile JSON must be an object keyed by owner_id")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run public job discovery outside Flask/Gunicorn.",
    )
    parser.add_argument(
        "--owner-id",
        action="append",
        dest="owner_ids",
        help="Catalog owner to scan. Defaults to the shared catalog owner.",
    )
    parser.add_argument(
        "--profiles-json",
        default="",
        help="Optional JSON object of serialized CandidateJobProfile values keyed by owner ID.",
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Scan only owners whose saved daily or weekly schedule is due.",
    )
    args = parser.parse_args(argv)
    owner_ids = args.owner_ids or _environment_owner_ids()
    profiles = _load_profiles(args.profiles_json)
    runner = ExternalJobDiscoveryRunner(
        create_external_discovery_store(),
        profile_provider=_mapping_profile_provider(profiles) if profiles else None,
    )
    summary = (
        runner.run_scheduled_owners(owner_ids)
        if args.scheduled
        else runner.run_owners(owner_ids)
    )
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    return 0 if summary.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
