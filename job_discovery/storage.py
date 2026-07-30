from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .models import DiscoveredJob, JobSourceType, WorkplaceType


class JobStore(Protocol):
    def replace_for_source(self, source_id: str, jobs: list[DiscoveredJob]) -> None:
        ...

    def list_jobs(self, *, source_id: str | None = None) -> list[DiscoveredJob]:
        ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, DiscoveredJob] = {}
        self._lock = threading.RLock()

    def replace_for_source(self, source_id: str, jobs: list[DiscoveredJob]) -> None:
        with self._lock:
            self._jobs = {
                key: job for key, job in self._jobs.items() if job.source_id != source_id
            }
            for job in jobs:
                self._jobs[_storage_key(job)] = job

    def list_jobs(self, *, source_id: str | None = None) -> list[DiscoveredJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        if source_id is not None:
            jobs = [job for job in jobs if job.source_id == source_id]
        return sorted(jobs, key=lambda job: (job.company.casefold(), job.title.casefold(), job.external_id))


class JsonFileJobStore(InMemoryJobStore):
    """Small local store for development; production can implement ``JobStore`` in DynamoDB."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        super().__init__()
        self._load()

    def replace_for_source(self, source_id: str, jobs: list[DiscoveredJob]) -> None:
        super().replace_for_source(source_id, jobs)
        self._save()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            jobs = [_job_from_dict(item) for item in payload.get("jobs", [])]
        except (OSError, ValueError, TypeError, KeyError):
            return
        with self._lock:
            self._jobs = {_storage_key(job): job for job in jobs}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": [_job_to_dict(job) for job in self.list_jobs()]}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


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


def _storage_key(job: DiscoveredJob) -> str:
    return f"{job.source_id}:{job.external_id}"


def _job_to_dict(job: DiscoveredJob) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_id": job.source_id,
        "source_type": job.source_type.value,
        "external_id": job.external_id,
        "company": job.company,
        "title": job.title,
        "job_url": job.job_url,
        "apply_url": job.apply_url,
        "description": job.description,
        "location": job.location,
        "locations": list(job.locations),
        "workplace_type": job.workplace_type.value,
        "employment_type": job.employment_type,
        "department": job.department,
        "team": job.team,
        "skills": list(job.skills),
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "salary_interval": job.salary_interval,
        "salary_summary": job.salary_summary,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "valid_through": job.valid_through.isoformat() if job.valid_through else None,
        "discovered_at": job.discovered_at.isoformat(),
        "metadata": dict(job.metadata),
    }
    return payload


def _job_from_dict(payload: dict[str, Any]) -> DiscoveredJob:
    data = dict(payload)
    data["source_type"] = JobSourceType(data["source_type"])
    data["workplace_type"] = WorkplaceType(data.get("workplace_type", "unspecified"))
    for name in ("posted_at", "updated_at", "valid_through", "discovered_at"):
        value = data.get(name)
        data[name] = datetime.fromisoformat(value) if value else None
    data["locations"] = tuple(data.get("locations") or ())
    data["skills"] = tuple(data.get("skills") or ())
    return DiscoveredJob(**data)
