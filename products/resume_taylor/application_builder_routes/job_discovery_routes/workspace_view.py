from __future__ import annotations

from time import perf_counter
from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Template and JSON read-model construction for Job Discovery."""

_routes = DeferredRouteRegistry()

def render_job_discovery_workspace():
    current = state()
    owner_id = g.application_owner_id
    active_discovery_assessment_job = async_job_store.find_active(
        owner_id, AsyncJobType.JOB_DISCOVERY_ASSESSMENT
    )
    discovery_view = (
        "settings" if request.args.get("view") == "settings" else "results"
    )
    discovery_owner_scope = hashlib.sha256(
        owner_id.encode("utf-8")
    ).hexdigest()[:16]
    g.job_discovery_timing_view = discovery_view
    g.job_discovery_timing_owner_scope = discovery_owner_scope
    g.job_discovery_timing_index_state = "not_applicable"

    sources_started_at = perf_counter()
    can_manage_catalog = _current_user_can_manage_job_catalog()
    discovery_sources = discovery_store.list_company_sources(
        SHARED_CATALOG_SOURCE_OWNER_ID
    )
    enabled_discovery_sources = tuple(
        source for source in discovery_sources if source.enabled
    )
    latest_discovery_check = max(
        (
            source.last_checked_at
            for source in discovery_sources
            if source.last_checked_at
        ),
        default="",
    )
    discovery_catalog_version = hashlib.sha256(
        json.dumps(
            [
                {
                    "id": source.id,
                    "enabled": source.enabled,
                    "revision": source.revision,
                    "last_checked_at": source.last_checked_at,
                }
                for source in discovery_sources
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    _record_job_discovery_phase(
        "jd_sources", sources_started_at, "Company source load"
    )

    preferences_started_at = perf_counter()
    discovery_preferences = _discovery_search_preferences(owner_id, current)
    _record_job_discovery_phase(
        "jd_preferences", preferences_started_at, "Search preferences"
    )

    discovery_source_scan_statuses: dict[str, dict[str, Any]] = {}
    if can_manage_catalog and discovery_view == "settings":
        catalog_status_started_at = perf_counter()
        catalog_statuses_by_key = {
            status.source_key: status
            for status in discovery_store.list_public_catalog_statuses()
        }
        discovery_source_scan_statuses = {
            source.id: _discovery_source_scan_status(
                source, catalog_statuses_by_key
            )
            for source in discovery_sources
        }
        _record_job_discovery_phase(
            "jd_catalog_status",
            catalog_status_started_at,
            "Catalog scan status",
        )

    template_context: dict[str, Any] = {
        "active_tab": "discovery",
        "discovery_view": discovery_view,
        "can_manage_job_catalog": can_manage_catalog,
        "discovery_source_count": len(discovery_sources),
        "enabled_discovery_source_count": len(enabled_discovery_sources),
        "discovery_refresh_sources": tuple(
            {"id": source.id, "company_name": source.company_name}
            for source in enabled_discovery_sources
        ),
        "discovery_assessment_run_limit": _discovery_assessment_run_limit(),
        "active_discovery_assessment_job": active_discovery_assessment_job,
        "discovery_checked_label": _discovery_checked_label(
            latest_discovery_check
        ),
        "discovery_catalog_version": discovery_catalog_version,
        "discovery_owner_scope": discovery_owner_scope,
        "discovery_sources": discovery_sources,
        "discovery_source_scan_statuses": discovery_source_scan_statuses,
        "discovery_source_types": (
            (JobSourceType.GREENHOUSE.value, "Greenhouse"),
            (JobSourceType.LEVER.value, "Lever"),
            (JobSourceType.ASHBY.value, "Ashby"),
            (JobSourceType.WORKDAY.value, "Workday"),
            (JobSourceType.SUCCESSFACTORS.value, "SAP SuccessFactors"),
            (JobSourceType.ORACLE_CLOUD_HCM.value, "Oracle Cloud HCM"),
            (JobSourceType.ICIMS.value, "iCIMS"),
            (JobSourceType.SMARTRECRUITERS.value, "SmartRecruiters"),
            (JobSourceType.AVATURE.value, "Avature"),
            (JobSourceType.EIGHTFOLD.value, "Eightfold"),
            (JobSourceType.TALEO.value, "Taleo"),
            (JobSourceType.DAYFORCE.value, "Dayforce"),
            (JobSourceType.TALEMETRY_TTC.value, "Talemetry / TTC Portals"),
            (JobSourceType.JOBVITE.value, "Jobvite"),
            (JobSourceType.UKG_PRO.value, "UKG Pro / UltiPro"),
            (JobSourceType.PEOPLEADMIN.value, "PeopleAdmin"),
            (
                JobSourceType.RADANCY_TALENTBREW.value,
                "Radancy / TalentBrew",
            ),
            (JobSourceType.AMAZON_JOBS.value, "Amazon Jobs"),
            (
                JobSourceType.BRANDED_REQUISITION.value,
                "Branded Requisition Portal",
            ),
            (
                JobSourceType.GENERIC_JSONLD.value,
                "Manual career-page URL (JSON-LD)",
            ),
        ),
        "discovery_preferences": discovery_preferences,
        "discovery_workplace_types": (
            (WorkplaceType.REMOTE.value, "Remote"),
            (WorkplaceType.HYBRID.value, "Hybrid"),
            (WorkplaceType.ONSITE.value, "Onsite"),
        ),
        "discovery_accepted_workplace_values": tuple(
            item.value
            for item in discovery_preferences.accepted_workplace_types
        ),
    }

    if discovery_view == "settings":
        schedule_started_at = perf_counter()
        discovery_schedule = _discovery_scan_schedule(
            SHARED_CATALOG_SOURCE_OWNER_ID
        )
        try:
            next_run = next_scheduled_run(discovery_schedule)
            schedule_error = ""
        except ValueError as exc:
            next_run = None
            schedule_error = str(exc)
        _record_job_discovery_phase(
            "jd_schedule", schedule_started_at, "Refresh schedule"
        )
        template_context.update(
            discovery_schedule=discovery_schedule,
            discovery_schedule_next_label=_discovery_schedule_time_label(
                next_run
            ),
            discovery_schedule_error=schedule_error,
            discovery_schedule_cadences=(
                (DiscoveryScheduleCadence.MANUAL.value, "Manual only"),
                (DiscoveryScheduleCadence.DAILY.value, "Daily"),
                (DiscoveryScheduleCadence.WEEKLY.value, "Weekly"),
            ),
            discovery_schedule_timezones=(
                "America/Los_Angeles",
                "America/Denver",
                "America/Chicago",
                "America/New_York",
                "UTC",
            ),
            discovery_weekdays=tuple(
                enumerate(
                    (
                        "Monday",
                        "Tuesday",
                        "Wednesday",
                        "Thursday",
                        "Friday",
                        "Saturday",
                        "Sunday",
                    )
                )
            ),
        )
    else:
        result_tab = _discovery_result_tab(
            "ignored"
            if request.args.get("show_ignored") == "1"
            else request.args.get("result_tab")
        )
        page = _discovery_positive_int(request.args.get("page"), default=1)
        per_page = _discovery_page_size(request.args.get("per_page"))
        discovery_filters = _discovery_result_filters(request.args)
        discovery_results_inline = request.args.get("render_results") == "1"
        requested_pagination = _discovery_pagination(
            0, page=page, per_page=per_page
        )
        result_context: dict[str, Any] = {
            "discovery_results_inline": discovery_results_inline,
            "discovery_result_tab": result_tab,
            "discovery_result_tabs": (
                ("recommended", "Recommended"),
                ("possible", "Possible matches"),
                ("pending", "Awaiting assessment"),
                ("low_match", "Low matches"),
                ("saved", "Saved"),
                ("ignored", "Ignored"),
            ),
            "discovery_pagination": requested_pagination,
            "discovery_page_sizes": _DISCOVERY_PAGE_SIZES,
            "discovery_filters": discovery_filters,
            "discovery_minimum_fit_options": _DISCOVERY_MINIMUM_FIT_OPTIONS,
            "discovery_confidence_options": (
                ("high,medium", "High and Medium"),
                ("high", "High only"),
                ("medium", "Medium only"),
                ("low", "Low only"),
                ("high,medium,low", "All confidence levels"),
            ),
            "discovery_recommendation_options": (
                ("all_viable", "Strong, Good, and Stretch"),
                ("strong", "Strong match only"),
                ("good", "Good match only"),
                ("stretch", "Stretch opportunities only"),
                ("all", "All recommendation tiers"),
            ),
            "discovery_sort_options": (
                ("recommended", "Recommended order"),
                ("job_fit", "Job Fit"),
                ("confidence", "Confidence"),
                ("newest", "Newest posting"),
            ),
            "discovery_results_fallback_url": url_for(
                "application_builder.job_discovery_workspace",
                result_tab=result_tab,
                page=page,
                per_page=per_page,
                min_fit=discovery_filters.minimum_fit,
                confidence=discovery_filters.confidence_query,
                recommendation=discovery_filters.recommendation_filter,
                sort=discovery_filters.sort_mode,
                render_results=1,
            ),
        }

        if discovery_results_inline:
            # Progressive-enhancement fallback for browsers without JavaScript.
            # Normal page requests render only the shell and skeleton; the
            # compact result page is loaded by ``job_discovery_results_json``.
            result_profile_started_at = perf_counter()
            discovery_profile = _discovery_candidate_profile(
                current,
                owner_id=owner_id,
            )
            _record_job_discovery_phase(
                "jd_result_profile",
                result_profile_started_at,
                "Candidate profile",
            )
            result_index_started_at = perf_counter()
            (
                discovery_cards,
                discovery_result_summary,
                discovery_pagination,
            ) = _discovery_result_cards(
                owner_id,
                discovery_profile,
                result_tab=result_tab,
                page=page,
                per_page=per_page,
                maximum_posting_age_days=(
                    discovery_preferences.maximum_posting_age_days
                ),
                filters=discovery_filters,
                allowed_source_ids=tuple(
                    source.id for source in enabled_discovery_sources
                ),
            )
            _record_job_discovery_phase(
                "jd_result_index",
                result_index_started_at,
                "Result index read",
            )
            g.job_discovery_timing_index_state = (
                "stale"
                if discovery_result_summary.get("index_stale")
                else "current"
            )
            result_context.update(
                discovery_cards=discovery_cards,
                discovery_dispositions=DiscoveryJobDisposition,
                discovery_result_summary=discovery_result_summary,
                discovery_pagination=discovery_pagination,
            )
        else:
            g.job_discovery_timing_index_state = "deferred_json"

        template_context.update(result_context)

    template_started_at = perf_counter()
    rendered_page = render_template(
        "application_builder/job_discovery.html",
        **template_context,
    )
    _record_job_discovery_phase(
        "jd_template", template_started_at, "Template render"
    )
    return rendered_page


def build_job_discovery_results_response():
    """Return one compact, private result page after the HTML shell renders."""

    request_started_at = perf_counter()
    owner_id = g.application_owner_id
    current = state(hydrate_documents=False)
    result_tab = _discovery_result_tab(
        "ignored"
        if request.args.get("show_ignored") == "1"
        else request.args.get("result_tab")
    )
    page = _discovery_positive_int(request.args.get("page"), default=1)
    per_page = _discovery_page_size(request.args.get("per_page"))
    discovery_filters = _discovery_result_filters(request.args)

    source_started_at = perf_counter()
    discovery_sources = discovery_store.list_company_sources(
        SHARED_CATALOG_SOURCE_OWNER_ID
    )
    enabled_discovery_sources = tuple(
        source for source in discovery_sources if source.enabled
    )
    source_ms = max(0.0, (perf_counter() - source_started_at) * 1000.0)

    preferences_started_at = perf_counter()
    discovery_preferences = _discovery_search_preferences(owner_id, current)
    preferences_ms = max(
        0.0, (perf_counter() - preferences_started_at) * 1000.0
    )

    profile_started_at = perf_counter()
    discovery_profile = _discovery_candidate_profile(
        current,
        owner_id=owner_id,
    )
    profile_ms = max(0.0, (perf_counter() - profile_started_at) * 1000.0)

    index_started_at = perf_counter()
    (
        discovery_cards,
        discovery_result_summary,
        discovery_pagination,
    ) = _discovery_result_cards(
        owner_id,
        discovery_profile,
        result_tab=result_tab,
        page=page,
        per_page=per_page,
        maximum_posting_age_days=(
            discovery_preferences.maximum_posting_age_days
        ),
        filters=discovery_filters,
        allowed_source_ids=tuple(
            source.id for source in enabled_discovery_sources
        ),
    )
    index_ms = max(0.0, (perf_counter() - index_started_at) * 1000.0)

    template_started_at = perf_counter()
    results_html = render_template(
        "application_builder/_discovery_results_content.html",
        can_manage_job_catalog=_current_user_can_manage_job_catalog(),
        discovery_source_count=len(discovery_sources),
        discovery_cards=discovery_cards,
        discovery_dispositions=DiscoveryJobDisposition,
        discovery_result_summary=discovery_result_summary,
        discovery_result_tab=result_tab,
        discovery_result_tabs=(
            ("recommended", "Recommended"),
            ("possible", "Possible matches"),
            ("pending", "Awaiting assessment"),
            ("low_match", "Low matches"),
            ("saved", "Saved"),
            ("ignored", "Ignored"),
        ),
        discovery_pagination=discovery_pagination,
        discovery_page_sizes=_DISCOVERY_PAGE_SIZES,
        discovery_filters=discovery_filters,
        discovery_minimum_fit_options=_DISCOVERY_MINIMUM_FIT_OPTIONS,
        discovery_confidence_options=(
            ("high,medium", "High and Medium"),
            ("high", "High only"),
            ("medium", "Medium only"),
            ("low", "Low only"),
            ("high,medium,low", "All confidence levels"),
        ),
        discovery_recommendation_options=(
            ("all_viable", "Strong, Good, and Stretch"),
            ("strong", "Strong match only"),
            ("good", "Good match only"),
            ("stretch", "Stretch opportunities only"),
            ("all", "All recommendation tiers"),
        ),
        discovery_sort_options=(
            ("recommended", "Recommended order"),
            ("job_fit", "Job Fit"),
            ("confidence", "Confidence"),
            ("newest", "Newest posting"),
        ),
    )
    template_ms = max(0.0, (perf_counter() - template_started_at) * 1000.0)
    total_ms = max(0.0, (perf_counter() - request_started_at) * 1000.0)

    page_url = (
        url_for(
            "application_builder.job_discovery_workspace",
            result_tab=result_tab,
            page=discovery_pagination["page"],
            per_page=discovery_pagination["per_page"],
            min_fit=discovery_filters.minimum_fit,
            confidence=discovery_filters.confidence_query,
            recommendation=discovery_filters.recommendation_filter,
            sort=discovery_filters.sort_mode,
        )
        + "#job-discovery-results"
    )
    response = jsonify(
        {
            "ok": True,
            "html": results_html,
            "summary": discovery_result_summary,
            "pagination": discovery_pagination,
            "result_tab": result_tab,
            "index_stale": bool(
                discovery_result_summary.get("index_stale")
            ),
            "page_url": page_url,
        }
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    response.headers["Server-Timing"] = ", ".join(
        (
            f'jd_json_sources;dur={source_ms:.2f};desc="Company sources"',
            f'jd_json_preferences;dur={preferences_ms:.2f};desc="Search preferences"',
            f'jd_json_profile;dur={profile_ms:.2f};desc="Candidate profile"',
            f'jd_json_index;dur={index_ms:.2f};desc="Result index page"',
            f'jd_json_template;dur={template_ms:.2f};desc="Result fragment render"',
            f'jd_json_total;dur={total_ms:.2f};desc="Result JSON total"',
        )
    )
    result_log_method = (
        current_app.logger.warning
        if total_ms >= _job_discovery_slow_request_threshold_ms()
        else current_app.logger.info
    )
    result_log_method(
        "Job Discovery result JSON owner_scope=%s tab=%s page=%s "
        "cards=%s stale=%s total_ms=%.2f index_ms=%.2f",
        hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:16],
        result_tab,
        discovery_pagination["page"],
        len(discovery_cards),
        bool(discovery_result_summary.get("index_stale")),
        total_ms,
        index_ms,
    )
    return response


_EXPORT_NAMES = (
    'render_job_discovery_workspace',
    'build_job_discovery_results_response',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
