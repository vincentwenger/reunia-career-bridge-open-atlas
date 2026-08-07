from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

from job_discovery.source_normalization import (
    default_source_url,
    normalize_source_configuration,
    normalized_source_identifier,
)
from job_discovery.sources.common import bounded_float, bounded_int

"""Company-source normalization, limits, labels, and scheduling helpers."""

_routes = DeferredRouteRegistry()

def _split_discovery_values(raw: str) -> tuple[str, ...]:
    values: list[str] = []
    for item in re.split(r"[,\n;]+", str(raw or "")):
        value = " ".join(item.split())
        if value and value.casefold() not in {existing.casefold() for existing in values}:
            values.append(value)
    return tuple(values)


def _source_identifier_value(
    source_type: JobSourceType, raw: str, careers_url: str = ""
) -> str:
    return normalized_source_identifier(source_type, raw, careers_url)


def _default_source_url(source_type: JobSourceType, identifier: str) -> str:
    return default_source_url(source_type, identifier)


def _normalized_company_source(
    *,
    source_id: str,
    owner_id: str,
    company_name: str,
    source_type: JobSourceType,
    source_identifier: str,
    careers_url: str,
    enabled: bool,
    existing: CompanySource | None = None,
) -> CompanySource:
    company_name = " ".join(str(company_name or "").split())
    careers_url, source_identifier = normalize_source_configuration(
        source_type, careers_url, source_identifier
    )
    return CompanySource(
        id=source_id,
        owner_id=owner_id,
        company_name=company_name,
        careers_url=careers_url,
        source_type=source_type,
        source_identifier=source_identifier,
        enabled=enabled,
        last_checked_at=existing.last_checked_at if existing else "",
        filters=(
            dict(existing.filters)
            if existing
            else {
                "include_compensation": True,
                "deactivate_after_missed_scans": 3,
            }
        ),
        revision=existing.revision if existing else 0,
    )

def _company_source_identity(source: CompanySource) -> tuple[str, str]:
    if source.source_type in {
        JobSourceType.GREENHOUSE,
        JobSourceType.LEVER,
        JobSourceType.ASHBY,
    }:
        locator = source.source_identifier.casefold().strip().strip("/")
    else:
        parsed = urlsplit(source.careers_url)
        normalized_path = "/".join(
            part for part in parsed.path.casefold().split("/") if part
        )
        normalized_query = parsed.query.casefold()
        locator = (
            f"{(parsed.hostname or '').casefold()}|{normalized_path}|{normalized_query}"
        )
    return source.source_type.value, locator


def _interactive_discovery_source(source: CompanySource) -> CompanySource:
    """Apply browser-safe limits without changing saved source settings."""

    filters = dict(source.filters)

    def capped_int(name: str, default: int, maximum: int) -> int:
        return bounded_int(filters.get(name), default, 0, maximum)

    def capped_float(name: str, default: float, maximum: float) -> float:
        return bounded_float(filters.get(name), default, 0.0, maximum)

    standard_sources = {
        JobSourceType.WORKDAY,
        JobSourceType.SUCCESSFACTORS,
        JobSourceType.ORACLE_CLOUD_HCM,
        JobSourceType.ICIMS,
        JobSourceType.SMARTRECRUITERS,
        JobSourceType.AVATURE,
        JobSourceType.EIGHTFOLD,
        JobSourceType.TALEO,
        JobSourceType.DAYFORCE,
        JobSourceType.TALEMETRY_TTC,
        JobSourceType.JOBVITE,
        JobSourceType.UKG_PRO,
        JobSourceType.PEOPLEADMIN,
        JobSourceType.RADANCY_TALENTBREW,
        JobSourceType.AMAZON_JOBS,
    }

    if source.source_type in standard_sources:
        limits = {
            "max_jobs": capped_int("max_jobs", 80, 80),
            "max_pages": max(1, capped_int("max_pages", 4, 4)),
            "detail_fetch_limit": capped_int("detail_fetch_limit", 10, 10),
            "fetch_budget_seconds": capped_float(
                "fetch_budget_seconds", 18.0, 18.0
            ),
            "timeout_seconds": max(
                1.0, capped_float("timeout_seconds", 5.0, 5.0)
            ),
            "min_request_interval_seconds": capped_float(
                "min_request_interval_seconds", 0.2, 0.2
            ),
        }
    elif source.source_type is JobSourceType.GENERIC_JSONLD:
        limits = {
            "max_pages": max(1, capped_int("max_pages", 3, 3)),
            "timeout_seconds": max(
                1.0, capped_float("timeout_seconds", 4.0, 4.0)
            ),
            "min_request_interval_seconds": capped_float(
                "min_request_interval_seconds", 0.15, 0.15
            ),
        }
    elif source.source_type is JobSourceType.BRANDED_REQUISITION:
        limits = {
            "max_jobs": capped_int("max_jobs", 80, 80),
            "max_pages": max(1, capped_int("max_pages", 2, 2)),
            "detail_fetch_limit": capped_int("detail_fetch_limit", 5, 5),
            "fetch_budget_seconds": capped_float(
                "fetch_budget_seconds", 22.0, 22.0
            ),
            "timeout_seconds": max(
                1.0, capped_float("timeout_seconds", 15.0, 15.0)
            ),
            "min_request_interval_seconds": capped_float(
                "min_request_interval_seconds", 0.5, 0.5
            ),
            "retry_attempts": 1,
            "retry_backoff_seconds": 0.0,
        }
    else:
        # Greenhouse, Lever, and Ashby expose complete boards in one bounded
        # public API request, so their saved settings remain unchanged.
        return source

    filters.update(limits)
    return replace(source, filters=filters)

def _discovery_search_preferences(
    owner_id: str, current: WorkflowState
) -> DiscoverySearchPreferences:
    stored = discovery_store.get_search_preferences(owner_id)
    if stored is not None:
        return stored

    source_profile = current.confirmed_profile or current.source_profile
    reusable = _load_reusable_career_profile(owner_id)
    target_titles = tuple(
        dict.fromkeys(
            value
            for value in (
                current.target_title,
                *reusable.target_titles,
            )
            if value
        )
    )
    locations = tuple(
        dict.fromkeys(
            value
            for value in (
                *reusable.preferred_locations,
                source_profile.contact.location,
            )
            if value
        )
    )
    return DiscoverySearchPreferences(
        owner_id=owner_id,
        target_titles=target_titles,
        preferred_locations=locations,
        accepted_workplace_types=reusable.accepted_workplace_types,
        preferred_keywords=tuple(
            dict.fromkeys((*reusable.industry_values, *reusable.skill_values))
        ),
    )


def _discovery_candidate_profile(
    current: WorkflowState, *, owner_id: str | None = None
) -> CandidateJobProfile:
    """Build traceable evidence plus owner-managed search preferences."""

    resolved_owner = owner_id or _application_owner_id()
    preferences = _discovery_search_preferences(resolved_owner, current)
    source_profile = current.confirmed_profile or current.source_profile
    base = CandidateJobProfile.from_resume_workflow(
        source_profile,
        _effective_career_background(current),
        target_title=current.target_title,
    )
    accepted_workplaces = tuple(preferences.accepted_workplace_types)
    reusable = _load_reusable_career_profile(resolved_owner)
    return replace(
        base,
        target_titles=preferences.target_titles or base.target_titles,
        preferred_locations=(
            preferences.preferred_locations or base.preferred_locations
        ),
        accepts_remote=(
            not accepted_workplaces
            or WorkplaceType.REMOTE in accepted_workplaces
        ),
        preferred_employment_types=preferences.preferred_employment_types,
        preferred_keywords=preferences.preferred_keywords,
        required_keywords=preferences.required_keywords,
        accepted_workplace_types=accepted_workplaces,
        minimum_salary=preferences.minimum_salary,
        minimum_salary_currency=preferences.minimum_salary_currency,
        minimum_salary_interval=preferences.minimum_salary_interval,
        excluded_terms=preferences.excluded_terms,
        excluded_title_terms=preferences.excluded_title_terms,
        require_title_match=preferences.require_title_match,
        require_location_match=preferences.require_location_match,
        require_workplace_match=preferences.require_workplace_match,
        require_employment_type_match=(
            preferences.require_employment_type_match
        ),
        requires_sponsorship=reusable.requires_sponsorship,
        work_authorized=reusable.work_authorized,
        eligibility_profile_complete=bool(reusable.work_authorization),
    )


def _discovery_checked_label(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return "Not refreshed yet"
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if checked.tzinfo is None or checked.utcoffset() is None:
            return "Last refresh time unavailable"
        checked = checked.astimezone(timezone.utc)
    except ValueError:
        return "Last refresh time unavailable"
    return "Last refreshed " + checked.strftime("%b %d, %Y at %H:%M UTC")


def _discovery_scan_time_label(raw: str, *, prefix: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if checked.tzinfo is None or checked.utcoffset() is None:
            return f"{prefix} time unavailable"
        checked = checked.astimezone(timezone.utc)
    except ValueError:
        return f"{prefix} time unavailable"
    return f"{prefix} " + checked.strftime("%b %d, %Y at %H:%M UTC")


def _discovery_source_scan_status(
    source: CompanySource, statuses_by_key: dict[str, Any]
) -> dict[str, Any]:
    """Build the manager-facing result of the latest shared catalog scan.

    Public catalog statuses are persisted independently of each user's
    materialized jobs, so a failed refresh remains visible even when older
    cached postings are still available.
    """

    try:
        status = statuses_by_key.get(public_source_key(source))
    except (TypeError, ValueError) as exc:
        return {
            "state": "issue",
            "label": "Configuration issue",
            "attempt_label": "The source configuration could not be evaluated.",
            "success_label": "",
            "message": str(exc),
            "job_count_label": "",
        }

    if status is None:
        if source.last_checked_at:
            return {
                "state": "legacy",
                "label": "Previously refreshed",
                "attempt_label": _discovery_scan_time_label(
                    source.last_checked_at, prefix="Last refreshed"
                ),
                "success_label": "",
                "message": (
                    "Detailed scan results were not recorded for this earlier refresh. "
                    "Run Refresh jobs for everyone to create a current result."
                ),
                "job_count_label": "",
            }
        return {
            "state": "not_scanned",
            "label": "Not scanned",
            "attempt_label": "No scan has been attempted yet.",
            "success_label": "",
            "message": "",
            "job_count_label": "",
        }

    attempt_label = _discovery_scan_time_label(
        status.last_attempt_at, prefix="Last scan"
    ) or "No scan has been attempted yet."
    success_label = ""
    if status.last_error and status.last_success_at:
        success_label = _discovery_scan_time_label(
            status.last_success_at, prefix="Last successful scan"
        )

    if status.last_error:
        normalized_error = str(status.last_error).casefold()
        if "robots.txt disallows" in normalized_error:
            indexed_timeout = (
                "indexed fallback was unavailable" in normalized_error
                and any(
                    token in normalized_error
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
            )
            if indexed_timeout:
                state = "issue"
                label = "Retry recommended"
                message = (
                    "The employer blocks direct listing scans, and the compliant "
                    "search-index fallback temporarily timed out. This is not a "
                    "reason to disable or remove the source. Retry the source scan; "
                    "previously collected jobs remain available. An authorized feed "
                    "or crawler allowlisting is still the best option for a complete "
                    "scan. "
                    f"Technical detail: {status.last_error}"
                )
            else:
                state = "permission_required"
                label = "Permission required"
                message = (
                    "The employer's robots policy blocks automated discovery for this "
                    "public search path. Career Bridge will not bypass that policy. "
                    "Previously collected jobs remain available while an authorized "
                    "feed, sitemap, allow-rule, or crawler allowlisting is requested. "
                    f"Technical detail: {status.last_error}"
                )
        else:
            state = "issue"
            label = "Issue"
            message = status.last_error
    elif status.last_attempt_at and status.complete_scan:
        state = "success"
        label = "Successful"
        message = "The source was scanned successfully."
    elif status.last_attempt_at:
        state = "limited"
        label = "Successful · limited"
        message = (
            "The interactive scan completed within browser-safe limits. "
            "The external scheduled runner can perform a complete scan."
        )
    else:
        state = "not_scanned"
        label = "Not scanned"
        message = ""

    job_count_label = ""
    if status.last_success_at:
        noun = "posting" if status.job_count == 1 else "postings"
        job_count_label = (
            f"{status.job_count} active public {noun} stored from the latest "
            "successful scan."
        )

    return {
        "state": state,
        "label": label,
        "attempt_label": attempt_label,
        "success_label": success_label,
        "message": message,
        "job_count_label": job_count_label,
    }


def _discovery_posted_label(job: Any) -> str:
    raw = str(job.posted_at or job.first_seen_at or "").strip()
    if not raw:
        return "Posting date not available"
    try:
        posted = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if posted.tzinfo is None or posted.utcoffset() is None:
            return "Posting date not available"
        age_days = max(0, (datetime.now(timezone.utc) - posted.astimezone(timezone.utc)).days)
    except ValueError:
        return "Posting date not available"
    if age_days == 0:
        return "Posted today"
    if age_days == 1:
        return "Posted 1 day ago"
    return f"Posted {age_days} days ago"


def _discovery_scan_schedule(owner_id: str) -> DiscoveryScanSchedule:
    stored = discovery_store.get_scan_schedule(owner_id)
    if stored is not None:
        return stored
    return DiscoveryScanSchedule(
        owner_id=owner_id,
        cadence=DiscoveryScheduleCadence.MANUAL,
        timezone_name=str(
            current_app.config.get("CAREER_BRIDGE_DEFAULT_TIMEZONE") or "UTC"
        ),
    )


def _discovery_schedule_time_label(value: datetime | None) -> str:
    if value is None:
        return "Manual refresh only"
    return value.astimezone(timezone.utc).strftime("%b %d, %Y at %H:%M UTC")


_EXPORT_NAMES = (
    '_split_discovery_values',
    '_source_identifier_value',
    '_default_source_url',
    '_normalized_company_source',
    '_company_source_identity',
    '_interactive_discovery_source',
    '_discovery_search_preferences',
    '_discovery_candidate_profile',
    '_discovery_checked_label',
    '_discovery_scan_time_label',
    '_discovery_source_scan_status',
    '_discovery_posted_label',
    '_discovery_scan_schedule',
    '_discovery_schedule_time_label',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
