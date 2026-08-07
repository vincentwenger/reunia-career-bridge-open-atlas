from __future__ import annotations

from typing import Any

from job_discovery.models import DEFAULT_MAX_POSTING_AGE_DAYS

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Job Discovery result filtering, pagination, cached read models, and card construction."""

_routes = DeferredRouteRegistry()

_DISCOVERY_RESULT_INDEX_VERSION = "6"


_DISCOVERY_RESULT_TABS = (
    "recommended",
    "possible",
    "pending",
    "low_match",
    "saved",
    "ignored",
)


_DISCOVERY_PAGE_SIZES = (10, 20, 50)


_DISCOVERY_DEFAULT_PAGE_SIZE = 20


_DISCOVERY_MINIMUM_FIT_OPTIONS = (0, 50, 60, 70, 80)


_DISCOVERY_ASSESSMENT_BATCH_DEFAULT = 1


_DISCOVERY_ASSESSMENT_BATCH_MAX = 1


_DISCOVERY_ASSESSMENT_RUN_DEFAULT = 25


_DISCOVERY_ASSESSMENT_RUN_MAX = 100


def _discovery_result_tab(raw: Any) -> str:
    value = str(raw or "recommended").strip().casefold()
    return value if value in _DISCOVERY_RESULT_TABS else "recommended"


def _discovery_positive_int(raw: Any, *, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _discovery_page_size(raw: Any) -> int:
    value = _discovery_positive_int(
        raw, default=_DISCOVERY_DEFAULT_PAGE_SIZE
    )
    return (
        value
        if value in _DISCOVERY_PAGE_SIZES
        else _DISCOVERY_DEFAULT_PAGE_SIZE
    )


def _discovery_assessment_batch_size(raw: Any) -> int:
    configured = current_app.config.get(
        "CAREER_BRIDGE_DISCOVERY_ASSESSMENT_BATCH_SIZE",
        _DISCOVERY_ASSESSMENT_BATCH_DEFAULT,
    )
    try:
        default = int(configured)
    except (TypeError, ValueError):
        default = _DISCOVERY_ASSESSMENT_BATCH_DEFAULT
    default = min(_DISCOVERY_ASSESSMENT_BATCH_MAX, max(1, default))
    try:
        requested = int(raw)
    except (TypeError, ValueError):
        return default
    return min(_DISCOVERY_ASSESSMENT_BATCH_MAX, max(1, requested))


def _discovery_assessment_run_limit() -> int:
    configured = current_app.config.get(
        "CAREER_BRIDGE_DISCOVERY_ASSESSMENT_RUN_LIMIT",
        _DISCOVERY_ASSESSMENT_RUN_DEFAULT,
    )
    try:
        value = int(configured)
    except (TypeError, ValueError):
        value = _DISCOVERY_ASSESSMENT_RUN_DEFAULT
    return min(_DISCOVERY_ASSESSMENT_RUN_MAX, max(1, value))


def _discovery_result_filters(values: Any | None = None) -> DiscoveryResultFilters:
    source = values if values is not None else request.values
    raw_minimum_fit = source.get("min_fit", DEFAULT_MINIMUM_FIT)
    try:
        minimum_fit = int(raw_minimum_fit)
    except (TypeError, ValueError):
        minimum_fit = DEFAULT_MINIMUM_FIT
    minimum_fit = min(100, max(0, minimum_fit))
    return DiscoveryResultFilters(
        minimum_fit=minimum_fit,
        confidence_tiers=parse_confidence_query(
            source.get("confidence", ",".join(DEFAULT_CONFIDENCE_TIERS))
        ),
        recommendation_filter=str(
            source.get("recommendation", DEFAULT_RECOMMENDATION_FILTER)
        ),
        sort_mode=str(source.get("sort", DEFAULT_SORT_MODE)),
        # Public Job Discovery now always shows the worldwide catalog.
        # Country and U.S.-state result filters are intentionally ignored.
        country_code="",
        us_state_code="",
    )


def _discovery_results_url(
    *,
    result_tab: str | None = None,
    page: int | None = None,
    per_page: int | None = None,
    anchor: str = "job-discovery-results",
) -> str:
    selected_tab = _discovery_result_tab(
        result_tab if result_tab is not None else request.values.get("result_tab")
    )
    selected_page = _discovery_positive_int(
        page if page is not None else request.values.get("page"),
        default=1,
    )
    selected_size = _discovery_page_size(
        per_page if per_page is not None else request.values.get("per_page")
    )
    filters = _discovery_result_filters(request.values)
    return (
        url_for(
            "application_builder.job_discovery_workspace",
            result_tab=selected_tab,
            page=selected_page,
            per_page=selected_size,
            min_fit=filters.minimum_fit,
            confidence=filters.confidence_query,
            recommendation=filters.recommendation_filter,
            sort=filters.sort_mode,
        )
        + (f"#{anchor}" if anchor else "")
    )


def _discovery_card_analysis(
    job: Any,
    profile: CandidateJobProfile,
    fit: Any | None = None,
) -> dict[str, Any]:
    resolved_fit = fit or discovery_store.get_fit_snapshot(
        job.owner_id,
        job.id,
        profile.fingerprint,
        job.description_fingerprint,
    ) or discovery_store.get_fit_snapshot(
        job.owner_id,
        job.id,
        profile.fingerprint,
    )
    stage_one = evaluate_stage_one(job, profile)
    ranked = (
        ranked_from_snapshot(job, resolved_fit, stage_one=stage_one)
        if resolved_fit is not None and stage_one.passed
        else None
    )
    traceable_strengths = tuple(
        item
        for item in (
            resolved_fit.evidence_matches if resolved_fit is not None else ()
        )
        if item.status == "supported" and item.evidence
    )
    traceable_partial = tuple(
        item
        for item in (
            resolved_fit.evidence_matches if resolved_fit is not None else ()
        )
        if item.status == "partial" and item.evidence
    )
    return {
        "job": job,
        "fit": resolved_fit,
        "stage_one": stage_one,
        "search_priority": ranked.search_priority if ranked else None,
        "preference_score": stage_one.preference_score,
        "freshness_score": stage_one.freshness_score,
        "preference_components": stage_one.preference_components,
        "strongest_matches": traceable_strengths[:3],
        "partial_matches": traceable_partial[:3],
        "important_gaps": (
            resolved_fit.unsupported_requirements[:5] if resolved_fit else ()
        ),
    }


def _discovery_result_index_preference_fingerprint(
    profile: CandidateJobProfile,
    maximum_posting_age_days: int | None,
    filters: DiscoveryResultFilters,
    allowed_source_ids: tuple[str, ...],
) -> str:
    age_value = "any" if maximum_posting_age_days is None else str(maximum_posting_age_days)
    material = "|".join(
        (
            profile.preference_fingerprint,
            f"result_index_version={_DISCOVERY_RESULT_INDEX_VERSION}",
            f"maximum_posting_age_days={age_value}",
            f"minimum_fit={filters.minimum_fit}",
            f"confidence={filters.confidence_query}",
            f"recommendation={filters.recommendation_filter}",
            f"sort={filters.sort_mode}",
            "allowed_sources=" + ",".join(sorted(allowed_source_ids)),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _compact_discovery_job(job: DiscoveredJob) -> DiscoveredJob:
    """Remove detail-heavy fields that are not needed by the result card."""

    return replace(
        job,
        description="",
        skills=(),
        metadata={},
    )


def _compact_discovery_fit(fit: Any | None) -> Any | None:
    if fit is None:
        return None
    return replace(
        fit,
        supported_requirements=(),
        partial_requirements=(),
        unsupported_requirements=(),
        hard_blockers=(),
        evidence_matches=(),
    )


def _discovery_index_card(
    record: DiscoveryResultRecord,
) -> dict[str, Any]:
    application = (
        application_store.get(
            record.owner_id,
            record.application_id,
            include_resume_bytes=False,
        )
        if record.application_id
        else None
    )
    disposition = record.disposition
    if (
        disposition is DiscoveryJobDisposition.APPLICATION_CREATED
        and application is None
    ):
        # A cached result index can outlive an application that was
        # deleted before the discovery state was repaired. Keep the page
        # usable and let the user create a replacement workspace.
        disposition = DiscoveryJobDisposition.SAVED
    return {
        "job": record.job,
        "fit": record.fit,
        "state": None,
        "disposition": disposition,
        "application": application,
        "stage_one": None,
        "search_priority": record.search_priority,
        "preference_score": record.preference_score,
        "freshness_score": record.freshness_score,
        "posted_label": record.posted_label,
        "result_group": record.result_group,
    }


def _discovery_pagination(
    total: int,
    *,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    total = max(0, int(total))
    total_pages = max(1, (total + per_page - 1) // per_page)
    selected_page = min(_discovery_positive_int(page, default=1), total_pages)
    start = (selected_page - 1) * per_page
    end = min(total, start + per_page)
    return {
        "page": selected_page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "start": start + 1 if total else 0,
        "end": end,
        "offset": start,
        "has_previous": selected_page > 1,
        "has_next": selected_page < total_pages,
        "previous_page": max(1, selected_page - 1),
        "next_page": min(total_pages, selected_page + 1),
    }


def _discovery_paginate(
    records: list[Any],
    *,
    page: int,
    per_page: int,
) -> tuple[list[Any], dict[str, Any]]:
    pagination = _discovery_pagination(
        len(records), page=page, per_page=per_page
    )
    start = pagination["offset"]
    return records[start : start + per_page], pagination


def _discovery_result_cards(
    owner_id: str,
    profile: CandidateJobProfile,
    *,
    result_tab: str = "recommended",
    page: int = 1,
    per_page: int = _DISCOVERY_DEFAULT_PAGE_SIZE,
    maximum_posting_age_days: int | None = DEFAULT_MAX_POSTING_AGE_DAYS,
    filters: DiscoveryResultFilters | None = None,
    allowed_source_ids: tuple[str, ...] = (),
    rebuild_if_needed: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Return one page from the compact materialized discovery result index.

    Normal page requests never rebuild this read model. They return the
    current index, or the last materialized index while a separate bounded
    request refreshes it. Mutation and explicit prebuild requests opt in to
    the expensive full rebuild with ``rebuild_if_needed=True``.
    """

    selected_tab = _discovery_result_tab(result_tab)
    selected_size = _discovery_page_size(per_page)
    selected_filters = filters or DiscoveryResultFilters()
    evidence_fingerprint = profile.fingerprint
    preference_fingerprint = _discovery_result_index_preference_fingerprint(
        profile, maximum_posting_age_days, selected_filters, allowed_source_ids
    )
    revision_token = discovery_store.get_result_revision(owner_id)
    cached_summary = discovery_store.get_result_index_summary(
        owner_id,
        evidence_fingerprint,
        preference_fingerprint,
    )
    if cached_summary is not None and (
        cached_summary.revision_token == revision_token
        or not rebuild_if_needed
    ):
        index_stale = cached_summary.revision_token != revision_token
        selected_total = int(getattr(cached_summary, f"{selected_tab}_count"))
        pagination = _discovery_pagination(
            selected_total, page=page, per_page=selected_size
        )
        page_records = discovery_store.list_result_records_page(
            owner_id,
            evidence_fingerprint,
            preference_fingerprint,
            selected_tab,
            offset=pagination["offset"],
            limit=selected_size,
        )
        page_cards = [_discovery_index_card(item) for item in page_records]
        summary = {
            "recommended_count": cached_summary.recommended_count,
            "possible_count": cached_summary.possible_count,
            "pending_count": cached_summary.pending_count,
            "low_match_count": cached_summary.low_match_count,
            "saved_count": cached_summary.saved_count,
            "ignored_count": cached_summary.ignored_count,
            "filtered_count": cached_summary.filtered_count,
            "quality_filtered_count": cached_summary.quality_filtered_count,
            "age_filtered_count": cached_summary.age_filtered_count,
            "shown_count": len(page_cards),
            "ranked_count": (
                cached_summary.recommended_count
                + cached_summary.possible_count
                + cached_summary.low_match_count
            ),
            "pinned_count": cached_summary.saved_count,
            "top_count": len(page_cards),
            "index_stale": index_stale,
        }
        return page_cards, summary, pagination

    if not rebuild_if_needed:
        pagination = _discovery_pagination(0, page=page, per_page=selected_size)
        return [], {
            "recommended_count": 0,
            "possible_count": 0,
            "pending_count": 0,
            "low_match_count": 0,
            "saved_count": 0,
            "ignored_count": 0,
            "filtered_count": 0,
            "quality_filtered_count": 0,
            "age_filtered_count": 0,
            "shown_count": 0,
            "ranked_count": 0,
            "pinned_count": 0,
            "top_count": 0,
            "index_stale": True,
        }, pagination

    applications = application_store.list_for_owner(owner_id)
    applications_by_id = {item.id: item for item in applications}
    applications_by_source_job = {
        item.source_job_id: item
        for item in applications
        if item.source_job_id
    }
    jobs = discovery_store.list_discovered_jobs(owner_id, active_only=True)
    allowed_source_id_set = set(allowed_source_ids)
    states = {
        (item.source_id, item.job_id): item
        for item in discovery_store.list_job_states(owner_id)
    }
    fits_by_key: dict[tuple[str, str, str], Any] = {}
    for snapshot in discovery_store.list_fit_snapshots(owner_id):
        key = (
            snapshot.job_id,
            snapshot.profile_fingerprint,
            snapshot.description_fingerprint,
        )
        current = fits_by_key.get(key)
        if current is None or snapshot.analyzed_at > current.analyzed_at:
            fits_by_key[key] = snapshot

    groups: dict[str, list[dict[str, Any]]] = {
        name: [] for name in _DISCOVERY_RESULT_TABS
    }
    filtered_count = 0
    quality_filtered_count = 0
    age_filtered_count = 0

    for job in jobs:
        if not job_matches_location_filters(
            job,
            country_code=selected_filters.country_code,
            us_state_code=selected_filters.us_state_code,
        ):
            continue
        fit = fits_by_key.get(
            (job.id, profile.fingerprint, job.description_fingerprint)
        ) or fits_by_key.get((job.id, profile.fingerprint, ""))
        job_state = states.get((job.source_id, job.id))
        application = applications_by_source_job.get(job.id)
        if (
            application is None
            and job_state is not None
            and job_state.disposition
            is DiscoveryJobDisposition.APPLICATION_CREATED
        ):
            # DynamoDB Query is eventually consistent by default. The
            # application-created state may therefore be visible before
            # list_for_owner() includes the new application. Resolve the
            # recorded application ID directly (a strongly consistent
            # read in the production application store) before deciding
            # that the link is stale.
            application = applications_by_id.get(job_state.application_id)
            if application is None:
                application = application_store.get(
                    owner_id,
                    job_state.application_id,
                    include_resume_bytes=False,
                )
            if application is not None:
                applications_by_id[application.id] = application
                if application.source_job_id:
                    applications_by_source_job[application.source_job_id] = (
                        application
                    )

        stale_application_link = (
            application is None
            and job_state is not None
            and job_state.disposition
            is DiscoveryJobDisposition.APPLICATION_CREATED
        )
        if stale_application_link:
            current_app.logger.warning(
                "Ignoring stale Job Discovery application link owner=%s "
                "source=%s job=%s application=%s",
                owner_id,
                job.source_id,
                job.id,
                job_state.application_id,
            )
        disposition = (
            DiscoveryJobDisposition.APPLICATION_CREATED
            if application is not None
            else DiscoveryJobDisposition.SAVED
            if stale_application_link
            else job_state.disposition
            if job_state is not None
            else None
        )
        if (
            job.source_id not in allowed_source_id_set
            and disposition
            not in {
                DiscoveryJobDisposition.SAVED,
                DiscoveryJobDisposition.APPLICATION_CREATED,
                DiscoveryJobDisposition.IGNORED,
            }
        ):
            continue
        age_decision = evaluate_posting_age(
            job,
            maximum_age_days=maximum_posting_age_days,
        )
        if (
            not age_decision.eligible
            and disposition
            not in {
                DiscoveryJobDisposition.SAVED,
                DiscoveryJobDisposition.APPLICATION_CREATED,
                DiscoveryJobDisposition.IGNORED,
            }
        ):
            age_filtered_count += 1
            continue

        stage_one = evaluate_stage_one(job, profile)
        ranked = (
            ranked_from_snapshot(job, fit, stage_one=stage_one)
            if fit is not None and stage_one.passed
            else None
        )
        card = {
            "job": job,
            "fit": fit,
            "state": job_state,
            "disposition": disposition,
            "application": application,
            "stage_one": stage_one,
            "search_priority": ranked.search_priority if ranked else None,
            "preference_score": stage_one.preference_score,
            "freshness_score": stage_one.freshness_score,
            "posted_label": _discovery_posted_label(job),
            "recommendation_tier": (
                recommendation_tier(fit.recommendation) if fit is not None else "unassessed"
            ),
            "confidence_tier": (
                confidence_tier(fit.confidence) if fit is not None else "unassessed"
            ),
        }

        if disposition is DiscoveryJobDisposition.IGNORED:
            card["result_group"] = "ignored"
            groups["ignored"].append(card)
            continue
        if disposition in {
            DiscoveryJobDisposition.SAVED,
            DiscoveryJobDisposition.APPLICATION_CREATED,
        }:
            card["result_group"] = "saved"
            groups["saved"].append(card)
            continue
        if not stage_one.passed:
            filtered_count += 1
            continue
        if fit is None:
            card["result_group"] = "pending"
            groups["pending"].append(card)
            continue

        result_group = assessed_visibility_group(
            fit_score=fit.fit_score,
            recommendation=fit.recommendation,
            confidence=fit.confidence,
            filters=selected_filters,
        )
        if result_group is None:
            quality_filtered_count += 1
            continue
        card["result_group"] = result_group
        groups[result_group].append(card)

    def assessed_card_key(item: dict[str, Any]) -> tuple[object, ...]:
        fit = item.get("fit")
        if fit is None:
            return (0, 0, 0, 0, 0, 0, "")
        return assessed_sort_key(
            fit_score=fit.fit_score,
            recommendation=fit.recommendation,
            confidence=fit.confidence,
            preference_score=item["preference_score"],
            freshness_score=item["freshness_score"],
            posted_at=item["job"].posted_at or item["job"].first_seen_at,
            title=item["job"].title,
            sort_mode=selected_filters.sort_mode,
        )

    for group_name in ("recommended", "possible", "low_match"):
        groups[group_name].sort(key=assessed_card_key, reverse=True)
    groups["pending"].sort(
        key=lambda item: (
            item["preference_score"],
            item["freshness_score"],
            item["job"].posted_at,
            item["job"].title.casefold(),
        ),
        reverse=True,
    )
    groups["saved"].sort(
        key=lambda item: (
            1 if item["application"] is not None else 0,
            *assessed_card_key(item),
        ),
        reverse=True,
    )
    groups["ignored"].sort(
        key=lambda item: (
            item["job"].company.casefold(),
            item["job"].title.casefold(),
        )
    )

    result_records: list[DiscoveryResultRecord] = []
    for group_name, cards in groups.items():
        for ordinal, card in enumerate(cards):
            application = card["application"]
            result_records.append(
                DiscoveryResultRecord(
                    owner_id=owner_id,
                    evidence_fingerprint=evidence_fingerprint,
                    preference_fingerprint=preference_fingerprint,
                    result_group=group_name,
                    job=_compact_discovery_job(card["job"]),
                    recommendation_tier=card["recommendation_tier"],
                    confidence_tier=card["confidence_tier"],
                    visibility_category=group_name,
                    disposition=card["disposition"],
                    application_id=(
                        application.id if application is not None else ""
                    ),
                    fit=_compact_discovery_fit(card["fit"]),
                    preference_score=card["preference_score"],
                    freshness_score=card["freshness_score"],
                    search_priority=card["search_priority"],
                    posted_label=card["posted_label"],
                    sort_rank=f"{ordinal:08d}",
                )
            )

    index_summary = DiscoveryResultIndexSummary(
        owner_id=owner_id,
        evidence_fingerprint=evidence_fingerprint,
        preference_fingerprint=preference_fingerprint,
        revision_token=revision_token,
        recommended_count=len(groups["recommended"]),
        possible_count=len(groups["possible"]),
        pending_count=len(groups["pending"]),
        low_match_count=len(groups["low_match"]),
        saved_count=len(groups["saved"]),
        ignored_count=len(groups["ignored"]),
        filtered_count=filtered_count,
        quality_filtered_count=quality_filtered_count,
        age_filtered_count=age_filtered_count,
    )
    discovery_store.replace_result_index(index_summary, result_records)

    page_cards, pagination = _discovery_paginate(
        groups[selected_tab], page=page, per_page=selected_size
    )
    summary = {
        "recommended_count": index_summary.recommended_count,
        "possible_count": index_summary.possible_count,
        "pending_count": index_summary.pending_count,
        "low_match_count": index_summary.low_match_count,
        "saved_count": index_summary.saved_count,
        "ignored_count": index_summary.ignored_count,
        "filtered_count": index_summary.filtered_count,
        "quality_filtered_count": index_summary.quality_filtered_count,
        "age_filtered_count": index_summary.age_filtered_count,
        "shown_count": len(page_cards),
        "ranked_count": (
            index_summary.recommended_count
            + index_summary.possible_count
            + index_summary.low_match_count
        ),
        "pinned_count": index_summary.saved_count,
        "top_count": len(page_cards),
        "index_stale": False,
    }
    return page_cards, summary, pagination


def _prebuild_discovery_result_index(
    owner_id: str,
    *,
    current: WorkflowState | None = None,
    filters: DiscoveryResultFilters | None = None,
) -> dict[str, Any]:
    """Build the common owner-scoped result read model outside page GETs."""

    workflow_state = current or state()
    selected_filters = filters or DiscoveryResultFilters()
    preferences = _discovery_search_preferences(owner_id, workflow_state)
    enabled_sources = discovery_store.list_company_sources(
        SHARED_CATALOG_SOURCE_OWNER_ID, enabled_only=True
    )
    profile = _discovery_candidate_profile(workflow_state, owner_id=owner_id)

    # Self-heal stale profile-specific snapshots before materializing the
    # read model. This is cache-only: it reuses durable structured job
    # analyses and never starts a new OpenAI request.
    stored_fit_snapshots = discovery_store.list_fit_snapshots(owner_id)
    current_fit_keys = {
        (
            snapshot.job_id,
            snapshot.profile_fingerprint,
            snapshot.description_fingerprint,
        )
        for snapshot in stored_fit_snapshots
    }
    legacy_fit_keys = {
        (snapshot.job_id, snapshot.profile_fingerprint)
        for snapshot in stored_fit_snapshots
        if not snapshot.description_fingerprint
    }
    stale_jobs = [
        job
        for job in discovery_store.list_discovered_jobs(
            owner_id, active_only=True
        )
        if (
            job.id,
            profile.fingerprint,
            job.description_fingerprint,
        )
        not in current_fit_keys
        and (job.id, profile.fingerprint) not in legacy_fit_keys
    ]
    _restore_discovery_fit_snapshots_from_cached_analysis(
        owner_id,
        profile,
        stale_jobs,
    )

    build_kwargs = {
        "result_tab": "recommended",
        "page": 1,
        "per_page": _DISCOVERY_DEFAULT_PAGE_SIZE,
        "maximum_posting_age_days": preferences.maximum_posting_age_days,
        "filters": selected_filters,
        "allowed_source_ids": tuple(source.id for source in enabled_sources),
    }
    _discovery_result_cards(
        owner_id,
        profile,
        **build_kwargs,
        rebuild_if_needed=True,
    )
    # A concurrent discovery mutation can advance the revision while the
    # index is being assembled. Verify that the materialized summary was
    # committed and retry once against the new revision when necessary.
    _, summary, _ = _discovery_result_cards(
        owner_id,
        profile,
        **build_kwargs,
    )
    if summary["index_stale"]:
        _discovery_result_cards(
            owner_id,
            profile,
            **build_kwargs,
            rebuild_if_needed=True,
        )
        _, summary, _ = _discovery_result_cards(
            owner_id,
            profile,
            **build_kwargs,
        )
    return summary


def _try_prebuild_discovery_result_index(
    owner_id: str,
    *,
    current: WorkflowState | None = None,
    filters: DiscoveryResultFilters | None = None,
) -> bool:
    """Best-effort prebuild for mutation paths that must remain successful."""

    try:
        summary = _prebuild_discovery_result_index(
            owner_id,
            current=current,
            filters=filters,
        )
    except Exception:
        current_app.logger.exception(
            "Job Discovery result-index prebuild failed owner=%s", owner_id
        )
        return False
    return not bool(summary.get("index_stale"))


def _job_action_service() -> DiscoveredJobApplicationService:
    return DiscoveredJobApplicationService(
        discovery_store,
        application_store,
        description_fetcher=posting_description_fetcher,
    )


_EXPORT_NAMES = (
    '_DISCOVERY_RESULT_INDEX_VERSION',
    '_DISCOVERY_RESULT_TABS',
    '_DISCOVERY_PAGE_SIZES',
    '_DISCOVERY_DEFAULT_PAGE_SIZE',
    '_DISCOVERY_MINIMUM_FIT_OPTIONS',
    '_DISCOVERY_ASSESSMENT_BATCH_DEFAULT',
    '_DISCOVERY_ASSESSMENT_BATCH_MAX',
    '_DISCOVERY_ASSESSMENT_RUN_DEFAULT',
    '_DISCOVERY_ASSESSMENT_RUN_MAX',
    '_discovery_result_tab',
    '_discovery_positive_int',
    '_discovery_page_size',
    '_discovery_assessment_batch_size',
    '_discovery_assessment_run_limit',
    '_discovery_result_filters',
    '_discovery_results_url',
    '_discovery_card_analysis',
    '_discovery_result_index_preference_fingerprint',
    '_compact_discovery_job',
    '_compact_discovery_fit',
    '_discovery_index_card',
    '_discovery_pagination',
    '_discovery_paginate',
    '_discovery_result_cards',
    '_prebuild_discovery_result_index',
    '_try_prebuild_discovery_result_index',
    '_job_action_service',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
