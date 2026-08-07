from __future__ import annotations

import threading
import time
from typing import Any, Protocol, runtime_checkable

class DiscoveryStorageConfigurationError(RuntimeError):
    pass


class DiscoveryOptimisticLockError(RuntimeError):
    """Raised when a stale discovery record attempts to overwrite a newer version."""

    pass


@runtime_checkable
class DiscoveryStore(Protocol):
    """Persistence boundary for discovery-only records.

    This contract intentionally has no method that creates or updates a
    JobApplication. Promotion into the application lifecycle must be an explicit
    user action handled outside this store.
    """

    def put_company_source(self, source: CompanySource) -> CompanySource:
        ...

    def get_company_source(self, owner_id: str, source_id: str) -> CompanySource | None:
        ...

    def list_company_sources(self, owner_id: str, *, enabled_only: bool = False) -> list[CompanySource]:
        ...

    def delete_company_source(self, owner_id: str, source_id: str) -> bool:
        ...

    def put_search_preferences(
        self, preferences: DiscoverySearchPreferences
    ) -> DiscoverySearchPreferences:
        ...

    def get_search_preferences(
        self, owner_id: str
    ) -> DiscoverySearchPreferences | None:
        ...

    def put_scan_schedule(
        self, schedule: DiscoveryScanSchedule
    ) -> DiscoveryScanSchedule:
        ...

    def get_scan_schedule(
        self, owner_id: str
    ) -> DiscoveryScanSchedule | None:
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

    def put_job_state(self, state: DiscoveryJobState) -> None:
        ...

    def get_job_state(
        self,
        owner_id: str,
        source_id: str,
        job_id: str,
    ) -> DiscoveryJobState | None:
        ...

    def list_job_states(self, owner_id: str) -> list[DiscoveryJobState]:
        ...

    def put_job_analysis(self, analysis: JobAnalysisRecord) -> None:
        ...

    def get_job_analysis(
        self,
        owner_id: str,
        job_id: str,
        description_fingerprint: str,
    ) -> JobAnalysisRecord | None:
        ...

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        ...

    def get_fit_snapshot(
        self,
        owner_id: str,
        job_id: str,
        profile_fingerprint: str,
        description_fingerprint: str = "",
    ) -> JobFitSnapshot | None:
        ...

    def list_fit_snapshots(
        self,
        owner_id: str,
        *,
        job_id: str | None = None,
    ) -> list[JobFitSnapshot]:
        ...

    def get_result_revision(self, owner_id: str) -> str:
        ...

    def replace_result_index(
        self,
        summary: DiscoveryResultIndexSummary,
        records: list[DiscoveryResultRecord],
    ) -> None:
        ...

    def get_result_index_summary(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
    ) -> DiscoveryResultIndexSummary | None:
        ...

    def list_result_records(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
        result_group: str,
    ) -> list[DiscoveryResultRecord]:
        ...

    def list_result_records_page(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
        result_group: str,
        *,
        offset: int,
        limit: int,
    ) -> list[DiscoveryResultRecord]:
        ...

    def get_public_catalog_status(
        self, source_key: str
    ) -> PublicJobCatalogStatus | None:
        ...

    def list_public_catalog_statuses(self) -> list[PublicJobCatalogStatus]:
        ...

    def list_public_catalog_jobs(
        self, source_key: str, *, active_only: bool = True
    ) -> list[DiscoveredJob]:
        ...

    def sync_public_catalog(
        self,
        source: CompanySource,
        source_key: str,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str,
        complete_scan: bool,
    ) -> PublicJobCatalogStatus:
        ...

    def try_acquire_public_refresh_lock(
        self,
        source_key: str,
        refresh_token: str,
        *,
        acquired_at: str,
        expires_at: str,
    ) -> bool:
        ...

    def release_public_refresh_lock(
        self, source_key: str, refresh_token: str
    ) -> None:
        ...

    def mark_public_catalog_failure(
        self,
        source: CompanySource,
        source_key: str,
        *,
        attempted_at: str,
        message: str,
    ) -> None:
        ...


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
