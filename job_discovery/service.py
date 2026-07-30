from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .deduplication import deduplicate_jobs
from .models import CompanySource, DiscoveredJob, JobSourceType
from .ranking import CandidateJobProfile, RankedJob, rank_jobs
from .sources.ashby import AshbyJobSource
from .sources.base import JobSource
from .sources.generic_jsonld import GenericJsonLdJobSource
from .sources.greenhouse import GreenhouseJobSource
from .sources.lever import LeverJobSource
from .storage import InMemoryJobStore, JobStore


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
    def __init__(
        self,
        *,
        adapters: Mapping[JobSourceType, JobSource] | None = None,
        store: JobStore | None = None,
    ) -> None:
        self.adapters: dict[JobSourceType, JobSource] = dict(adapters or default_adapters())
        self.store = store or InMemoryJobStore()

    def discover(
        self,
        sources: list[CompanySource],
        *,
        candidate_profile: CandidateJobProfile | None = None,
    ) -> DiscoveryResult:
        collected: list[DiscoveredJob] = []
        errors: list[SourceDiscoveryError] = []
        for source in sources:
            if not source.enabled:
                continue
            adapter = self.adapters.get(source.source_type)
            if adapter is None:
                errors.append(
                    SourceDiscoveryError(source.source_id, source.source_type, "No adapter is registered")
                )
                continue
            try:
                source_jobs = adapter.fetch_jobs(source)
            except Exception as exc:
                errors.append(SourceDiscoveryError(source.source_id, source.source_type, str(exc)))
                continue
            source_jobs = deduplicate_jobs(source_jobs)
            self.store.replace_for_source(source.source_id, source_jobs)
            collected.extend(source_jobs)
        jobs = deduplicate_jobs(collected)
        ranked = rank_jobs(jobs, candidate_profile) if candidate_profile is not None else []
        return DiscoveryResult(tuple(jobs), tuple(ranked), tuple(errors))


def default_adapters() -> dict[JobSourceType, JobSource]:
    return {
        JobSourceType.GREENHOUSE: GreenhouseJobSource(),
        JobSourceType.LEVER: LeverJobSource(),
        JobSourceType.ASHBY: AshbyJobSource(),
        JobSourceType.GENERIC_JSONLD: GenericJsonLdJobSource(),
    }
