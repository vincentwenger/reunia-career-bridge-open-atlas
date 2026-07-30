from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from .deduplication import deduplicate_jobs
from .models import CompanySource, DiscoveredJob, JobSourceType
from .ranking import CandidateJobProfile, RankedJob, rank_jobs
from .sources.ashby import AshbyJobSource
from .sources.base import JobSource
from .sources.generic_jsonld import GenericJsonLdJobSource
from .sources.greenhouse import GreenhouseJobSource
from .sources.lever import LeverJobSource
from .storage import DiscoveryStore, InMemoryDiscoveryStore


PUBLIC_COVERAGE_DESCRIPTION = (
    "Finds publicly accessible job postings exposed by configured sources. "
    "Internal, unlisted, removed, authentication-protected, or otherwise "
    "inaccessible positions cannot be guaranteed."
)


@dataclass(frozen=True, slots=True)
class SourceDiscoveryError:
    source_id: str
    source_type: JobSourceType
    message: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    jobs: tuple[DiscoveredJob, ...]
    ranked_jobs: tuple[RankedJob, ...] = ()
    errors: tuple[SourceDiscoveryError, ...] = ()


class JobDiscoveryService:
    """Collect and rank discovery records without creating JobApplications."""

    def __init__(
        self,
        *,
        adapters: Mapping[JobSourceType, JobSource] | None = None,
        store: DiscoveryStore | None = None,
    ) -> None:
        self.adapters: dict[JobSourceType, JobSource] = dict(adapters or default_adapters())
        self.store = store or InMemoryDiscoveryStore()

    def discover(
        self,
        sources: list[CompanySource],
        *,
        candidate_profile: CandidateJobProfile | None = None,
    ) -> DiscoveryResult:
        collected: list[DiscoveredJob] = []
        errors: list[SourceDiscoveryError] = []
        for source in sources:
            existing_source = self.store.get_company_source(source.owner_id, source.id)
            configured_source = source
            if existing_source is not None and not source.last_checked_at:
                configured_source = replace(
                    source,
                    last_checked_at=existing_source.last_checked_at,
                )
            self.store.put_company_source(configured_source)
            if not configured_source.enabled:
                continue
            adapter = self.adapters.get(configured_source.source_type)
            if adapter is None:
                errors.append(
                    SourceDiscoveryError(configured_source.id, configured_source.source_type, "No adapter is registered")
                )
                continue
            try:
                source_jobs = deduplicate_jobs(adapter.fetch_jobs(configured_source))
                synchronized = self.store.sync_discovered_jobs(configured_source, source_jobs)
            except Exception as exc:
                errors.append(SourceDiscoveryError(configured_source.id, configured_source.source_type, str(exc)))
                continue
            collected.extend(synchronized)

        jobs = deduplicate_jobs(collected)
        ranked = rank_jobs(jobs, candidate_profile) if candidate_profile is not None else []
        for item in ranked:
            self.store.put_fit_snapshot(item.fit_snapshot)
        return DiscoveryResult(tuple(jobs), tuple(ranked), tuple(errors))

    def discover_configured(
        self,
        owner_id: str,
        *,
        candidate_profile: CandidateJobProfile | None = None,
    ) -> DiscoveryResult:
        sources = self.store.list_company_sources(owner_id, enabled_only=True)
        return self.discover(sources, candidate_profile=candidate_profile)


def default_adapters() -> dict[JobSourceType, JobSource]:
    return {
        JobSourceType.GREENHOUSE: GreenhouseJobSource(),
        JobSourceType.LEVER: LeverJobSource(),
        JobSourceType.ASHBY: AshbyJobSource(),
        JobSourceType.GENERIC_JSONLD: GenericJsonLdJobSource(),
    }
