from __future__ import annotations

from dataclasses import dataclass
from products.resume_taylor.resume_tailor.application_tracker import ApplicationRecord
from products.resume_taylor.resume_tailor.storage import ApplicationStore

from .models import (
    DiscoveryJobDisposition,
    DiscoveryJobState,
    DiscoveredJob,
    JobFitSnapshot,
    utc_now_iso,
)
from .storage import DiscoveryStore


# MVP safeguard: discovery may create an internal workspace only. It never
# submits forms, uploads documents, or sends an application to an employer.
AUTOMATIC_APPLICATION_SUBMISSION_SUPPORTED = False
MVP_APPLICATION_SUBMISSION_MODE = "manual_workspace_only"


@dataclass(frozen=True, slots=True)
class ApplicationWorkspaceResult:
    application: ApplicationRecord
    job: DiscoveredJob
    fit_snapshot: JobFitSnapshot | None
    created: bool


class DiscoveredJobApplicationService:
    """Manage user actions and explicit promotion into Job Applications."""

    def __init__(
        self,
        discovery_store: DiscoveryStore,
        application_store: ApplicationStore,
    ) -> None:
        self.discovery_store = discovery_store
        self.application_store = application_store

    def save(self, owner_id: str, source_id: str, job_id: str) -> DiscoveryJobState:
        job = self._job(owner_id, source_id, job_id)
        state = DiscoveryJobState(
            owner_id=owner_id,
            source_id=source_id,
            job_id=job.id,
            disposition=DiscoveryJobDisposition.SAVED,
        )
        self.discovery_store.put_job_state(state)
        return state

    def ignore(self, owner_id: str, source_id: str, job_id: str) -> DiscoveryJobState:
        job = self._job(owner_id, source_id, job_id)
        state = DiscoveryJobState(
            owner_id=owner_id,
            source_id=source_id,
            job_id=job.id,
            disposition=DiscoveryJobDisposition.IGNORED,
        )
        self.discovery_store.put_job_state(state)
        return state

    def create_application_workspace(
        self, owner_id: str, source_id: str, job_id: str
    ) -> ApplicationWorkspaceResult:
        job = self._job(owner_id, source_id, job_id)
        existing = self.application_store.find_by_source_job(owner_id, job.id)
        fit = self._latest_fit(owner_id, job)
        if existing is not None:
            self._mark_application_created(job, existing.id)
            return ApplicationWorkspaceResult(existing, job, fit, created=False)

        try:
            application = self.application_store.create(
                owner_id,
                company=job.company,
                role=job.title,
                job_url=job.canonical_url,
                job_description=job.description,
                alignment_score=fit.fit_score if fit is not None else None,
                status="considering",
                workflow_step="setup",
                next_action="Complete Career and Job Setup",
                notes="Created from a publicly discovered job posting.",
                source_job_id=job.id,
            )
        except Exception:
            # SQLite's unique index and DynamoDB's conditional source-job link
            # provide race-safe duplicate prevention. If another request won,
            # return the workspace it created instead of surfacing an error.
            existing = self.application_store.find_by_source_job(owner_id, job.id)
            if existing is None:
                raise
            self._mark_application_created(job, existing.id)
            return ApplicationWorkspaceResult(existing, job, fit, created=False)
        self._mark_application_created(job, application.id)
        return ApplicationWorkspaceResult(application, job, fit, created=True)

    def _job(self, owner_id: str, source_id: str, job_id: str) -> DiscoveredJob:
        job = self.discovery_store.get_discovered_job(owner_id, source_id, job_id)
        if job is None:
            raise LookupError("The discovered job could not be found.")
        return job

    def _latest_fit(
        self, owner_id: str, job: DiscoveredJob
    ) -> JobFitSnapshot | None:
        matches = [
            item
            for item in self.discovery_store.list_fit_snapshots(owner_id, job_id=job.id)
            if not item.description_fingerprint
            or item.description_fingerprint == job.description_fingerprint
        ]
        return max(matches, key=lambda item: item.analyzed_at, default=None)

    def _mark_application_created(
        self, job: DiscoveredJob, application_id: str
    ) -> None:
        self.discovery_store.put_job_state(
            DiscoveryJobState(
                owner_id=job.owner_id,
                source_id=job.source_id,
                job_id=job.id,
                disposition=DiscoveryJobDisposition.APPLICATION_CREATED,
                application_id=application_id,
                updated_at=utc_now_iso(),
            )
        )
