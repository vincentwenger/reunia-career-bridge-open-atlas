from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Discovery refresh and durable-assessment orchestration services."""

_routes = DeferredRouteRegistry()

def _run_discovery_source_refresh(
    owner_id: str, sources: list[CompanySource]
):
    discovery_service = (
        JobDiscoveryService(store=discovery_store)
        .enable_shared_public_catalog()
    )
    result = discovery_service.discover(
        sources,
        candidate_profile=None,
        analyze_new_jobs=False,
        source_fetch_transform=_interactive_discovery_source,
    )
    discovery_service.hydrate_owner_from_shared_catalog(owner_id, sources)
    for error in result.errors:
        current_app.logger.warning(
            "Job discovery source refresh failed catalog_owner=%s actor=%s source=%s type=%s error=%s",
            SHARED_CATALOG_SOURCE_OWNER_ID,
            owner_id,
            error.source_id,
            error.source_type.value,
            error.message,
        )
    for error in result.analysis_errors:
        current_app.logger.warning(
            "Job discovery analysis failed catalog_owner=%s actor=%s source=%s job=%s error=%s",
            SHARED_CATALOG_SOURCE_OWNER_ID,
            owner_id,
            error.source_id,
            error.job_id,
            error.message,
        )
    return result


def _discovery_source_refresh_payload(
    source: CompanySource, result
) -> dict[str, Any]:
    issues = [error.message for error in result.errors]
    issues.extend(error.message for error in result.analysis_errors)
    if result.shared_catalog_hits:
        outcome = "reused"
        message = f"Reused recently collected {source.company_name} jobs."
    elif result.shared_catalog_refreshes:
        outcome = "refreshed"
        message = f"Refreshed {source.company_name} for the shared catalog."
    elif result.shared_refreshes_in_progress:
        outcome = "in_progress"
        message = (
            f"A {source.company_name} refresh was already running; "
            "cached public jobs were used."
        )
    elif issues:
        normalized_issues = [issue.casefold() for issue in issues]
        robots_issue = any(
            "robots.txt disallows" in issue for issue in normalized_issues
        )
        transient_index_issue = any(
            "indexed fallback was unavailable" in issue
            and any(
                token in issue
                for token in (
                    "timeout",
                    "timed out",
                    "502",
                    "503",
                    "504",
                    "temporarily unavailable",
                    "connection",
                )
            )
            for issue in normalized_issues
        )
        if robots_issue and transient_index_issue:
            outcome = "error"
            message = (
                f"{source.company_name}'s direct listing is blocked, and the "
                "compliant fallback temporarily failed. Retry the scan."
            )
        elif robots_issue:
            outcome = "permission_required"
            message = (
                f"{source.company_name} requires an authorized feed or crawler "
                "permission before automated discovery can run."
            )
        else:
            outcome = "error"
            message = f"{source.company_name} could not be refreshed."
    else:
        outcome = "completed"
        message = f"Checked {source.company_name}."
    return {
        "ok": not issues,
        "source_id": source.id,
        "company_name": source.company_name,
        "outcome": outcome,
        "message": message,
        "jobs_available": len(result.jobs),
        "posting_age_filtered": len(result.age_filtered_jobs),
        "issues": issues,
    }


def _restore_discovery_fit_snapshots_from_cached_analysis(
    owner_id: str,
    profile: CandidateJobProfile,
    jobs: list[DiscoveredJob],
) -> set[tuple[str, str]]:
    """Rebuild current-profile fits without making new AI requests.

    Job descriptions are analyzed once and that structured analysis is
    stored durably. If the Career Evidence fingerprint changes because the
    foundation profile was reloaded, reordered, or legitimately updated,
    recompute only the deterministic fit snapshot from the cached analysis.
    This prevents already-assessed jobs from returning to the pending queue
    after a container replacement.
    """

    if not jobs:
        return set()
    try:
        result = JobDiscoveryService(store=discovery_store).assess_existing_jobs(
            jobs,
            profile,
            analyze_new_jobs=False,
        )
    except Exception:
        current_app.logger.exception(
            "Could not restore cached Job Discovery fits owner=%s jobs=%s",
            owner_id,
            len(jobs),
        )
        return set()

    restored = {
        (item.job.source_id, item.job.id)
        for item in result.ranked_jobs
        if item.fit_snapshot.profile_fingerprint == profile.fingerprint
    }
    if restored:
        current_app.logger.info(
            "Restored %s Job Discovery fit snapshots from durable cached analyses owner=%s",
            len(restored),
            owner_id,
        )
    for error in result.analysis_errors:
        current_app.logger.warning(
            "Cached Job Discovery fit restoration failed owner=%s source=%s job=%s error=%s",
            owner_id,
            error.source_id,
            error.job_id,
            error.message,
        )
    return restored


def _pending_discovery_assessment_jobs(
    owner_id: str,
    profile: CandidateJobProfile,
    *,
    skip_job_keys: set[str] | None = None,
) -> list[DiscoveredJob]:
    """Return visible owner jobs that still need a profile-specific fit snapshot."""

    skipped = skip_job_keys or set()
    enabled_source_ids = {
        source.id
        for source in discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID,
            enabled_only=True,
        )
    }
    preferences = discovery_store.get_search_preferences(owner_id)
    maximum_posting_age_days = (
        preferences.maximum_posting_age_days
        if preferences is not None
        else DEFAULT_MAX_POSTING_AGE_DAYS
    )
    fit_snapshots = discovery_store.list_fit_snapshots(owner_id)
    fits = {
        (
            item.job_id,
            item.profile_fingerprint,
            item.description_fingerprint,
        )
        for item in fit_snapshots
    }
    legacy_fits = {
        (item.job_id, item.profile_fingerprint)
        for item in fit_snapshots
        if not item.description_fingerprint
    }
    states = {
        (item.source_id, item.job_id): item
        for item in discovery_store.list_job_states(owner_id)
    }
    candidates: list[DiscoveredJob] = []
    for job in discovery_store.list_discovered_jobs(owner_id, active_only=True):
        job_key = f"{job.source_id}:{job.id}"
        if job_key in skipped:
            continue
        if enabled_source_ids and job.source_id not in enabled_source_ids:
            continue
        if (
            (
                job.id,
                profile.fingerprint,
                job.description_fingerprint,
            )
            in fits
            or (job.id, profile.fingerprint) in legacy_fits
        ):
            continue
        state_record = states.get((job.source_id, job.id))
        if state_record is not None and state_record.disposition in {
            DiscoveryJobDisposition.SAVED,
            DiscoveryJobDisposition.IGNORED,
            DiscoveryJobDisposition.APPLICATION_CREATED,
        }:
            continue
        if not evaluate_posting_age(
            job,
            maximum_age_days=maximum_posting_age_days,
        ).eligible:
            continue
        if not evaluate_stage_one(job, profile).passed:
            continue
        candidates.append(job)

    restored = _restore_discovery_fit_snapshots_from_cached_analysis(
        owner_id,
        profile,
        candidates,
    )
    pending = [
        job
        for job in candidates
        if (job.source_id, job.id) not in restored
    ]
    pending.sort(
        key=lambda item: (
            item.posted_at or item.first_seen_at,
            item.company.casefold(),
            item.title.casefold(),
            item.id,
        ),
        reverse=True,
    )
    return pending


def _assessment_request_payload() -> tuple[dict[str, Any], bool]:
    wants_json = request.is_json or "application/json" in str(
        request.headers.get("Accept") or ""
    )
    source = request.get_json(silent=True) if request.is_json else request.form
    return dict(source or {}), wants_json


def _async_job_response(job: AsyncJob) -> dict[str, Any]:
    payload = job.to_public_dict()
    payload.update(
        {
            "ok": True,
            "successful": job.status is not AsyncJobStatus.FAILED,
            "status_url": url_for(
                "application_builder.discovery_assessment_job_status",
                job_id=job.id,
            ),
            "cancel_url": url_for(
                "application_builder.cancel_discovery_assessment_job",
                job_id=job.id,
            ),
            "retry_url": url_for(
                "application_builder.retry_discovery_assessment_job",
                job_id=job.id,
            ),
        }
    )
    return payload


_EXPORT_NAMES = (
    '_run_discovery_source_refresh',
    '_discovery_source_refresh_payload',
    '_restore_discovery_fit_snapshots_from_cached_analysis',
    '_pending_discovery_assessment_jobs',
    '_assessment_request_payload',
    '_async_job_response',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
