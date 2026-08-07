from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module
from .storage_base import DiscoveryStore
from .storage_memory import InMemoryDiscoveryStore

"""JSON-file discovery persistence for local development."""

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

def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
