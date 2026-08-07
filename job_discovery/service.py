from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, replace
from typing import Callable, Mapping, Protocol

from products.resume_taylor.resume_tailor.models import JobAnalysis

from .deduplication import deduplicate_jobs
from .models import (
    CompanySource,
    DEFAULT_MAX_POSTING_AGE_DAYS,
    DiscoveredJob,
    DiscoveryJobDisposition,
    JobAnalysisRecord,
    JobSourceType,
    utc_now_iso,
)
from .posting_age import partition_jobs_by_posting_age
from .public_catalog import (
    catalog_lock_expiry,
    is_catalog_fresh,
    materialize_catalog_job,
    public_catalog_enabled,
    public_source_key,
)
from .ranking import (
    CandidateJobProfile,
    RankedJob,
    StageOneEvaluation,
    assess_analyzed_job,
    evaluate_stage_one,
    ranked_from_snapshot,
)
from .sources.ashby import AshbyJobSource
from .sources.base import CompanyRateLimiter, JobSource
from .sources.generic_jsonld import GenericJsonLdJobSource
from .sources.greenhouse import GreenhouseJobSource
from .sources.lever import LeverJobSource
from .sources.workday import WorkdayJobSource
from .sources.successfactors import SuccessFactorsJobSource
from .sources.oracle_cloud_hcm import OracleCloudHcmJobSource
from .sources.icims import IcmsJobSource
from .sources.smartrecruiters import SmartRecruitersJobSource
from .sources.avature import AvatureJobSource
from .sources.eightfold import EightfoldJobSource
from .sources.taleo import TaleoJobSource
from .sources.dayforce import DayforceJobSource
from .sources.talemetry_ttc import TalemetryTtcJobSource
from .sources.jobvite import JobviteJobSource
from .sources.ukg_pro import UkgProJobSource
from .sources.peopleadmin import PeopleAdminJobSource
from .sources.radancy_talentbrew import RadancyTalentBrewJobSource
from .sources.amazon_jobs import AmazonJobsJobSource
from .sources.branded_requisition import BrandedRequisitionJobSource
from .storage import DiscoveryStore, InMemoryDiscoveryStore
from .source_migrations import migrate_known_company_source


PUBLIC_COVERAGE_DESCRIPTION = (
    "Finds publicly accessible job postings exposed by configured sources. "
    "Internal, unlisted, removed, authentication-protected, or otherwise "
    "inaccessible positions cannot be guaranteed."
)


class JobAnalyzer(Protocol):
    def analyze_job(self, job_description: str, stated_title: str = "") -> JobAnalysis:
        ...


@dataclass(frozen=True, slots=True)
class SourceDiscoveryError:
    source_id: str
    source_type: JobSourceType
    message: str


@dataclass(frozen=True, slots=True)
class JobAnalysisError:
    job_id: str
    source_id: str
    message: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    jobs: tuple[DiscoveredJob, ...]
    ranked_jobs: tuple[RankedJob, ...] = ()
    filtered_jobs: tuple[StageOneEvaluation, ...] = ()
    stage_one_results: tuple[StageOneEvaluation, ...] = ()
    errors: tuple[SourceDiscoveryError, ...] = ()
    analysis_errors: tuple[JobAnalysisError, ...] = ()
    age_filtered_jobs: tuple[DiscoveredJob, ...] = ()
    shared_catalog_hits: int = 0
    shared_catalog_refreshes: int = 0
    shared_refreshes_in_progress: int = 0


class JobDiscoveryService:
    """Collect jobs, cheaply filter them, then analyze only viable postings."""

    def __init__(
        self,
        *,
        adapters: Mapping[JobSourceType, JobSource] | None = None,
        store: DiscoveryStore | None = None,
        job_analyzer: JobAnalyzer | None = None,
        analyzer_factory: Callable[[str], JobAnalyzer] | None = None,
        ranking_clock: Callable[[], str] | None = None,
        use_shared_public_catalog: bool = False,
    ) -> None:
        self.adapters: dict[JobSourceType, JobSource] = dict(adapters or default_adapters())
        self.store = store or InMemoryDiscoveryStore()
        self._job_analyzer = job_analyzer
        self._analyzer_factory = analyzer_factory or _default_analyzer_factory
        self._owner_analyzers: dict[str, JobAnalyzer] = {}
        self._ranking_clock = ranking_clock or utc_now_iso
        self._use_shared_public_catalog = bool(use_shared_public_catalog)

    def enable_shared_public_catalog(self) -> "JobDiscoveryService":
        self._use_shared_public_catalog = True
        return self

    def discover(
        self,
        sources: list[CompanySource],
        *,
        candidate_profile: CandidateJobProfile | None = None,
        analyze_new_jobs: bool = True,
        persist_source_configuration: bool = True,
        source_fetch_transform: Callable[[CompanySource], CompanySource] | None = None,
    ) -> DiscoveryResult:
        collected: list[DiscoveredJob] = []
        age_filtered_jobs: list[DiscoveredJob] = []
        errors: list[SourceDiscoveryError] = []
        shared_catalog_hits = 0
        shared_catalog_refreshes = 0
        shared_refreshes_in_progress = 0
        evaluated_at = self._ranking_clock()
        for source in sources:
            existing_source = self.store.get_company_source(source.owner_id, source.id)
            configured_source = source
            if existing_source is not None and source.revision == 0:
                configured_source = replace(
                    source,
                    last_checked_at=(
                        source.last_checked_at or existing_source.last_checked_at
                    ),
                    revision=existing_source.revision,
                )
            configured_source = migrate_known_company_source(configured_source)
            if persist_source_configuration:
                configured_source = self.store.put_company_source(configured_source)
            if not configured_source.enabled:
                continue
            adapter = self.adapters.get(configured_source.source_type)
            if adapter is None:
                errors.append(
                    SourceDiscoveryError(
                        configured_source.id,
                        configured_source.source_type,
                        "No adapter is registered",
                    )
                )
                continue
            try:
                fetch_source = (
                    source_fetch_transform(configured_source)
                    if source_fetch_transform is not None
                    else configured_source
                )
                if self._use_shared_public_catalog and public_catalog_enabled(configured_source):
                    catalog_key = public_source_key(configured_source)
                    status = self.store.get_public_catalog_status(catalog_key)
                    catalog_jobs = self.store.list_public_catalog_jobs(catalog_key)
                    complete_scan = fetch_source == configured_source
                    if is_catalog_fresh(
                        status,
                        configured_source,
                        now=evaluated_at,
                        require_complete=complete_scan,
                    ):
                        shared_catalog_hits += 1
                    else:
                        refresh_token = uuid.uuid4().hex
                        acquired = self.store.try_acquire_public_refresh_lock(
                            catalog_key,
                            refresh_token,
                            acquired_at=evaluated_at,
                            expires_at=catalog_lock_expiry(configured_source, evaluated_at),
                        )
                        if acquired:
                            try:
                                fetched_jobs = deduplicate_jobs(adapter.fetch_jobs(fetch_source))
                                effective_complete_scan = complete_scan and _adapter_scan_is_complete(
                                    adapter, fetch_source, fetched_jobs
                                )
                                self.store.sync_public_catalog(
                                    configured_source,
                                    catalog_key,
                                    fetched_jobs,
                                    checked_at=evaluated_at,
                                    complete_scan=effective_complete_scan,
                                )
                                catalog_jobs = self.store.list_public_catalog_jobs(catalog_key)
                                shared_catalog_refreshes += 1
                            except Exception as exc:
                                self.store.mark_public_catalog_failure(
                                    configured_source,
                                    catalog_key,
                                    attempted_at=evaluated_at,
                                    message=str(exc),
                                )
                                if not catalog_jobs:
                                    raise
                                errors.append(
                                    SourceDiscoveryError(
                                        configured_source.id,
                                        configured_source.source_type,
                                        f"Using previously collected public jobs because refresh failed: {exc}",
                                    )
                                )
                            finally:
                                self.store.release_public_refresh_lock(
                                    catalog_key, refresh_token
                                )
                        elif catalog_jobs:
                            shared_refreshes_in_progress += 1
                        else:
                            raise RuntimeError(
                                "This public company source is already being refreshed. Try again after the shared scan completes."
                            )
                    source_jobs = [
                        materialize_catalog_job(job, configured_source)
                        for job in catalog_jobs
                    ]
                else:
                    source_jobs = deduplicate_jobs(adapter.fetch_jobs(fetch_source))
                    if not _adapter_scan_is_complete(adapter, fetch_source, source_jobs):
                        # Indexed/feed fallbacks are intentionally non-exhaustive. Preserve
                        # previously collected active jobs instead of counting unseen records
                        # as misses and eventually deactivating them.
                        source_jobs = deduplicate_jobs(
                            self.store.list_discovered_jobs(
                                configured_source.owner_id,
                                source_id=configured_source.id,
                                active_only=True,
                            )
                            + source_jobs
                        )

                preferences = self.store.get_search_preferences(configured_source.owner_id)
                maximum_age_days = (
                    preferences.maximum_posting_age_days
                    if preferences is not None
                    else DEFAULT_MAX_POSTING_AGE_DAYS
                )
                source_jobs, source_age_filtered = partition_jobs_by_posting_age(
                    source_jobs,
                    maximum_age_days=maximum_age_days,
                    evaluated_at=evaluated_at,
                )
                age_filtered_jobs.extend(source_age_filtered)
                synchronized = self.store.sync_discovered_jobs(configured_source, source_jobs)
            except Exception as exc:
                errors.append(
                    SourceDiscoveryError(
                        configured_source.id,
                        configured_source.source_type,
                        str(exc),
                    )
                )
                continue
            collected.extend(synchronized)

        jobs = deduplicate_jobs(collected)
        if candidate_profile is None:
            return DiscoveryResult(
                tuple(jobs),
                errors=tuple(errors),
                age_filtered_jobs=tuple(deduplicate_jobs(age_filtered_jobs)),
                shared_catalog_hits=shared_catalog_hits,
                shared_catalog_refreshes=shared_catalog_refreshes,
                shared_refreshes_in_progress=shared_refreshes_in_progress,
            )

        stage_one_results = tuple(
            self._apply_user_disposition(
                evaluate_stage_one(job, candidate_profile, evaluated_at=evaluated_at)
            )
            for job in jobs
        )
        filtered_jobs = tuple(item for item in stage_one_results if not item.passed)
        ranked, analysis_errors = self._run_stage_two(
            stage_one_results,
            candidate_profile,
            analyze_new_jobs=analyze_new_jobs,
        )
        return DiscoveryResult(
            jobs=tuple(jobs),
            ranked_jobs=tuple(ranked),
            filtered_jobs=filtered_jobs,
            stage_one_results=stage_one_results,
            errors=tuple(errors),
            analysis_errors=tuple(analysis_errors),
            age_filtered_jobs=tuple(deduplicate_jobs(age_filtered_jobs)),
            shared_catalog_hits=shared_catalog_hits,
            shared_catalog_refreshes=shared_catalog_refreshes,
            shared_refreshes_in_progress=shared_refreshes_in_progress,
        )

    def hydrate_owner_from_shared_catalog(
        self, owner_id: str, sources: list[CompanySource], *, force: bool = False
    ) -> int:
        """Materialize every centrally managed catalog source for one user.

        Company sources remain controlled by the shared catalog owner. Each user
        receives owner-scoped job records so private fit snapshots, saved/ignored
        dispositions, and Application Workspace links continue to work unchanged.
        No external HTTP request is made by this method.
        """

        if not self._use_shared_public_catalog:
            return 0
        normalized_owner = str(owner_id or "").strip()
        if not normalized_owner:
            raise ValueError("owner_id is required")
        if not sources:
            return 0

        # Deferred hydration is called with the complete centrally managed
        # source list after the Job Discovery HTML has rendered. Fetch the
        # owner's materialized sources and all shared catalog statuses once
        # instead of issuing two point reads for every source. On DynamoDB this
        # changes the sync pattern from O(number of sources) network calls to two
        # prefix queries plus the job queries required only for catalogs that
        # actually need synchronization.
        existing_sources_by_id = {
            source.id: source
            for source in self.store.list_company_sources(normalized_owner)
        }
        catalog_statuses_by_key = {
            status.source_key: status
            for status in self.store.list_public_catalog_statuses()
        }
        preferences = self.store.get_search_preferences(normalized_owner)
        maximum_age_days = (
            preferences.maximum_posting_age_days
            if preferences is not None
            else DEFAULT_MAX_POSTING_AGE_DAYS
        )

        hydrated = 0
        for catalog_source in sources:
            existing = existing_sources_by_id.get(catalog_source.id)
            config_changed = existing is None or any(
                (
                    existing.company_name != catalog_source.company_name,
                    existing.careers_url != catalog_source.careers_url,
                    existing.source_type != catalog_source.source_type,
                    existing.source_identifier != catalog_source.source_identifier,
                    existing.enabled != catalog_source.enabled,
                    existing.filters != catalog_source.filters,
                )
            )
            owner_source = replace(
                catalog_source,
                owner_id=normalized_owner,
                last_checked_at=existing.last_checked_at if existing else "",
                revision=existing.revision if existing else 0,
            )
            if config_changed:
                owner_source = self.store.put_company_source(owner_source)
                existing_sources_by_id[catalog_source.id] = owner_source
            else:
                owner_source = existing

            if not catalog_source.enabled:
                continue
            catalog_key = public_source_key(catalog_source)
            status = catalog_statuses_by_key.get(catalog_key)
            if status is None or not status.last_success_at:
                continue
            if (
                not force
                and not config_changed
                and owner_source.last_checked_at
                and owner_source.last_checked_at >= status.last_success_at
            ):
                continue
            public_jobs = self.store.list_public_catalog_jobs(catalog_key)
            owner_jobs = [
                materialize_catalog_job(job, owner_source) for job in public_jobs
            ]
            eligible_jobs, _ = partition_jobs_by_posting_age(
                owner_jobs,
                maximum_age_days=maximum_age_days,
                evaluated_at=status.last_success_at,
            )
            self.store.sync_discovered_jobs(
                owner_source, eligible_jobs, checked_at=status.last_success_at
            )
            hydrated += len(eligible_jobs)
        return hydrated

    def hydrate_from_shared_catalog(
        self, sources: list[CompanySource]
    ) -> int:
        """Copy newer shared public postings into owner-scoped discovery records.

        This performs no external HTTP requests. It is intended for an explicit
        post-render sync or an external refresh workflow rather than the initial
        Job Discovery page request.
        """

        if not self._use_shared_public_catalog:
            return 0
        if not sources:
            return 0
        catalog_statuses_by_key = {
            status.source_key: status
            for status in self.store.list_public_catalog_statuses()
        }
        maximum_age_days_by_owner: dict[str, int] = {}
        hydrated = 0
        for source in sources:
            if not source.enabled or not public_catalog_enabled(source):
                continue
            catalog_key = public_source_key(source)
            status = catalog_statuses_by_key.get(catalog_key)
            if status is None or not status.last_success_at:
                continue
            if source.last_checked_at and source.last_checked_at >= status.last_success_at:
                continue
            public_jobs = self.store.list_public_catalog_jobs(catalog_key)
            if not public_jobs:
                continue
            owner_jobs = [materialize_catalog_job(job, source) for job in public_jobs]
            maximum_age_days = maximum_age_days_by_owner.get(source.owner_id)
            if maximum_age_days is None:
                preferences = self.store.get_search_preferences(source.owner_id)
                maximum_age_days = (
                    preferences.maximum_posting_age_days
                    if preferences is not None
                    else DEFAULT_MAX_POSTING_AGE_DAYS
                )
                maximum_age_days_by_owner[source.owner_id] = maximum_age_days
            eligible_jobs, _ = partition_jobs_by_posting_age(
                owner_jobs,
                maximum_age_days=maximum_age_days,
                evaluated_at=status.last_success_at,
            )
            self.store.sync_discovered_jobs(
                source, eligible_jobs, checked_at=status.last_success_at
            )
            hydrated += len(eligible_jobs)
        return hydrated

    def assess_existing_jobs(
        self,
        jobs: list[DiscoveredJob],
        candidate_profile: CandidateJobProfile,
        *,
        analyze_new_jobs: bool = True,
    ) -> DiscoveryResult:
        """Assess already-materialized owner jobs without scanning any source."""

        evaluated_at = self._ranking_clock()
        deduplicated = deduplicate_jobs(jobs)
        stage_one_results = tuple(
            self._apply_user_disposition(
                evaluate_stage_one(job, candidate_profile, evaluated_at=evaluated_at)
            )
            for job in deduplicated
        )
        filtered_jobs = tuple(item for item in stage_one_results if not item.passed)
        ranked, analysis_errors = self._run_stage_two(
            stage_one_results,
            candidate_profile,
            analyze_new_jobs=analyze_new_jobs,
        )
        return DiscoveryResult(
            jobs=tuple(deduplicated),
            ranked_jobs=tuple(ranked),
            filtered_jobs=filtered_jobs,
            stage_one_results=stage_one_results,
            analysis_errors=tuple(analysis_errors),
        )

    def discover_configured(
        self,
        owner_id: str,
        *,
        candidate_profile: CandidateJobProfile | None = None,
        analyze_new_jobs: bool = True,
    ) -> DiscoveryResult:
        sources = self.store.list_company_sources(owner_id, enabled_only=True)
        return self.discover(
            sources,
            candidate_profile=candidate_profile,
            analyze_new_jobs=analyze_new_jobs,
        )

    def _apply_user_disposition(
        self, evaluation: StageOneEvaluation
    ) -> StageOneEvaluation:
        state = self.store.get_job_state(
            evaluation.job.owner_id,
            evaluation.job.source_id,
            evaluation.job.id,
        )
        if state is None or state.disposition is not DiscoveryJobDisposition.IGNORED:
            return evaluation
        reason = "Ignored by user"
        return replace(
            evaluation,
            passed=False,
            reasons=tuple(dict.fromkeys((*evaluation.reasons, reason))),
            rejection_reasons=tuple(
                dict.fromkeys((*evaluation.rejection_reasons, reason))
            ),
        )

    def _run_stage_two(
        self,
        stage_one_results: tuple[StageOneEvaluation, ...],
        profile: CandidateJobProfile,
        *,
        analyze_new_jobs: bool = True,
    ) -> tuple[list[RankedJob], list[JobAnalysisError]]:
        ranked: list[RankedJob] = []
        errors: list[JobAnalysisError] = []
        for stage_one in stage_one_results:
            if not stage_one.passed:
                continue
            job = stage_one.job
            cached_fit = self.store.get_fit_snapshot(
                job.owner_id,
                job.id,
                profile.fingerprint,
                job.description_fingerprint,
            )
            if cached_fit is not None:
                ranked.append(ranked_from_snapshot(job, cached_fit, stage_one=stage_one))
                continue
            try:
                analysis_record = self.store.get_job_analysis(
                    job.owner_id,
                    job.id,
                    job.description_fingerprint,
                )
                if analysis_record is None:
                    if not analyze_new_jobs:
                        # A previous fit can become stale when verified evidence
                        # changes. In cache-only mode we may rebuild the fit from
                        # a durable job analysis, but must never start a new AI
                        # analysis request.
                        continue
                    analyzer = self._analyzer_for_owner(job.owner_id)
                    analysis = analyzer.analyze_job(job.description, job.title)
                    analysis_record = _analysis_record(job, analysis)
                    self.store.put_job_analysis(analysis_record)
                else:
                    analysis = _job_analysis(analysis_record)

                item = assess_analyzed_job(
                    job,
                    profile,
                    analysis,
                    stage_one=stage_one,
                )
                self.store.put_fit_snapshot(item.fit_snapshot)
                ranked.append(item)
            except Exception as exc:
                errors.append(JobAnalysisError(job.id, job.source_id, str(exc)))

        ranked.sort(
            key=lambda item: (
                item.search_priority,
                item.fit_score,
                item.job.posted_at,
                item.job.title.casefold(),
            ),
            reverse=True,
        )
        return ranked, errors

    def _analyzer_for_owner(self, owner_id: str) -> JobAnalyzer:
        if self._job_analyzer is not None:
            return self._job_analyzer
        analyzer = self._owner_analyzers.get(owner_id)
        if analyzer is None:
            analyzer = self._analyzer_factory(owner_id)
            self._owner_analyzers[owner_id] = analyzer
        return analyzer


def _analysis_record(job: DiscoveredJob, analysis: JobAnalysis) -> JobAnalysisRecord:
    requirements = tuple(_model_payload(item) for item in analysis.requirements)
    return JobAnalysisRecord(
        job_id=job.id,
        owner_id=job.owner_id,
        description_fingerprint=job.description_fingerprint,
        target_title=analysis.target_title or job.title,
        target_company=analysis.target_company or job.company,
        requirements=requirements,
        ignored_boilerplate=tuple(analysis.ignored_boilerplate),
    )



def _adapter_scan_is_complete(
    adapter: JobSource,
    source: CompanySource,
    jobs: list[DiscoveredJob],
) -> bool:
    checker = getattr(adapter, "scan_is_complete", None)
    if not callable(checker):
        return True
    try:
        return bool(checker(source, jobs))
    except Exception:
        # Completeness metadata must never make an otherwise successful source
        # scan fail. Unknown adapters retain the historical complete-scan behavior.
        return True

def _job_analysis(record: JobAnalysisRecord) -> JobAnalysis:
    return JobAnalysis(
        target_title=record.target_title,
        target_company=record.target_company,
        requirements=[dict(item) for item in record.requirements],
        ignored_boilerplate=list(record.ignored_boilerplate),
    )


def _model_payload(value: object) -> dict[str, object]:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="json"))
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        return dict(legacy_dict())
    raise TypeError(f"Unsupported analysis model: {type(value)!r}")


def _job_discovery_ai_timeout_seconds() -> float:
    """Keep one interactive assessment request below the web gateway timeout."""

    raw = os.getenv("JOB_DISCOVERY_AI_TIMEOUT_SECONDS", "20")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 20.0
    # Lightsail/proxy 504s commonly occur before the generic 90-second AI timeout.
    # Leave time for DynamoDB reads/writes and Flask to serialize the response.
    return min(25.0, max(5.0, value))


def _job_discovery_ai_reasoning_effort() -> str:
    """Use the smallest reasoning budget for extraction/classification work."""

    value = str(
        os.getenv("JOB_DISCOVERY_AI_REASONING_EFFORT", "minimal") or "minimal"
    ).strip().casefold()
    supported = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
    return value if value in supported else "minimal"


def _job_discovery_ai_max_output_tokens() -> int:
    """Reserve enough room for reasoning plus the structured requirement list."""

    raw = os.getenv("JOB_DISCOVERY_AI_MAX_OUTPUT_TOKENS", "4800")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 4800
    return min(12000, max(1600, value))


def _default_analyzer_factory(owner_id: str) -> JobAnalyzer:
    from products.resume_taylor.resume_tailor.ai import ResumeAI

    # Job assessment is a high-volume extraction and ranking workload. Keep its
    # model independent from the broader application preset so changing
    # AI_MODEL_FAST cannot accidentally make a large discovery backlog costly.
    # Interactive discovery deliberately uses one provider attempt with a short
    # timeout: a slow posting is isolated and can be retried instead of causing
    # the entire browser batch to fail with HTTP 504.
    model = (os.getenv("JOB_DISCOVERY_AI_MODEL") or "gpt-5-nano").strip()
    return ResumeAI(
        model,
        reasoning_effort=_job_discovery_ai_reasoning_effort(),
        user_id=owner_id,
        max_attempts=1,
        request_timeout_seconds=_job_discovery_ai_timeout_seconds(),
        max_output_tokens_by_operation={
            "analyze_job": _job_discovery_ai_max_output_tokens(),
        },
    )


def default_adapters() -> dict[JobSourceType, JobSource]:
    # One limiter is shared across adapters so multiple configured sources for
    # the same owner/company cannot bypass process-local request spacing.
    limiter = CompanyRateLimiter()
    return {
        JobSourceType.GREENHOUSE: GreenhouseJobSource(rate_limiter=limiter),
        JobSourceType.LEVER: LeverJobSource(rate_limiter=limiter),
        JobSourceType.ASHBY: AshbyJobSource(rate_limiter=limiter),
        JobSourceType.WORKDAY: WorkdayJobSource(rate_limiter=limiter),
        JobSourceType.SUCCESSFACTORS: SuccessFactorsJobSource(rate_limiter=limiter),
        JobSourceType.ORACLE_CLOUD_HCM: OracleCloudHcmJobSource(rate_limiter=limiter),
        JobSourceType.ICIMS: IcmsJobSource(rate_limiter=limiter),
        JobSourceType.SMARTRECRUITERS: SmartRecruitersJobSource(rate_limiter=limiter),
        JobSourceType.AVATURE: AvatureJobSource(rate_limiter=limiter),
        JobSourceType.EIGHTFOLD: EightfoldJobSource(rate_limiter=limiter),
        JobSourceType.TALEO: TaleoJobSource(rate_limiter=limiter),
        JobSourceType.DAYFORCE: DayforceJobSource(rate_limiter=limiter),
        JobSourceType.TALEMETRY_TTC: TalemetryTtcJobSource(rate_limiter=limiter),
        JobSourceType.JOBVITE: JobviteJobSource(rate_limiter=limiter),
        JobSourceType.UKG_PRO: UkgProJobSource(rate_limiter=limiter),
        JobSourceType.PEOPLEADMIN: PeopleAdminJobSource(rate_limiter=limiter),
        JobSourceType.RADANCY_TALENTBREW: RadancyTalentBrewJobSource(rate_limiter=limiter),
        JobSourceType.AMAZON_JOBS: AmazonJobsJobSource(rate_limiter=limiter),
        JobSourceType.BRANDED_REQUISITION: BrandedRequisitionJobSource(rate_limiter=limiter),
        JobSourceType.GENERIC_JSONLD: GenericJsonLdJobSource(rate_limiter=limiter),
    }
