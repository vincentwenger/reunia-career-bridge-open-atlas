from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module
from .storage_base import DiscoveryStore

"""In-memory discovery persistence for tests and local development."""

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

def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
