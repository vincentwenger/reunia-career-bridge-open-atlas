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
from .posting_details import (
    PostingDescriptionFetchResult,
    PostingDescriptionFetcherProtocol,
)
from .storage import DiscoveryStore


# Safety boundary: discovery may create an internal workspace only. It never
# submits forms, uploads documents, or sends an application to an employer.
AUTOMATIC_APPLICATION_SUBMISSION_SUPPORTED = False
MVP_APPLICATION_SUBMISSION_MODE = "manual_workspace_only"


@dataclass(frozen=True, slots=True)
class ApplicationWorkspaceResult:
    application: ApplicationRecord
    job: DiscoveredJob
    fit_snapshot: JobFitSnapshot | None
    created: bool
    description_refreshed: bool = False
    description_fetch_error: str = ""
    previous_job_description: str = ""


class DiscoveredJobApplicationService:
    """Manage user actions and explicit promotion into Job Applications."""

    def __init__(
        self,
        discovery_store: DiscoveryStore,
        application_store: ApplicationStore,
        description_fetcher: PostingDescriptionFetcherProtocol | None = None,
    ) -> None:
        self.discovery_store = discovery_store
        self.application_store = application_store
        self.description_fetcher = description_fetcher

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
        description_result = self._fetch_description(job)
        selected_description = description_result.description or job.description
        existing = self.application_store.find_by_source_job(owner_id, job.id)
        fit = self._latest_fit(owner_id, job)
        if existing is not None:
            previous_description = ""
            if (
                description_result.refreshed
                and self._same_description(existing.job_description, job.description)
            ):
                previous_description = existing.job_description
                updated = self.application_store.update_builder_progress(
                    owner_id,
                    existing.id,
                    workflow_step=existing.workflow_step or "setup",
                    job_description=selected_description,
                )
                if updated is not None:
                    existing = updated
            self._mark_application_created(job, existing.id)
            return ApplicationWorkspaceResult(
                existing,
                job,
                fit,
                created=False,
                description_refreshed=bool(previous_description),
                description_fetch_error=description_result.error,
                previous_job_description=previous_description,
            )

        try:
            application = self.application_store.create(
                owner_id,
                company=job.company,
                role=job.title,
                job_url=job.canonical_url,
                job_description=selected_description,
                alignment_score=fit.fit_score if fit is not None else None,
                status="considering",
                workflow_step="setup",
                next_action="Complete Career and Job Setup",
                notes="Created from a publicly discovered job posting.",
                source_job_id=job.id,
            )
        except Exception:
            # The application store's conditional source-job link
            # provide race-safe duplicate prevention. If another request won,
            # return the workspace it created instead of surfacing an error.
            existing = self.application_store.find_by_source_job(owner_id, job.id)
            if existing is None:
                raise
            previous_description = ""
            if (
                description_result.refreshed
                and self._same_description(existing.job_description, job.description)
            ):
                previous_description = existing.job_description
                updated = self.application_store.update_builder_progress(
                    owner_id,
                    existing.id,
                    workflow_step=existing.workflow_step or "setup",
                    job_description=selected_description,
                )
                if updated is not None:
                    existing = updated
            self._mark_application_created(job, existing.id)
            return ApplicationWorkspaceResult(
                existing,
                job,
                fit,
                created=False,
                description_refreshed=bool(previous_description),
                description_fetch_error=description_result.error,
                previous_job_description=previous_description,
            )
        self._mark_application_created(job, application.id)
        return ApplicationWorkspaceResult(
            application,
            job,
            fit,
            created=True,
            description_refreshed=description_result.refreshed,
            description_fetch_error=description_result.error,
        )

    def _fetch_description(
        self, job: DiscoveredJob
    ) -> PostingDescriptionFetchResult:
        if self.description_fetcher is None:
            return PostingDescriptionFetchResult(description=job.description)
        try:
            return self.description_fetcher.fetch(job)
        except Exception as exc:
            # Opening the workspace must remain available even when an employer
            # blocks or times out the targeted detail lookup.
            return PostingDescriptionFetchResult(
                description=job.description,
                attempted=True,
                error=str(exc)[:1000],
            )

    @staticmethod
    def _same_description(left: str, right: str) -> bool:
        normalize = lambda value: " ".join(str(value or "").split()).casefold()
        return normalize(left) == normalize(right)

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
