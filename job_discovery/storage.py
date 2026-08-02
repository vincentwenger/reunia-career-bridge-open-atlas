from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .models import (
    CompanySource,
    DiscoveredJob,
    DiscoveryJobDisposition,
    DiscoveryJobState,
    DiscoveryResultIndexSummary,
    DiscoveryResultRecord,
    DiscoverySearchPreferences,
    DiscoveryScanSchedule,
    DiscoveryScheduleCadence,
    JobAnalysisRecord,
    JobFitSnapshot,
    JobSourceType,
    PublicJobCatalogStatus,
    WorkplaceType,
    normalize_iso_timestamp,
    utc_now_iso,
)
from .public_catalog import PUBLIC_CATALOG_OWNER_ID, to_public_catalog_job

DISCOVERY_TABLE_CONFIG_KEY = "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME"
_SOURCE_PREFIX = "SOURCE#"
_JOB_PREFIX = "JOB#"
_FIT_PREFIX = "FIT#"
_ANALYSIS_PREFIX = "ANALYSIS#"
_STATE_PREFIX = "STATE#"
_RESULT_PREFIX = "RESULT#"
_RESULT_REVISION_KEY = "RESULT#REVISION"
_PREFERENCES_KEY = "PREFERENCES#SEARCH"
_SCHEDULE_KEY = "PREFERENCES#SCHEDULE"
_PUBLIC_SOURCE_PREFIX = "PUBLIC#SOURCE#"
_PUBLIC_JOB_PREFIX = "PUBLIC#JOB#"
_PUBLIC_LOCK_PREFIX = "PUBLIC#LOCK#"


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


class InMemoryDiscoveryStore:
    def __init__(self, *, clock: Callable[[], str] | None = None) -> None:
        self._clock = clock or utc_now_iso
        self._sources: dict[tuple[str, str], CompanySource] = {}
        self._preferences: dict[str, DiscoverySearchPreferences] = {}
        self._schedules: dict[str, DiscoveryScanSchedule] = {}
        self._jobs: dict[tuple[str, str, str], DiscoveredJob] = {}
        self._states: dict[tuple[str, str, str], DiscoveryJobState] = {}
        self._analyses: dict[tuple[str, str, str], JobAnalysisRecord] = {}
        self._fits: dict[tuple[str, str, str, str], JobFitSnapshot] = {}
        self._result_revisions: dict[str, str] = {}
        self._result_summaries: dict[tuple[str, str, str], DiscoveryResultIndexSummary] = {}
        self._result_records: dict[tuple[str, str, str, str, str, str], DiscoveryResultRecord] = {}
        self._public_catalog_statuses: dict[str, PublicJobCatalogStatus] = {}
        self._public_catalog_jobs: dict[tuple[str, str], DiscoveredJob] = {}
        self._public_refresh_locks: dict[str, tuple[str, str]] = {}
        self._lock = threading.RLock()

    def put_company_source(self, source: CompanySource) -> CompanySource:
        with self._lock:
            key = (source.owner_id, source.id)
            existing = self._sources.get(key)
            if existing is None:
                if source.revision != 0:
                    raise DiscoveryOptimisticLockError(
                        f"Cannot create source {source.id} at revision {source.revision}."
                    )
                stored = replace(source, revision=1)
            else:
                if source.revision != existing.revision:
                    raise DiscoveryOptimisticLockError(
                        f"Source {source.id} changed from revision {source.revision} "
                        f"to {existing.revision}. Reload it before updating."
                    )
                stored = replace(source, revision=existing.revision + 1)
            self._sources[key] = stored
            self._mark_result_dirty(source.owner_id)
            return stored

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
            deleted = self._sources.pop((owner_id, source_id), None) is not None
            if deleted:
                self._mark_result_dirty(owner_id)
            return deleted

    def put_search_preferences(
        self, preferences: DiscoverySearchPreferences
    ) -> DiscoverySearchPreferences:
        with self._lock:
            self._preferences[preferences.owner_id] = preferences
            self._mark_result_dirty(preferences.owner_id)
        return preferences

    def get_search_preferences(
        self, owner_id: str
    ) -> DiscoverySearchPreferences | None:
        with self._lock:
            return self._preferences.get(owner_id)

    def put_scan_schedule(
        self, schedule: DiscoveryScanSchedule
    ) -> DiscoveryScanSchedule:
        with self._lock:
            self._schedules[schedule.owner_id] = schedule
        return schedule

    def get_scan_schedule(
        self, owner_id: str
    ) -> DiscoveryScanSchedule | None:
        with self._lock:
            return self._schedules.get(owner_id)

    def sync_discovered_jobs(
        self,
        source: CompanySource,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str | None = None,
    ) -> list[DiscoveredJob]:
        checked = normalize_iso_timestamp(checked_at) or self._clock()
        _validate_sync(source, jobs)
        threshold = _deactivation_threshold(source)
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
                missing = previous.missed(threshold)
                self._jobs[(missing.owner_id, missing.source_id, missing.id)] = missing

            stored_source = self._sources.get((source.owner_id, source.id))
            effective_source = source
            if stored_source is not None and source.revision == 0:
                effective_source = replace(source, revision=stored_source.revision)
            self.put_company_source(effective_source.checked(checked))
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

    def put_job_state(self, state: DiscoveryJobState) -> None:
        with self._lock:
            job = self._jobs.get((state.owner_id, state.source_id, state.job_id))
            if job is None:
                raise ValueError("The discovered job does not exist.")
            self._states[(state.owner_id, state.source_id, state.job_id)] = state
            self._mark_result_dirty(state.owner_id)

    def get_job_state(
        self,
        owner_id: str,
        source_id: str,
        job_id: str,
    ) -> DiscoveryJobState | None:
        with self._lock:
            return self._states.get((owner_id, source_id, job_id))

    def list_job_states(self, owner_id: str) -> list[DiscoveryJobState]:
        with self._lock:
            states = [
                state
                for (stored_owner, _, _), state in self._states.items()
                if stored_owner == owner_id
            ]
        return sorted(states, key=lambda item: item.updated_at, reverse=True)

    def put_job_analysis(self, analysis: JobAnalysisRecord) -> None:
        with self._lock:
            self._analyses[(analysis.owner_id, analysis.job_id, analysis.description_fingerprint)] = analysis

    def get_job_analysis(
        self,
        owner_id: str,
        job_id: str,
        description_fingerprint: str,
    ) -> JobAnalysisRecord | None:
        with self._lock:
            return self._analyses.get((owner_id, job_id, description_fingerprint))

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        with self._lock:
            self._fits[(
                snapshot.owner_id,
                snapshot.job_id,
                snapshot.profile_fingerprint,
                snapshot.description_fingerprint,
            )] = snapshot
            self._mark_result_dirty(snapshot.owner_id)

    def get_fit_snapshot(
        self,
        owner_id: str,
        job_id: str,
        profile_fingerprint: str,
        description_fingerprint: str = "",
    ) -> JobFitSnapshot | None:
        with self._lock:
            if description_fingerprint:
                return self._fits.get((owner_id, job_id, profile_fingerprint, description_fingerprint))
            matches = [
                snapshot
                for (owner, stored_job_id, stored_profile, _), snapshot in self._fits.items()
                if owner == owner_id
                and stored_job_id == job_id
                and stored_profile == profile_fingerprint
            ]
        return max(matches, key=lambda item: item.analyzed_at, default=None)

    def list_fit_snapshots(
        self,
        owner_id: str,
        *,
        job_id: str | None = None,
    ) -> list[JobFitSnapshot]:
        with self._lock:
            snapshots = [
                snapshot
                for (owner, stored_job_id, _, _), snapshot in self._fits.items()
                if owner == owner_id and (job_id is None or stored_job_id == job_id)
            ]
        return sorted(snapshots, key=lambda item: (item.analyzed_at, item.job_id), reverse=True)

    def _mark_result_dirty(self, owner_id: str) -> str:
        token = uuid.uuid4().hex
        self._result_revisions[owner_id] = token
        return token

    def get_result_revision(self, owner_id: str) -> str:
        with self._lock:
            token = self._result_revisions.get(owner_id)
            if token is None:
                token = self._mark_result_dirty(owner_id)
            return token

    def replace_result_index(
        self,
        summary: DiscoveryResultIndexSummary,
        records: list[DiscoveryResultRecord],
    ) -> None:
        with self._lock:
            current_revision = self.get_result_revision(summary.owner_id)
            if summary.revision_token != current_revision:
                return
            prefix = (
                summary.owner_id,
                summary.evidence_fingerprint,
                summary.preference_fingerprint,
            )
            stale_summaries = [
                key for key in self._result_summaries if key[0] == summary.owner_id
            ]
            for key in stale_summaries:
                self._result_summaries.pop(key, None)
            self._result_summaries[prefix] = summary
            stale_keys = [
                key for key in self._result_records if key[0] == summary.owner_id
            ]
            for key in stale_keys:
                self._result_records.pop(key, None)
            for record in records:
                if (
                    record.owner_id != summary.owner_id
                    or record.evidence_fingerprint != summary.evidence_fingerprint
                    or record.preference_fingerprint != summary.preference_fingerprint
                ):
                    raise ValueError("result record does not belong to the supplied index")
                key = (
                    record.owner_id,
                    record.evidence_fingerprint,
                    record.preference_fingerprint,
                    record.result_group,
                    record.sort_rank,
                    record.job.id,
                )
                self._result_records[key] = record

    def get_result_index_summary(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
    ) -> DiscoveryResultIndexSummary | None:
        with self._lock:
            return self._result_summaries.get(
                (owner_id, evidence_fingerprint, preference_fingerprint)
            )

    def list_result_records(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
        result_group: str,
    ) -> list[DiscoveryResultRecord]:
        group = str(result_group or "").strip().casefold()
        with self._lock:
            records = [
                record
                for key, record in self._result_records.items()
                if key[:4] == (
                    owner_id,
                    evidence_fingerprint,
                    preference_fingerprint,
                    group,
                )
            ]
        return sorted(records, key=lambda item: (item.sort_rank, item.job.id))

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
        records = self.list_result_records(
            owner_id,
            evidence_fingerprint,
            preference_fingerprint,
            result_group,
        )
        start = max(0, int(offset))
        size = max(0, int(limit))
        return records[start : start + size]


    def get_public_catalog_status(
        self, source_key: str
    ) -> PublicJobCatalogStatus | None:
        with self._lock:
            return self._public_catalog_statuses.get(str(source_key))

    def list_public_catalog_statuses(self) -> list[PublicJobCatalogStatus]:
        with self._lock:
            statuses = list(self._public_catalog_statuses.values())
        return sorted(statuses, key=lambda item: (item.company_name.casefold(), item.source_key))

    def list_public_catalog_jobs(
        self, source_key: str, *, active_only: bool = True
    ) -> list[DiscoveredJob]:
        with self._lock:
            jobs = [
                job
                for (stored_key, _), job in self._public_catalog_jobs.items()
                if stored_key == source_key
            ]
        if active_only:
            jobs = [job for job in jobs if job.active]
        return sorted(jobs, key=_job_sort_key)

    def sync_public_catalog(
        self,
        source: CompanySource,
        source_key: str,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str,
        complete_scan: bool,
    ) -> PublicJobCatalogStatus:
        checked = normalize_iso_timestamp(checked_at) or self._clock()
        public_jobs = [to_public_catalog_job(job, source_key) for job in jobs]
        threshold = _deactivation_threshold(source)
        with self._lock:
            existing = {
                job.external_job_id: job
                for (stored_key, _), job in self._public_catalog_jobs.items()
                if stored_key == source_key
            }
            seen_external_ids: set[str] = set()
            for job in public_jobs:
                previous = existing.get(job.external_job_id)
                current = job.seen(
                    checked,
                    first_seen_at=previous.first_seen_at if previous else checked,
                )
                self._public_catalog_jobs[(source_key, current.id)] = current
                seen_external_ids.add(current.external_job_id)
            if complete_scan:
                for external_id, previous in existing.items():
                    if external_id in seen_external_ids or not previous.active:
                        continue
                    missed = previous.missed(threshold)
                    self._public_catalog_jobs[(source_key, missed.id)] = missed
            active_count = sum(
                1
                for (stored_key, _), item in self._public_catalog_jobs.items()
                if stored_key == source_key and item.active
            )
            status = PublicJobCatalogStatus(
                source_key=source_key,
                source_type=source.source_type,
                source_identifier=source.source_identifier,
                careers_url=source.careers_url,
                company_name=source.company_name,
                last_success_at=checked,
                last_attempt_at=checked,
                job_count=active_count,
                complete_scan=complete_scan,
                last_error="",
            )
            self._public_catalog_statuses[source_key] = status
            return status

    def try_acquire_public_refresh_lock(
        self,
        source_key: str,
        refresh_token: str,
        *,
        acquired_at: str,
        expires_at: str,
    ) -> bool:
        now = normalize_iso_timestamp(acquired_at) or self._clock()
        expiry = normalize_iso_timestamp(expires_at)
        with self._lock:
            current = self._public_refresh_locks.get(source_key)
            if current is not None and current[1] > now:
                return False
            self._public_refresh_locks[source_key] = (refresh_token, expiry)
            return True

    def release_public_refresh_lock(
        self, source_key: str, refresh_token: str
    ) -> None:
        with self._lock:
            current = self._public_refresh_locks.get(source_key)
            if current is not None and current[0] == refresh_token:
                self._public_refresh_locks.pop(source_key, None)

    def mark_public_catalog_failure(
        self,
        source: CompanySource,
        source_key: str,
        *,
        attempted_at: str,
        message: str,
    ) -> None:
        with self._lock:
            previous = self._public_catalog_statuses.get(source_key)
            self._public_catalog_statuses[source_key] = PublicJobCatalogStatus(
                source_key=source_key,
                source_type=source.source_type,
                source_identifier=source.source_identifier,
                careers_url=source.careers_url,
                company_name=source.company_name,
                last_success_at=previous.last_success_at if previous else "",
                last_attempt_at=attempted_at,
                job_count=previous.job_count if previous else 0,
                complete_scan=previous.complete_scan if previous else False,
                last_error=message,
            )


class JsonFileDiscoveryStore(InMemoryDiscoveryStore):
    """Local development adapter with the same discovery-specific contract."""

    def __init__(self, path: str | Path, *, clock: Callable[[], str] | None = None) -> None:
        self.path = Path(path)
        self._loading = True
        super().__init__(clock=clock)
        self._load()
        self._loading = False

    def put_company_source(self, source: CompanySource) -> CompanySource:
        stored = super().put_company_source(source)
        self._save()
        return stored

    def delete_company_source(self, owner_id: str, source_id: str) -> bool:
        deleted = super().delete_company_source(owner_id, source_id)
        if deleted:
            self._save()
        return deleted

    def put_search_preferences(
        self, preferences: DiscoverySearchPreferences
    ) -> DiscoverySearchPreferences:
        stored = super().put_search_preferences(preferences)
        self._save()
        return stored

    def put_scan_schedule(
        self, schedule: DiscoveryScanSchedule
    ) -> DiscoveryScanSchedule:
        stored = super().put_scan_schedule(schedule)
        self._save()
        return stored

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

    def put_job_state(self, state: DiscoveryJobState) -> None:
        super().put_job_state(state)
        self._save()

    def put_job_analysis(self, analysis: JobAnalysisRecord) -> None:
        super().put_job_analysis(analysis)
        self._save()

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        super().put_fit_snapshot(snapshot)
        self._save()

    def replace_result_index(
        self,
        summary: DiscoveryResultIndexSummary,
        records: list[DiscoveryResultRecord],
    ) -> None:
        super().replace_result_index(summary, records)
        self._save()

    def sync_public_catalog(
        self,
        source: CompanySource,
        source_key: str,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str,
        complete_scan: bool,
    ) -> PublicJobCatalogStatus:
        status = super().sync_public_catalog(
            source, source_key, jobs, checked_at=checked_at, complete_scan=complete_scan
        )
        self._save()
        return status

    def try_acquire_public_refresh_lock(
        self,
        source_key: str,
        refresh_token: str,
        *,
        acquired_at: str,
        expires_at: str,
    ) -> bool:
        acquired = super().try_acquire_public_refresh_lock(
            source_key, refresh_token, acquired_at=acquired_at, expires_at=expires_at
        )
        if acquired:
            self._save()
        return acquired

    def release_public_refresh_lock(
        self, source_key: str, refresh_token: str
    ) -> None:
        super().release_public_refresh_lock(source_key, refresh_token)
        self._save()

    def mark_public_catalog_failure(
        self,
        source: CompanySource,
        source_key: str,
        *,
        attempted_at: str,
        message: str,
    ) -> None:
        super().mark_public_catalog_failure(
            source, source_key, attempted_at=attempted_at, message=message
        )
        self._save()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            sources = [_company_source_from_dict(item) for item in payload.get("sources", [])]
            preferences = [
                _search_preferences_from_dict(item)
                for item in payload.get("search_preferences", [])
            ]
            schedules = [
                _scan_schedule_from_dict(item)
                for item in payload.get("scan_schedules", [])
            ]
            jobs = [_job_from_dict(item) for item in payload.get("jobs", [])]
            states = [_state_from_dict(item) for item in payload.get("job_states", [])]
            analyses = [_analysis_from_dict(item) for item in payload.get("job_analyses", [])]
            fits = [_fit_from_dict(item) for item in payload.get("fit_snapshots", [])]
            result_revisions = {
                str(owner): str(token)
                for owner, token in dict(payload.get("result_revisions") or {}).items()
                if str(owner).strip() and str(token).strip()
            }
            result_summaries = [
                _result_summary_from_dict(item)
                for item in payload.get("result_summaries", [])
            ]
            result_records = [
                _result_record_from_dict(item)
                for item in payload.get("result_records", [])
            ]
            public_catalog_statuses = [
                _public_catalog_status_from_dict(item)
                for item in payload.get("public_catalog_statuses", [])
            ]
            public_catalog_jobs = [
                _job_from_dict(item) for item in payload.get("public_catalog_jobs", [])
            ]
            public_refresh_locks = {
                str(source_key): (str(value[0]), str(value[1]))
                for source_key, value in dict(payload.get("public_refresh_locks") or {}).items()
                if isinstance(value, (list, tuple)) and len(value) == 2
            }
        except (OSError, ValueError, TypeError, KeyError):
            return
        with self._lock:
            self._sources = {(item.owner_id, item.id): item for item in sources}
            self._preferences = {item.owner_id: item for item in preferences}
            self._schedules = {item.owner_id: item for item in schedules}
            self._jobs = {(item.owner_id, item.source_id, item.id): item for item in jobs}
            self._states = {
                (item.owner_id, item.source_id, item.job_id): item
                for item in states
            }
            self._analyses = {
                (item.owner_id, item.job_id, item.description_fingerprint): item
                for item in analyses
            }
            self._fits = {
                (item.owner_id, item.job_id, item.profile_fingerprint, item.description_fingerprint): item
                for item in fits
            }
            self._result_revisions = result_revisions
            self._result_summaries = {
                (item.owner_id, item.evidence_fingerprint, item.preference_fingerprint): item
                for item in result_summaries
            }
            self._result_records = {
                (
                    item.owner_id,
                    item.evidence_fingerprint,
                    item.preference_fingerprint,
                    item.result_group,
                    item.sort_rank,
                    item.job.id,
                ): item
                for item in result_records
            }
            self._public_catalog_statuses = {
                item.source_key: item for item in public_catalog_statuses
            }
            self._public_catalog_jobs = {
                (item.source_id, item.id): item for item in public_catalog_jobs
            }
            self._public_refresh_locks = public_refresh_locks

    def _save(self) -> None:
        if self._loading:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = {
                "sources": [_company_source_to_dict(item) for item in self._sources.values()],
                "search_preferences": [
                    _search_preferences_to_dict(item)
                    for item in self._preferences.values()
                ],
                "scan_schedules": [
                    _scan_schedule_to_dict(item)
                    for item in self._schedules.values()
                ],
                "jobs": [_job_to_dict(item) for item in self._jobs.values()],
                "job_states": [_state_to_dict(item) for item in self._states.values()],
                "job_analyses": [_analysis_to_dict(item) for item in self._analyses.values()],
                "fit_snapshots": [_fit_to_dict(item) for item in self._fits.values()],
                "result_revisions": dict(self._result_revisions),
                "result_summaries": [
                    _result_summary_to_dict(item)
                    for item in self._result_summaries.values()
                ],
                "result_records": [
                    _result_record_to_dict(item)
                    for item in self._result_records.values()
                ],
                "public_catalog_statuses": [
                    _public_catalog_status_to_dict(item)
                    for item in self._public_catalog_statuses.values()
                ],
                "public_catalog_jobs": [
                    _job_to_dict(item) for item in self._public_catalog_jobs.values()
                ],
                "public_refresh_locks": {
                    key: list(value) for key, value in self._public_refresh_locks.items()
                },
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
    * ``STATE#<source_id>#<job_id>``
    * ``PREFERENCES#SEARCH``
    * ``PREFERENCES#SCHEDULE``
    * ``ANALYSIS#<job_id>#<description_fingerprint>``
    * ``FIT#<job_id>#<profile_fingerprint>#<description_fingerprint>``

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

    def _put_many(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        table = self._table()
        batch_writer = getattr(table, "batch_writer", None)
        if not callable(batch_writer):
            for item in items:
                self._put(item)
            return
        with batch_writer(overwrite_by_pkeys=["owner_id", "storage_key"]) as batch:
            for item in items:
                batch.put_item(Item=_to_dynamodb(item))

    def _delete_many(self, keys: list[dict[str, str]]) -> None:
        if not keys:
            return
        table = self._table()
        batch_writer = getattr(table, "batch_writer", None)
        if callable(batch_writer):
            with batch_writer(overwrite_by_pkeys=["owner_id", "storage_key"]) as batch:
                delete_item = getattr(batch, "delete_item", None)
                if callable(delete_item):
                    for key in keys:
                        delete_item(Key=key)
                    return
        for key in keys:
            table.delete_item(Key=key)

    def _mark_result_dirty(self, owner_id: str) -> str:
        token = uuid.uuid4().hex
        self._put(
            {
                "owner_id": owner_id,
                "storage_key": _RESULT_REVISION_KEY,
                "entity_type": "discovery_result_revision",
                "revision_token": token,
            }
        )
        return token

    def _put_versioned_source(self, source: CompanySource) -> CompanySource:
        expected_revision = int(source.revision)
        stored = _source_item(replace(source, revision=expected_revision + 1))
        values: dict[str, Any] = {}
        if expected_revision == 0:
            condition = "attribute_not_exists(#storage_key)"
            names = {"#storage_key": "storage_key"}
        else:
            condition = "#revision = :expected_revision"
            names = {"#revision": "revision"}
            values[":expected_revision"] = expected_revision
        kwargs: dict[str, Any] = {
            "Item": _to_dynamodb(stored),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
        }
        if values:
            kwargs["ExpressionAttributeValues"] = _to_dynamodb(values)
        try:
            self._table().put_item(**kwargs)
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
            if code == "ConditionalCheckFailedException":
                raise DiscoveryOptimisticLockError(
                    f"Source {source.id} was updated by another process; reload before saving."
                ) from exc
            raise
        return _company_source_from_dict(stored)

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

    def _query_range(
        self,
        owner_id: str,
        start_key: str,
        end_key: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        remaining = max(0, int(limit))
        if remaining == 0:
            return []
        query_args: dict[str, Any] = {
            "KeyConditionExpression": (
                "#owner_id = :owner_id AND #storage_key BETWEEN :start_key AND :end_key"
            ),
            "ExpressionAttributeNames": {
                "#owner_id": "owner_id",
                "#storage_key": "storage_key",
            },
            "ExpressionAttributeValues": {
                ":owner_id": owner_id,
                ":start_key": start_key,
                ":end_key": end_key,
            },
            "Limit": remaining,
        }
        items: list[dict[str, Any]] = []
        while remaining > 0:
            query_args["Limit"] = remaining
            response = self._table().query(**query_args)
            page = [_from_dynamodb(item) for item in response.get("Items", [])]
            items.extend(page)
            remaining = max(0, limit - len(items))
            last_key = response.get("LastEvaluatedKey")
            if not last_key or remaining == 0:
                return items
            query_args["ExclusiveStartKey"] = last_key
        return items

    def put_company_source(self, source: CompanySource) -> CompanySource:
        stored = self._put_versioned_source(source)
        self._mark_result_dirty(source.owner_id)
        return stored

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
        deleted = bool(response.get("Attributes"))
        if deleted:
            self._mark_result_dirty(owner_id)
        return deleted

    def put_search_preferences(
        self, preferences: DiscoverySearchPreferences
    ) -> DiscoverySearchPreferences:
        self._put(_search_preferences_item(preferences))
        self._mark_result_dirty(preferences.owner_id)
        return preferences

    def get_search_preferences(
        self, owner_id: str
    ) -> DiscoverySearchPreferences | None:
        item = self._get(owner_id, _PREFERENCES_KEY)
        return _search_preferences_from_dict(item) if item else None

    def put_scan_schedule(
        self, schedule: DiscoveryScanSchedule
    ) -> DiscoveryScanSchedule:
        self._put(_scan_schedule_item(schedule))
        return schedule

    def get_scan_schedule(
        self, owner_id: str
    ) -> DiscoveryScanSchedule | None:
        item = self._get(owner_id, _SCHEDULE_KEY)
        return _scan_schedule_from_dict(item) if item else None

    def sync_discovered_jobs(
        self,
        source: CompanySource,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str | None = None,
    ) -> list[DiscoveredJob]:
        checked = normalize_iso_timestamp(checked_at) or self._clock()
        _validate_sync(source, jobs)
        threshold = _deactivation_threshold(source)
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
        job_items: list[dict[str, Any]] = []
        for job in jobs:
            previous = existing.get(job.external_job_id)
            current = job.seen(
                checked,
                first_seen_at=previous.first_seen_at if previous else checked,
            )
            job_items.append(_job_item(current))
            synchronized.append(current)
            seen_external_ids.add(current.external_job_id)

        for external_id, previous in existing.items():
            if external_id in seen_external_ids or not previous.active:
                continue
            job_items.append(_job_item(previous.missed(threshold)))

        self._put_many(job_items)
        self._mark_result_dirty(source.owner_id)
        stored_source = self.get_company_source(source.owner_id, source.id)
        effective_source = source
        if stored_source is not None and source.revision == 0:
            effective_source = replace(source, revision=stored_source.revision)
        self._put_versioned_source(effective_source.checked(checked))
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

    def put_job_state(self, state: DiscoveryJobState) -> None:
        if self.get_discovered_job(state.owner_id, state.source_id, state.job_id) is None:
            raise ValueError("The discovered job does not exist.")
        self._put(_state_item(state))
        self._mark_result_dirty(state.owner_id)

    def get_job_state(
        self,
        owner_id: str,
        source_id: str,
        job_id: str,
    ) -> DiscoveryJobState | None:
        item = self._get(owner_id, _state_key(source_id, job_id))
        return _state_from_dict(item) if item else None

    def list_job_states(self, owner_id: str) -> list[DiscoveryJobState]:
        states = [_state_from_dict(item) for item in self._query_prefix(owner_id, _STATE_PREFIX)]
        return sorted(states, key=lambda item: item.updated_at, reverse=True)

    def put_job_analysis(self, analysis: JobAnalysisRecord) -> None:
        self._put(_analysis_item(analysis))

    def get_job_analysis(
        self,
        owner_id: str,
        job_id: str,
        description_fingerprint: str,
    ) -> JobAnalysisRecord | None:
        item = self._get(owner_id, _analysis_key(job_id, description_fingerprint))
        return _analysis_from_dict(item) if item else None

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        self._put(_fit_item(snapshot))
        self._mark_result_dirty(snapshot.owner_id)

    def get_fit_snapshot(
        self,
        owner_id: str,
        job_id: str,
        profile_fingerprint: str,
        description_fingerprint: str = "",
    ) -> JobFitSnapshot | None:
        if description_fingerprint:
            item = self._get(
                owner_id,
                _fit_key(job_id, profile_fingerprint, description_fingerprint),
            )
            return _fit_from_dict(item) if item else None
        prefix = _fit_key(job_id, profile_fingerprint, "")
        matches = [_fit_from_dict(item) for item in self._query_prefix(owner_id, prefix)]
        return max(matches, key=lambda item: item.analyzed_at, default=None)

    def list_fit_snapshots(
        self,
        owner_id: str,
        *,
        job_id: str | None = None,
    ) -> list[JobFitSnapshot]:
        prefix = f"{_FIT_PREFIX}{job_id}#" if job_id else _FIT_PREFIX
        snapshots = [_fit_from_dict(item) for item in self._query_prefix(owner_id, prefix)]
        return sorted(snapshots, key=lambda item: (item.analyzed_at, item.job_id), reverse=True)

    def get_result_revision(self, owner_id: str) -> str:
        item = self._get(owner_id, _RESULT_REVISION_KEY)
        if item is not None and str(item.get("revision_token") or "").strip():
            return str(item["revision_token"])
        return self._mark_result_dirty(owner_id)

    def replace_result_index(
        self,
        summary: DiscoveryResultIndexSummary,
        records: list[DiscoveryResultRecord],
    ) -> None:
        current_revision = self.get_result_revision(summary.owner_id)
        if summary.revision_token != current_revision:
            return
        stale_items = [
            item
            for item in self._query_prefix(summary.owner_id, _RESULT_PREFIX)
            if str(item.get("entity_type") or "").startswith(
                "discovery_result_index"
            )
            or item.get("entity_type") == "discovery_result_record"
        ]
        self._delete_many(
            [self._key(summary.owner_id, str(item["storage_key"])) for item in stale_items]
        )
        for record in records:
            if (
                record.owner_id != summary.owner_id
                or record.evidence_fingerprint != summary.evidence_fingerprint
                or record.preference_fingerprint != summary.preference_fingerprint
            ):
                raise ValueError("result record does not belong to the supplied index")
        self._put_many([_result_record_item(item) for item in records])
        if self.get_result_revision(summary.owner_id) == summary.revision_token:
            self._put(_result_summary_item(summary))

    def get_result_index_summary(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
    ) -> DiscoveryResultIndexSummary | None:
        item = self._get(
            owner_id,
            _result_summary_key(evidence_fingerprint, preference_fingerprint),
        )
        return _result_summary_from_dict(item) if item else None

    def list_result_records(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
        result_group: str,
    ) -> list[DiscoveryResultRecord]:
        prefix = _result_group_prefix(
            evidence_fingerprint,
            preference_fingerprint,
            result_group,
        )
        return [
            _result_record_from_dict(item)
            for item in self._query_prefix(owner_id, prefix)
        ]

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
        prefix = _result_group_prefix(
            evidence_fingerprint,
            preference_fingerprint,
            result_group,
        )
        start = max(0, int(offset))
        size = max(0, int(limit))
        if size == 0:
            return []
        start_key = prefix + f"{start:08d}#"
        end_key = prefix + f"{start + size - 1:08d}#\uffff"
        return [
            _result_record_from_dict(item)
            for item in self._query_range(
                owner_id, start_key, end_key, limit=size
            )
        ]


    def get_public_catalog_status(
        self, source_key: str
    ) -> PublicJobCatalogStatus | None:
        item = self._get(PUBLIC_CATALOG_OWNER_ID, _public_source_key(source_key))
        return _public_catalog_status_from_dict(item) if item else None

    def list_public_catalog_statuses(self) -> list[PublicJobCatalogStatus]:
        statuses = [
            _public_catalog_status_from_dict(item)
            for item in self._query_prefix(
                PUBLIC_CATALOG_OWNER_ID, _PUBLIC_SOURCE_PREFIX
            )
        ]
        return sorted(statuses, key=lambda item: (item.company_name.casefold(), item.source_key))

    def list_public_catalog_jobs(
        self, source_key: str, *, active_only: bool = True
    ) -> list[DiscoveredJob]:
        jobs = [
            _job_from_dict(item)
            for item in self._query_prefix(
                PUBLIC_CATALOG_OWNER_ID, _public_job_group_prefix(source_key)
            )
        ]
        if active_only:
            jobs = [job for job in jobs if job.active]
        return sorted(jobs, key=_job_sort_key)

    def sync_public_catalog(
        self,
        source: CompanySource,
        source_key: str,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str,
        complete_scan: bool,
    ) -> PublicJobCatalogStatus:
        checked = normalize_iso_timestamp(checked_at) or self._clock()
        threshold = _deactivation_threshold(source)
        existing = {
            job.external_job_id: job
            for job in self.list_public_catalog_jobs(source_key, active_only=False)
        }
        seen_external_ids: set[str] = set()
        projected = dict(existing)
        items: list[dict[str, Any]] = []
        for job in jobs:
            public_job = to_public_catalog_job(job, source_key)
            previous = existing.get(public_job.external_job_id)
            current = public_job.seen(
                checked,
                first_seen_at=previous.first_seen_at if previous else checked,
            )
            projected[current.external_job_id] = current
            items.append(_public_job_item(current, source_key))
            seen_external_ids.add(current.external_job_id)
        if complete_scan:
            for external_id, previous in existing.items():
                if external_id in seen_external_ids or not previous.active:
                    continue
                missed = previous.missed(threshold)
                projected[external_id] = missed
                items.append(_public_job_item(missed, source_key))
        self._put_many(items)
        active_count = sum(1 for item in projected.values() if item.active)
        status = PublicJobCatalogStatus(
            source_key=source_key,
            source_type=source.source_type,
            source_identifier=source.source_identifier,
            careers_url=source.careers_url,
            company_name=source.company_name,
            last_success_at=checked,
            last_attempt_at=checked,
            job_count=active_count,
            complete_scan=complete_scan,
            last_error="",
        )
        self._put(_public_catalog_status_item(status))
        return status

    def try_acquire_public_refresh_lock(
        self,
        source_key: str,
        refresh_token: str,
        *,
        acquired_at: str,
        expires_at: str,
    ) -> bool:
        now = normalize_iso_timestamp(acquired_at) or self._clock()
        expiry = normalize_iso_timestamp(expires_at)
        storage_key = _public_lock_key(source_key)
        existing = self._get(PUBLIC_CATALOG_OWNER_ID, storage_key)
        if existing and str(existing.get("expires_at") or "") > now:
            return False
        if existing:
            try:
                self._table().delete_item(
                    Key=self._key(PUBLIC_CATALOG_OWNER_ID, storage_key),
                    ConditionExpression="#refresh_token = :refresh_token",
                    ExpressionAttributeNames={"#refresh_token": "refresh_token"},
                    ExpressionAttributeValues=_to_dynamodb(
                        {":refresh_token": str(existing.get("refresh_token") or "")}
                    ),
                )
            except Exception as exc:
                response = getattr(exc, "response", {}) or {}
                code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
                if code == "ConditionalCheckFailedException":
                    return False
                raise
        item = {
            "owner_id": PUBLIC_CATALOG_OWNER_ID,
            "storage_key": storage_key,
            "entity_type": "public_job_catalog_refresh_lock",
            "refresh_token": str(refresh_token),
            "acquired_at": now,
            "expires_at": expiry,
            "ttl": int(datetime.fromisoformat(expiry).timestamp()),
        }
        try:
            self._table().put_item(
                Item=_to_dynamodb(item),
                ConditionExpression="attribute_not_exists(#storage_key)",
                ExpressionAttributeNames={"#storage_key": "storage_key"},
            )
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
            if code == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def release_public_refresh_lock(
        self, source_key: str, refresh_token: str
    ) -> None:
        storage_key = _public_lock_key(source_key)
        existing = self._get(PUBLIC_CATALOG_OWNER_ID, storage_key)
        if existing and str(existing.get("refresh_token") or "") == str(refresh_token):
            try:
                self._table().delete_item(
                    Key=self._key(PUBLIC_CATALOG_OWNER_ID, storage_key),
                    ConditionExpression="#refresh_token = :refresh_token",
                    ExpressionAttributeNames={"#refresh_token": "refresh_token"},
                    ExpressionAttributeValues=_to_dynamodb(
                        {":refresh_token": str(refresh_token)}
                    ),
                )
            except Exception as exc:
                response = getattr(exc, "response", {}) or {}
                code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
                if code != "ConditionalCheckFailedException":
                    raise

    def mark_public_catalog_failure(
        self,
        source: CompanySource,
        source_key: str,
        *,
        attempted_at: str,
        message: str,
    ) -> None:
        previous = self.get_public_catalog_status(source_key)
        status = PublicJobCatalogStatus(
            source_key=source_key,
            source_type=source.source_type,
            source_identifier=source.source_identifier,
            careers_url=source.careers_url,
            company_name=source.company_name,
            last_success_at=previous.last_success_at if previous else "",
            last_attempt_at=attempted_at,
            job_count=previous.job_count if previous else 0,
            complete_scan=previous.complete_scan if previous else False,
            last_error=message,
        )
        self._put(_public_catalog_status_item(status))


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


def _deactivation_threshold(source: CompanySource, default: int = 3) -> int:
    try:
        threshold = int(source.filters.get("deactivate_after_missed_scans", default))
    except (TypeError, ValueError):
        threshold = default
    return min(max(threshold, 2), 10)


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


def _state_key(source_id: str, job_id: str) -> str:
    return f"{_STATE_PREFIX}{source_id}#{job_id}"


def _analysis_key(job_id: str, description_fingerprint: str) -> str:
    return f"{_ANALYSIS_PREFIX}{job_id}#{description_fingerprint}"


def _fit_key(job_id: str, profile_fingerprint: str, description_fingerprint: str) -> str:
    suffix = f"#{description_fingerprint}" if description_fingerprint else "#"
    return f"{_FIT_PREFIX}{job_id}#{profile_fingerprint}{suffix}"


def _result_index_prefix(evidence_fingerprint: str, preference_fingerprint: str) -> str:
    return f"{_RESULT_PREFIX}{evidence_fingerprint}#{preference_fingerprint}#"


def _result_summary_key(evidence_fingerprint: str, preference_fingerprint: str) -> str:
    return _result_index_prefix(evidence_fingerprint, preference_fingerprint) + "META"


def _result_group_prefix(
    evidence_fingerprint: str,
    preference_fingerprint: str,
    result_group: str,
) -> str:
    group = str(result_group or "").strip().casefold()
    if group not in {"recommended", "possible", "pending", "low_match", "saved", "ignored"}:
        raise ValueError("Unknown result group")
    return _result_index_prefix(evidence_fingerprint, preference_fingerprint) + f"GROUP#{group}#"


def _result_record_key(record: DiscoveryResultRecord) -> str:
    return (
        _result_group_prefix(
            record.evidence_fingerprint,
            record.preference_fingerprint,
            record.result_group,
        )
        + f"{record.sort_rank}#{record.job.source_id}#{record.job.id}"
    )


def _public_source_key(source_key: str) -> str:
    return f"{_PUBLIC_SOURCE_PREFIX}{source_key}"


def _public_job_group_prefix(source_key: str) -> str:
    return f"{_PUBLIC_JOB_PREFIX}{source_key}#"


def _public_job_key(source_key: str, job_id: str) -> str:
    return _public_job_group_prefix(source_key) + job_id


def _public_lock_key(source_key: str) -> str:
    return f"{_PUBLIC_LOCK_PREFIX}{source_key}"


def _job_sort_key(job: DiscoveredJob) -> tuple[str, str, str]:
    return job.company.casefold(), job.title.casefold(), job.external_job_id


def _public_catalog_status_item(status: PublicJobCatalogStatus) -> dict[str, Any]:
    item = _public_catalog_status_to_dict(status)
    item.update(
        {
            "owner_id": PUBLIC_CATALOG_OWNER_ID,
            "storage_key": _public_source_key(status.source_key),
            "entity_type": "public_job_catalog_status",
        }
    )
    return item


def _public_job_item(job: DiscoveredJob, source_key: str) -> dict[str, Any]:
    item = _job_to_dict(job)
    item.update(
        {
            "owner_id": PUBLIC_CATALOG_OWNER_ID,
            "storage_key": _public_job_key(source_key, job.id),
            "entity_type": "public_job_catalog_posting",
        }
    )
    return item


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


def _search_preferences_item(
    preferences: DiscoverySearchPreferences,
) -> dict[str, Any]:
    item = _search_preferences_to_dict(preferences)
    item.update(
        {
            "owner_id": preferences.owner_id,
            "storage_key": _PREFERENCES_KEY,
            "entity_type": "discovery_search_preferences",
        }
    )
    return item


def _scan_schedule_item(schedule: DiscoveryScanSchedule) -> dict[str, Any]:
    item = _scan_schedule_to_dict(schedule)
    item.update(
        {
            "owner_id": schedule.owner_id,
            "storage_key": _SCHEDULE_KEY,
            "entity_type": "discovery_scan_schedule",
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


def _state_item(state: DiscoveryJobState) -> dict[str, Any]:
    item = _state_to_dict(state)
    item.update(
        {
            "owner_id": state.owner_id,
            "storage_key": _state_key(state.source_id, state.job_id),
            "entity_type": "discovery_job_state",
        }
    )
    return item


def _analysis_item(analysis: JobAnalysisRecord) -> dict[str, Any]:
    item = _analysis_to_dict(analysis)
    item.update(
        {
            "owner_id": analysis.owner_id,
            "storage_key": _analysis_key(analysis.job_id, analysis.description_fingerprint),
            "entity_type": "job_analysis",
        }
    )
    return item


def _fit_item(snapshot: JobFitSnapshot) -> dict[str, Any]:
    item = _fit_to_dict(snapshot)
    item.update(
        {
            "owner_id": snapshot.owner_id,
            "storage_key": _fit_key(
                snapshot.job_id,
                snapshot.profile_fingerprint,
                snapshot.description_fingerprint,
            ),
            "entity_type": "job_fit_snapshot",
        }
    )
    return item


def _result_summary_item(summary: DiscoveryResultIndexSummary) -> dict[str, Any]:
    item = _result_summary_to_dict(summary)
    item.update(
        {
            "owner_id": summary.owner_id,
            "storage_key": _result_summary_key(
                summary.evidence_fingerprint,
                summary.preference_fingerprint,
            ),
            "entity_type": "discovery_result_index_summary",
        }
    )
    return item


def _result_record_item(record: DiscoveryResultRecord) -> dict[str, Any]:
    item = _result_record_to_dict(record)
    item.update(
        {
            "owner_id": record.owner_id,
            "storage_key": _result_record_key(record),
            "entity_type": "discovery_result_record",
        }
    )
    return item


def _public_catalog_status_to_dict(status: PublicJobCatalogStatus) -> dict[str, Any]:
    return {
        "source_key": status.source_key,
        "source_type": status.source_type.value,
        "source_identifier": status.source_identifier,
        "careers_url": status.careers_url,
        "company_name": status.company_name,
        "last_success_at": status.last_success_at,
        "last_attempt_at": status.last_attempt_at,
        "job_count": status.job_count,
        "complete_scan": status.complete_scan,
        "last_error": status.last_error,
    }


def _public_catalog_status_from_dict(data: Mapping[str, Any]) -> PublicJobCatalogStatus:
    return PublicJobCatalogStatus(
        source_key=str(data.get("source_key") or ""),
        source_type=str(data.get("source_type") or JobSourceType.GENERIC_JSONLD.value),
        source_identifier=str(data.get("source_identifier") or ""),
        careers_url=str(data.get("careers_url") or ""),
        company_name=str(data.get("company_name") or ""),
        last_success_at=str(data.get("last_success_at") or ""),
        last_attempt_at=str(data.get("last_attempt_at") or ""),
        job_count=int(data.get("job_count") or 0),
        complete_scan=bool(data.get("complete_scan", True)),
        last_error=str(data.get("last_error") or ""),
    )


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
        "revision": source.revision,
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
        revision=int(payload.get("revision") or 0),
    )


def _search_preferences_to_dict(
    preferences: DiscoverySearchPreferences,
) -> dict[str, Any]:
    return {
        "owner_id": preferences.owner_id,
        "target_titles": list(preferences.target_titles),
        "preferred_locations": list(preferences.preferred_locations),
        "accepted_workplace_types": [
            item.value for item in preferences.accepted_workplace_types
        ],
        "preferred_employment_types": list(preferences.preferred_employment_types),
        "preferred_keywords": list(preferences.preferred_keywords),
        "required_keywords": list(preferences.required_keywords),
        "minimum_salary": preferences.minimum_salary,
        "minimum_salary_currency": preferences.minimum_salary_currency,
        "minimum_salary_interval": preferences.minimum_salary_interval,
        "excluded_terms": list(preferences.excluded_terms),
        "excluded_title_terms": list(preferences.excluded_title_terms),
        "maximum_posting_age_days": preferences.maximum_posting_age_days,
        "require_title_match": preferences.require_title_match,
        "require_location_match": preferences.require_location_match,
        "require_workplace_match": preferences.require_workplace_match,
        "require_employment_type_match": preferences.require_employment_type_match,
        "updated_at": preferences.updated_at,
    }


def _search_preferences_from_dict(
    payload: Mapping[str, Any],
) -> DiscoverySearchPreferences:
    return DiscoverySearchPreferences(
        owner_id=str(payload.get("owner_id") or ""),
        target_titles=tuple(payload.get("target_titles") or ()),
        preferred_locations=tuple(payload.get("preferred_locations") or ()),
        accepted_workplace_types=tuple(payload.get("accepted_workplace_types") or ()),
        preferred_employment_types=tuple(
            payload.get("preferred_employment_types") or ()
        ),
        preferred_keywords=tuple(payload.get("preferred_keywords") or ()),
        required_keywords=tuple(payload.get("required_keywords") or ()),
        minimum_salary=payload.get("minimum_salary"),
        minimum_salary_currency=str(
            payload.get("minimum_salary_currency") or "USD"
        ),
        minimum_salary_interval=str(
            payload.get("minimum_salary_interval") or "year"
        ),
        excluded_terms=tuple(payload.get("excluded_terms") or ()),
        excluded_title_terms=tuple(payload.get("excluded_title_terms") or ()),
        maximum_posting_age_days=payload.get("maximum_posting_age_days", 30),
        require_title_match=bool(payload.get("require_title_match", False)),
        require_location_match=bool(
            payload.get("require_location_match", False)
        ),
        require_workplace_match=bool(
            payload.get("require_workplace_match", False)
        ),
        require_employment_type_match=bool(
            payload.get("require_employment_type_match", False)
        ),
        updated_at=str(payload.get("updated_at") or ""),
    )


def _scan_schedule_to_dict(schedule: DiscoveryScanSchedule) -> dict[str, Any]:
    return {
        "owner_id": schedule.owner_id,
        "cadence": schedule.cadence.value,
        "local_hour": schedule.local_hour,
        "weekday": schedule.weekday,
        "timezone_name": schedule.timezone_name,
        "last_run_at": schedule.last_run_at,
        "updated_at": schedule.updated_at,
    }


def _scan_schedule_from_dict(payload: Mapping[str, Any]) -> DiscoveryScanSchedule:
    return DiscoveryScanSchedule(
        owner_id=str(payload.get("owner_id") or ""),
        cadence=str(payload.get("cadence") or DiscoveryScheduleCadence.MANUAL.value),
        local_hour=int(payload.get("local_hour", 8)),
        weekday=int(payload.get("weekday", 0)),
        timezone_name=str(payload.get("timezone_name") or "UTC"),
        last_run_at=str(payload.get("last_run_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
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


def _state_to_dict(state: DiscoveryJobState) -> dict[str, Any]:
    return {
        "owner_id": state.owner_id,
        "source_id": state.source_id,
        "job_id": state.job_id,
        "disposition": state.disposition.value,
        "application_id": state.application_id,
        "updated_at": state.updated_at,
    }


def _state_from_dict(payload: Mapping[str, Any]) -> DiscoveryJobState:
    return DiscoveryJobState(
        owner_id=str(payload.get("owner_id") or ""),
        source_id=str(payload.get("source_id") or ""),
        job_id=str(payload.get("job_id") or ""),
        disposition=DiscoveryJobDisposition(str(payload.get("disposition") or "")),
        application_id=str(payload.get("application_id") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def _analysis_to_dict(analysis: JobAnalysisRecord) -> dict[str, Any]:
    payload = asdict(analysis)
    payload["requirements"] = [dict(item) for item in analysis.requirements]
    payload["ignored_boilerplate"] = list(analysis.ignored_boilerplate)
    return payload


def _analysis_from_dict(payload: Mapping[str, Any]) -> JobAnalysisRecord:
    allowed = {field.name for field in JobAnalysisRecord.__dataclass_fields__.values()}
    data = {key: value for key, value in payload.items() if key in allowed}
    data["requirements"] = tuple(dict(item) for item in data.get("requirements") or ())
    data["ignored_boilerplate"] = tuple(data.get("ignored_boilerplate") or ())
    return JobAnalysisRecord(**data)


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
        "evidence_matches",
    ):
        data[name] = tuple(data.get(name) or ())
    return JobFitSnapshot(**data)


def _result_summary_to_dict(summary: DiscoveryResultIndexSummary) -> dict[str, Any]:
    return asdict(summary)


def _result_summary_from_dict(
    payload: Mapping[str, Any],
) -> DiscoveryResultIndexSummary:
    allowed = {
        field.name for field in DiscoveryResultIndexSummary.__dataclass_fields__.values()
    }
    return DiscoveryResultIndexSummary(
        **{key: value for key, value in payload.items() if key in allowed}
    )


def _result_record_to_dict(record: DiscoveryResultRecord) -> dict[str, Any]:
    return {
        "owner_id": record.owner_id,
        "evidence_fingerprint": record.evidence_fingerprint,
        "preference_fingerprint": record.preference_fingerprint,
        "result_group": record.result_group,
        "job": _job_to_dict(record.job),
        "recommendation_tier": record.recommendation_tier,
        "confidence_tier": record.confidence_tier,
        "visibility_category": record.visibility_category,
        "disposition": record.disposition.value if record.disposition else "",
        "application_id": record.application_id,
        "fit": _fit_to_dict(record.fit) if record.fit is not None else None,
        "preference_score": record.preference_score,
        "freshness_score": record.freshness_score,
        "search_priority": record.search_priority,
        "posted_label": record.posted_label,
        "sort_rank": record.sort_rank,
        "updated_at": record.updated_at,
    }


def _result_record_from_dict(payload: Mapping[str, Any]) -> DiscoveryResultRecord:
    fit_payload = payload.get("fit")
    disposition = str(payload.get("disposition") or "").strip() or None
    return DiscoveryResultRecord(
        owner_id=str(payload.get("owner_id") or ""),
        evidence_fingerprint=str(payload.get("evidence_fingerprint") or ""),
        preference_fingerprint=str(payload.get("preference_fingerprint") or ""),
        result_group=str(payload.get("result_group") or ""),
        job=_job_from_dict(dict(payload.get("job") or {})),
        recommendation_tier=str(payload.get("recommendation_tier") or "unassessed"),
        confidence_tier=str(payload.get("confidence_tier") or "unassessed"),
        visibility_category=str(payload.get("visibility_category") or payload.get("result_group") or ""),
        disposition=disposition,
        application_id=str(payload.get("application_id") or ""),
        fit=_fit_from_dict(dict(fit_payload)) if fit_payload else None,
        preference_score=float(payload.get("preference_score") or 0),
        freshness_score=float(payload.get("freshness_score") or 0),
        search_priority=(
            float(payload["search_priority"])
            if payload.get("search_priority") is not None
            else None
        ),
        posted_label=str(payload.get("posted_label") or ""),
        sort_rank=str(payload.get("sort_rank") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


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
