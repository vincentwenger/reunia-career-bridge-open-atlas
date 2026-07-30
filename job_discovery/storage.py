from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .models import (
    CompanySource,
    DiscoveredJob,
    JobFitSnapshot,
    JobSourceType,
    WorkplaceType,
    normalize_iso_timestamp,
    utc_now_iso,
)

DISCOVERY_TABLE_CONFIG_KEY = "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME"
_SOURCE_PREFIX = "SOURCE#"
_JOB_PREFIX = "JOB#"
_FIT_PREFIX = "FIT#"


class DiscoveryStorageConfigurationError(RuntimeError):
    pass


@runtime_checkable
class DiscoveryStore(Protocol):
    """Persistence boundary for discovery-only records.

    This contract intentionally has no method that creates or updates a
    JobApplication. Promotion into the application lifecycle must be an explicit
    user action handled outside this store.
    """

    def put_company_source(self, source: CompanySource) -> None:
        ...

    def get_company_source(self, owner_id: str, source_id: str) -> CompanySource | None:
        ...

    def list_company_sources(self, owner_id: str, *, enabled_only: bool = False) -> list[CompanySource]:
        ...

    def delete_company_source(self, owner_id: str, source_id: str) -> bool:
        ...

    def sync_discovered_jobs(
        self,
        source: CompanySource,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str | None = None,
    ) -> list[DiscoveredJob]:
        ...

    def get_discovered_job(
        self,
        owner_id: str,
        source_id: str,
        job_id: str,
    ) -> DiscoveredJob | None:
        ...

    def list_discovered_jobs(
        self,
        owner_id: str,
        *,
        source_id: str | None = None,
        active_only: bool = True,
    ) -> list[DiscoveredJob]:
        ...

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        ...

    def get_fit_snapshot(
        self,
        owner_id: str,
        job_id: str,
        profile_fingerprint: str,
    ) -> JobFitSnapshot | None:
        ...

    def list_fit_snapshots(
        self,
        owner_id: str,
        *,
        job_id: str | None = None,
    ) -> list[JobFitSnapshot]:
        ...


class InMemoryDiscoveryStore:
    def __init__(self, *, clock: Callable[[], str] | None = None) -> None:
        self._clock = clock or utc_now_iso
        self._sources: dict[tuple[str, str], CompanySource] = {}
        self._jobs: dict[tuple[str, str, str], DiscoveredJob] = {}
        self._fits: dict[tuple[str, str, str], JobFitSnapshot] = {}
        self._lock = threading.RLock()

    def put_company_source(self, source: CompanySource) -> None:
        with self._lock:
            self._sources[(source.owner_id, source.id)] = source

    def get_company_source(self, owner_id: str, source_id: str) -> CompanySource | None:
        with self._lock:
            return self._sources.get((owner_id, source_id))

    def list_company_sources(self, owner_id: str, *, enabled_only: bool = False) -> list[CompanySource]:
        with self._lock:
            sources = [source for (owner, _), source in self._sources.items() if owner == owner_id]
        if enabled_only:
            sources = [source for source in sources if source.enabled]
        return sorted(sources, key=lambda source: (source.company_name.casefold(), source.id))

    def delete_company_source(self, owner_id: str, source_id: str) -> bool:
        with self._lock:
            return self._sources.pop((owner_id, source_id), None) is not None

    def sync_discovered_jobs(
        self,
        source: CompanySource,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str | None = None,
    ) -> list[DiscoveredJob]:
        checked = normalize_iso_timestamp(checked_at) or self._clock()
        _validate_sync(source, jobs)
        with self._lock:
            existing = {
                job.external_job_id: job
                for (owner, stored_source, _), job in self._jobs.items()
                if owner == source.owner_id and stored_source == source.id
            }
            synchronized: list[DiscoveredJob] = []
            seen_external_ids: set[str] = set()
            for job in jobs:
                previous = existing.get(job.external_job_id)
                current = job.seen(
                    checked,
                    first_seen_at=previous.first_seen_at if previous else checked,
                )
                self._jobs[(current.owner_id, current.source_id, current.id)] = current
                synchronized.append(current)
                seen_external_ids.add(current.external_job_id)

            for external_id, previous in existing.items():
                if external_id in seen_external_ids or not previous.active:
                    continue
                inactive = previous.inactive()
                self._jobs[(inactive.owner_id, inactive.source_id, inactive.id)] = inactive

            self._sources[(source.owner_id, source.id)] = source.checked(checked)
        return sorted(synchronized, key=_job_sort_key)

    def get_discovered_job(
        self,
        owner_id: str,
        source_id: str,
        job_id: str,
    ) -> DiscoveredJob | None:
        with self._lock:
            return self._jobs.get((owner_id, source_id, job_id))

    def list_discovered_jobs(
        self,
        owner_id: str,
        *,
        source_id: str | None = None,
        active_only: bool = True,
    ) -> list[DiscoveredJob]:
        with self._lock:
            jobs = [
                job
                for (owner, stored_source, _), job in self._jobs.items()
                if owner == owner_id and (source_id is None or stored_source == source_id)
            ]
        if active_only:
            jobs = [job for job in jobs if job.active]
        return sorted(jobs, key=_job_sort_key)

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        with self._lock:
            self._fits[(snapshot.owner_id, snapshot.job_id, snapshot.profile_fingerprint)] = snapshot

    def get_fit_snapshot(
        self,
        owner_id: str,
        job_id: str,
        profile_fingerprint: str,
    ) -> JobFitSnapshot | None:
        with self._lock:
            return self._fits.get((owner_id, job_id, profile_fingerprint))

    def list_fit_snapshots(
        self,
        owner_id: str,
        *,
        job_id: str | None = None,
    ) -> list[JobFitSnapshot]:
        with self._lock:
            snapshots = [
                snapshot
                for (owner, stored_job_id, _), snapshot in self._fits.items()
                if owner == owner_id and (job_id is None or stored_job_id == job_id)
            ]
        return sorted(snapshots, key=lambda item: (item.analyzed_at, item.job_id), reverse=True)


class JsonFileDiscoveryStore(InMemoryDiscoveryStore):
    """Local development adapter with the same discovery-specific contract."""

    def __init__(self, path: str | Path, *, clock: Callable[[], str] | None = None) -> None:
        self.path = Path(path)
        self._loading = True
        super().__init__(clock=clock)
        self._load()
        self._loading = False

    def put_company_source(self, source: CompanySource) -> None:
        super().put_company_source(source)
        self._save()

    def delete_company_source(self, owner_id: str, source_id: str) -> bool:
        deleted = super().delete_company_source(owner_id, source_id)
        if deleted:
            self._save()
        return deleted

    def sync_discovered_jobs(
        self,
        source: CompanySource,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str | None = None,
    ) -> list[DiscoveredJob]:
        synchronized = super().sync_discovered_jobs(source, jobs, checked_at=checked_at)
        self._save()
        return synchronized

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        super().put_fit_snapshot(snapshot)
        self._save()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            sources = [_company_source_from_dict(item) for item in payload.get("sources", [])]
            jobs = [_job_from_dict(item) for item in payload.get("jobs", [])]
            fits = [_fit_from_dict(item) for item in payload.get("fit_snapshots", [])]
        except (OSError, ValueError, TypeError, KeyError):
            return
        with self._lock:
            self._sources = {(item.owner_id, item.id): item for item in sources}
            self._jobs = {(item.owner_id, item.source_id, item.id): item for item in jobs}
            self._fits = {
                (item.owner_id, item.job_id, item.profile_fingerprint): item for item in fits
            }

    def _save(self) -> None:
        if self._loading:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = {
                "sources": [_company_source_to_dict(item) for item in self._sources.values()],
                "jobs": [_job_to_dict(item) for item in self._jobs.values()],
                "fit_snapshots": [_fit_to_dict(item) for item in self._fits.values()],
            }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


class DynamoDBDiscoveryStore:
    """Dedicated DynamoDB repository for source, posting, and fit records.

    The table is deliberately separate from the application table. It uses
    ``owner_id`` as the partition key and ``storage_key`` as the sort key:

    * ``SOURCE#<source_id>``
    * ``JOB#<source_id>#<job_id>``
    * ``FIT#<job_id>#<profile_fingerprint>``

    All queries are owner-scoped and prefix-based; the adapter never scans the
    table and never writes a JobApplication item.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        table: Any | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._table_override = table
        self._resolved_table: Any | None = None
        self._clock = clock or utc_now_iso
        table_name = str(config.get(DISCOVERY_TABLE_CONFIG_KEY) or "").strip()
        if table is None and not table_name:
            raise DiscoveryStorageConfigurationError(
                f"{DISCOVERY_TABLE_CONFIG_KEY} is required for DynamoDB job discovery storage."
            )
        self._table_name = table_name

    def _table(self) -> Any:
        if self._table_override is not None:
            return self._table_override
        if self._resolved_table is None:
            import boto3

            region = str(
                self._config.get("AWS_REGION")
                or self._config.get("AWS_DEFAULT_REGION")
                or "us-west-2"
            ).strip()
            self._resolved_table = boto3.resource("dynamodb", region_name=region).Table(
                self._table_name
            )
        return self._resolved_table

    @staticmethod
    def _key(owner_id: str, storage_key: str) -> dict[str, str]:
        return {"owner_id": owner_id, "storage_key": storage_key}

    def _put(self, item: dict[str, Any]) -> None:
        self._table().put_item(Item=_to_dynamodb(item))

    def _get(self, owner_id: str, storage_key: str) -> dict[str, Any] | None:
        response = self._table().get_item(
            Key=self._key(owner_id, storage_key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return _from_dynamodb(item) if item else None

    def _query_prefix(self, owner_id: str, prefix: str) -> list[dict[str, Any]]:
        query_args: dict[str, Any] = {
            "KeyConditionExpression": "#owner_id = :owner_id AND begins_with(#storage_key, :prefix)",
            "ExpressionAttributeNames": {
                "#owner_id": "owner_id",
                "#storage_key": "storage_key",
            },
            "ExpressionAttributeValues": {
                ":owner_id": owner_id,
                ":prefix": prefix,
            },
        }
        items: list[dict[str, Any]] = []
        while True:
            response = self._table().query(**query_args)
            items.extend(_from_dynamodb(item) for item in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            query_args["ExclusiveStartKey"] = last_key

    def put_company_source(self, source: CompanySource) -> None:
        self._put(_source_item(source))

    def get_company_source(self, owner_id: str, source_id: str) -> CompanySource | None:
        item = self._get(owner_id, _source_key(source_id))
        return _company_source_from_dict(item) if item else None

    def list_company_sources(self, owner_id: str, *, enabled_only: bool = False) -> list[CompanySource]:
        sources = [_company_source_from_dict(item) for item in self._query_prefix(owner_id, _SOURCE_PREFIX)]
        if enabled_only:
            sources = [source for source in sources if source.enabled]
        return sorted(sources, key=lambda source: (source.company_name.casefold(), source.id))

    def delete_company_source(self, owner_id: str, source_id: str) -> bool:
        response = self._table().delete_item(
            Key=self._key(owner_id, _source_key(source_id)),
            ReturnValues="ALL_OLD",
        )
        return bool(response.get("Attributes"))

    def sync_discovered_jobs(
        self,
        source: CompanySource,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str | None = None,
    ) -> list[DiscoveredJob]:
        checked = normalize_iso_timestamp(checked_at) or self._clock()
        _validate_sync(source, jobs)
        existing = {
            job.external_job_id: job
            for job in self.list_discovered_jobs(
                source.owner_id,
                source_id=source.id,
                active_only=False,
            )
        }
        synchronized: list[DiscoveredJob] = []
        seen_external_ids: set[str] = set()
        for job in jobs:
            previous = existing.get(job.external_job_id)
            current = job.seen(
                checked,
                first_seen_at=previous.first_seen_at if previous else checked,
            )
            self._put(_job_item(current))
            synchronized.append(current)
            seen_external_ids.add(current.external_job_id)

        for external_id, previous in existing.items():
            if external_id in seen_external_ids or not previous.active:
                continue
            self._put(_job_item(previous.inactive()))

        self.put_company_source(source.checked(checked))
        return sorted(synchronized, key=_job_sort_key)

    def get_discovered_job(
        self,
        owner_id: str,
        source_id: str,
        job_id: str,
    ) -> DiscoveredJob | None:
        item = self._get(owner_id, _job_key(source_id, job_id))
        return _job_from_dict(item) if item else None

    def list_discovered_jobs(
        self,
        owner_id: str,
        *,
        source_id: str | None = None,
        active_only: bool = True,
    ) -> list[DiscoveredJob]:
        prefix = f"{_JOB_PREFIX}{source_id}#" if source_id else _JOB_PREFIX
        jobs = [_job_from_dict(item) for item in self._query_prefix(owner_id, prefix)]
        if active_only:
            jobs = [job for job in jobs if job.active]
        return sorted(jobs, key=_job_sort_key)

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        self._put(_fit_item(snapshot))

    def get_fit_snapshot(
        self,
        owner_id: str,
        job_id: str,
        profile_fingerprint: str,
    ) -> JobFitSnapshot | None:
        item = self._get(owner_id, _fit_key(job_id, profile_fingerprint))
        return _fit_from_dict(item) if item else None

    def list_fit_snapshots(
        self,
        owner_id: str,
        *,
        job_id: str | None = None,
    ) -> list[JobFitSnapshot]:
        prefix = f"{_FIT_PREFIX}{job_id}#" if job_id else _FIT_PREFIX
        snapshots = [_fit_from_dict(item) for item in self._query_prefix(owner_id, prefix)]
        return sorted(snapshots, key=lambda item: (item.analyzed_at, item.job_id), reverse=True)


class CacheStore(Protocol):
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        ...


class InMemoryTTLCache:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._values: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        now = self._clock()
        with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._values.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._values[key] = (self._clock() + max(0.0, float(ttl_seconds)), value)


def _validate_sync(source: CompanySource, jobs: list[DiscoveredJob]) -> None:
    for job in jobs:
        if job.owner_id != source.owner_id:
            raise ValueError("job owner_id must match source owner_id")
        if job.source_id != source.id:
            raise ValueError("job source_id must match source id")


def _source_key(source_id: str) -> str:
    return f"{_SOURCE_PREFIX}{source_id}"


def _job_key(source_id: str, job_id: str) -> str:
    return f"{_JOB_PREFIX}{source_id}#{job_id}"


def _fit_key(job_id: str, profile_fingerprint: str) -> str:
    return f"{_FIT_PREFIX}{job_id}#{profile_fingerprint}"


def _job_sort_key(job: DiscoveredJob) -> tuple[str, str, str]:
    return job.company.casefold(), job.title.casefold(), job.external_job_id


def _source_item(source: CompanySource) -> dict[str, Any]:
    item = _company_source_to_dict(source)
    item.update(
        {
            "owner_id": source.owner_id,
            "storage_key": _source_key(source.id),
            "entity_type": "company_source",
        }
    )
    return item


def _job_item(job: DiscoveredJob) -> dict[str, Any]:
    item = _job_to_dict(job)
    item.update(
        {
            "owner_id": job.owner_id,
            "storage_key": _job_key(job.source_id, job.id),
            "entity_type": "discovered_job",
        }
    )
    return item


def _fit_item(snapshot: JobFitSnapshot) -> dict[str, Any]:
    item = _fit_to_dict(snapshot)
    item.update(
        {
            "owner_id": snapshot.owner_id,
            "storage_key": _fit_key(snapshot.job_id, snapshot.profile_fingerprint),
            "entity_type": "job_fit_snapshot",
        }
    )
    return item


def _company_source_to_dict(source: CompanySource) -> dict[str, Any]:
    return {
        "id": source.id,
        "owner_id": source.owner_id,
        "company_name": source.company_name,
        "careers_url": source.careers_url,
        "source_type": source.source_type.value,
        "source_identifier": source.source_identifier,
        "enabled": source.enabled,
        "last_checked_at": source.last_checked_at,
        "filters": dict(source.filters),
    }


def _company_source_from_dict(payload: Mapping[str, Any]) -> CompanySource:
    return CompanySource(
        id=str(payload.get("id") or ""),
        owner_id=str(payload.get("owner_id") or ""),
        company_name=str(payload.get("company_name") or ""),
        careers_url=str(payload.get("careers_url") or ""),
        source_type=str(payload.get("source_type") or ""),
        source_identifier=str(payload.get("source_identifier") or ""),
        enabled=bool(payload.get("enabled", True)),
        last_checked_at=str(payload.get("last_checked_at") or ""),
        filters=dict(payload.get("filters") or {}),
    )


def _job_to_dict(job: DiscoveredJob) -> dict[str, Any]:
    payload = asdict(job)
    payload["source_type"] = job.source_type.value
    payload["workplace_type"] = job.workplace_type.value
    payload["locations"] = list(job.locations)
    payload["skills"] = list(job.skills)
    return payload


def _job_from_dict(payload: Mapping[str, Any]) -> DiscoveredJob:
    allowed = {field.name for field in DiscoveredJob.__dataclass_fields__.values()}
    data = {key: value for key, value in payload.items() if key in allowed}
    data["source_type"] = JobSourceType(str(data.get("source_type") or JobSourceType.GENERIC_JSONLD.value))
    data["workplace_type"] = WorkplaceType(str(data.get("workplace_type") or WorkplaceType.UNSPECIFIED.value))
    data["locations"] = tuple(data.get("locations") or ())
    data["skills"] = tuple(data.get("skills") or ())
    data["metadata"] = dict(data.get("metadata") or {})
    return DiscoveredJob(**data)


def _fit_to_dict(snapshot: JobFitSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    for name in (
        "supported_requirements",
        "partial_requirements",
        "unsupported_requirements",
        "hard_blockers",
    ):
        payload[name] = list(payload[name])
    return payload


def _fit_from_dict(payload: Mapping[str, Any]) -> JobFitSnapshot:
    allowed = {field.name for field in JobFitSnapshot.__dataclass_fields__.values()}
    data = {key: value for key, value in payload.items() if key in allowed}
    for name in (
        "supported_requirements",
        "partial_requirements",
        "unsupported_requirements",
        "hard_blockers",
    ):
        data[name] = tuple(data.get(name) or ())
    return JobFitSnapshot(**data)


def _to_dynamodb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(key): _to_dynamodb(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamodb(item) for item in value]
    return value


def _from_dynamodb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _from_dynamodb(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamodb(item) for item in value]
    return value


# Compatibility aliases for callers created with the first adapter-only version.
InMemoryJobStore = InMemoryDiscoveryStore
JsonFileJobStore = JsonFileDiscoveryStore
JobStore = DiscoveryStore
