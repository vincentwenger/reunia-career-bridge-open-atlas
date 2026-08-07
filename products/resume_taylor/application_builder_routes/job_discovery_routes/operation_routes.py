from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Thin controllers for assessment, hydration, index building, and refresh operations."""

_routes = DeferredRouteRegistry()

@_routes.post('/discovery/assess/pending')
def assess_pending_discovered_jobs():
    """Queue profile-specific Job Discovery assessment outside the web worker."""

    owner_id = _application_owner_id()
    payload, wants_json = _assessment_request_payload()
    profile = _discovery_candidate_profile(state(), owner_id=owner_id)
    if not (
        profile.target_titles
        or profile.verified_skills
        or profile.evidence_statements
        or profile.evidence_references
    ):
        message = (
            "Complete your Career Profile before assessing jobs so the ranking "
            "has a target role and verified evidence to use."
        )
        if wants_json:
            return jsonify({"ok": False, "message": message}), 409
        flash(message, "warning")
        return redirect(_discovery_results_url(result_tab="pending"))

    active = async_job_store.find_active(
        owner_id, AsyncJobType.JOB_DISCOVERY_ASSESSMENT
    )
    if active is not None:
        response = _async_job_response(active)
        response["message"] = (
            "A Job Discovery assessment is already running. Its saved progress "
            "has been reopened."
        )
        if wants_json:
            return jsonify(response), 202
        flash(response["message"], "info")
        return redirect(_discovery_results_url())

    all_pending = _pending_discovery_assessment_jobs(owner_id, profile)
    assess_all = str(payload.get("assess_all_remaining") or "").strip().casefold() in {
        "1", "true", "yes", "on"
    }
    limit = (
        max(
            1,
            min(
                500,
                int(
                    current_app.config.get(
                        "CAREER_BRIDGE_DISCOVERY_ASSESSMENT_JOB_MAX_ITEMS", 500
                    )
                ),
            ),
        )
        if assess_all
        else _discovery_assessment_run_limit()
    )
    selected = all_pending[:limit]
    if not selected:
        message = "All eligible pending jobs have been assessed."
        if wants_json:
            return jsonify(
                {
                    "ok": True,
                    "complete": True,
                    "terminal": True,
                    "status": AsyncJobStatus.COMPLETED.value,
                    "total_count": 0,
                    "attempted_count": 0,
                    "completed_count": 0,
                    "remaining_count": 0,
                    "failed_count": 0,
                    "message": message,
                }
            )
        flash(message, "success")
        return redirect(_discovery_results_url())

    job = AsyncJob.queued(
        owner_id=owner_id,
        job_type=AsyncJobType.JOB_DISCOVERY_ASSESSMENT,
        payload={
            "candidate_profile": candidate_profile_payload(profile),
            "profile_fingerprint": profile.fingerprint,
            "scope": "all" if assess_all else "limited",
            "jobs": [
                {
                    "source_id": item.source_id,
                    "job_id": item.id,
                    "label": f"{item.company} · {item.title}",
                }
                for item in selected
            ],
        },
        total_count=len(selected),
        message=(
            f"Queued {len(selected)} job{'s' if len(selected) != 1 else ''} "
            "for background assessment. You can leave this page."
        ),
    )
    stored = async_job_store.create(job)
    response = _async_job_response(stored)
    response["accepted"] = True
    if wants_json:
        return jsonify(response), 202
    flash(stored.message, "success")
    return redirect(_discovery_results_url())


@_routes.get('/discovery/assess/jobs/<job_id>')
def discovery_assessment_job_status(job_id: str):
    job = async_job_store.get(_application_owner_id(), job_id)
    if job is None or job.job_type is not AsyncJobType.JOB_DISCOVERY_ASSESSMENT:
        abort(404)
    return jsonify(_async_job_response(job))


@_routes.post('/discovery/assess/jobs/<job_id>/cancel')
def cancel_discovery_assessment_job(job_id: str):
    job = async_job_store.request_cancel(_application_owner_id(), job_id)
    if job is None or job.job_type is not AsyncJobType.JOB_DISCOVERY_ASSESSMENT:
        abort(404)
    return jsonify(_async_job_response(job))


@_routes.post('/discovery/assess/jobs/<job_id>/retry')
def retry_discovery_assessment_job(job_id: str):
    owner_id = _application_owner_id()
    previous = async_job_store.get(owner_id, job_id)
    if previous is None or previous.job_type is not AsyncJobType.JOB_DISCOVERY_ASSESSMENT:
        abort(404)
    if not previous.status.terminal:
        return jsonify(
            {"ok": False, "message": "Wait for the current background job to finish."}
        ), 409
    active = async_job_store.find_active(
        owner_id, AsyncJobType.JOB_DISCOVERY_ASSESSMENT
    )
    if active is not None:
        return jsonify(
            {"ok": False, "message": "A Job Discovery assessment is already running."}
        ), 409
    failed_keys = {
        (str(item.get("source_id") or ""), str(item.get("job_id") or ""))
        for item in previous.failed_items
    }
    jobs = [
        dict(item)
        for item in list(previous.payload.get("jobs") or ())
        if (
            str(dict(item).get("source_id") or ""),
            str(dict(item).get("job_id") or ""),
        ) in failed_keys
    ]
    if not jobs:
        return jsonify(
            {"ok": False, "message": "This job has no failed assessments to retry."}
        ), 409
    retry = AsyncJob.queued(
        owner_id=owner_id,
        job_type=AsyncJobType.JOB_DISCOVERY_ASSESSMENT,
        payload={**previous.payload, "jobs": jobs, "retry_of": previous.id},
        total_count=len(jobs),
        message=f"Queued {len(jobs)} failed assessment{'s' if len(jobs) != 1 else ''} for retry.",
    )
    return jsonify(_async_job_response(async_job_store.create(retry))), 202


@_routes.post('/discovery/result-index/prebuild')
def prebuild_discovery_result_index():
    """Materialize the selected result view outside the initial page GET."""

    owner_id = _application_owner_id()
    payload = request.get_json(silent=True) if request.is_json else request.form
    try:
        summary = _prebuild_discovery_result_index(
            owner_id,
            filters=_discovery_result_filters(payload or {}),
        )
    except Exception as exc:
        current_app.logger.exception(
            "Job Discovery result-index prebuild failed owner=%s", owner_id
        )
        return jsonify(
            {
                "ok": False,
                "changed": False,
                "message": str(exc),
            }
        ), 500
    return jsonify(
        {
            "ok": True,
            "changed": True,
            "recommended_count": summary["recommended_count"],
            "possible_count": summary["possible_count"],
            "pending_count": summary["pending_count"],
            "low_match_count": summary["low_match_count"],
            "saved_count": summary["saved_count"],
            "ignored_count": summary["ignored_count"],
        }
    )


@_routes.post('/discovery/catalog/hydrate')
def hydrate_discovered_jobs_from_shared_catalog():
    """Synchronize shared postings after the results page has rendered.

    Keeping this work in a separate request lets ``GET /job-discovery``
    return its existing durable read model without waiting for catalog
    queries or owner-scoped job writes.
    """

    owner_id = _application_owner_id()
    catalog_sources = discovery_store.list_company_sources(
        SHARED_CATALOG_SOURCE_OWNER_ID
    )
    if not catalog_sources:
        return jsonify(
            {
                "ok": True,
                "changed": False,
                "hydrated_job_count": 0,
            }
        )

    revision_before = discovery_store.get_result_revision(owner_id)
    try:
        hydrated_job_count = (
            JobDiscoveryService(store=discovery_store)
            .enable_shared_public_catalog()
            .hydrate_owner_from_shared_catalog(owner_id, catalog_sources)
        )
    except Exception as exc:
        current_app.logger.exception(
            "Deferred shared catalog hydration failed owner=%s",
            owner_id,
        )
        return jsonify(
            {
                "ok": False,
                "changed": False,
                "hydrated_job_count": 0,
                "message": str(exc),
            }
        ), 500

    revision_after = discovery_store.get_result_revision(owner_id)
    changed = revision_after != revision_before
    if changed:
        # This request is already outside the initial page render. Build the
        # default read model now so the subsequent reload remains index-only.
        result_index_prebuilt = _try_prebuild_discovery_result_index(owner_id)
    else:
        result_index_prebuilt = True
    return jsonify(
        {
            "ok": True,
            "changed": changed,
            "hydrated_job_count": hydrated_job_count,
            "result_index_prebuilt": result_index_prebuilt,
        }
    )


@_routes.post('/discovery/refresh/source')
def refresh_discovered_job_source():
    _require_job_catalog_manager()
    owner_id = _application_owner_id()
    payload = request.get_json(silent=True) if request.is_json else request.form
    source_id = str((payload or {}).get("source_id") or "").strip()
    source = discovery_store.get_company_source(
        SHARED_CATALOG_SOURCE_OWNER_ID, source_id
    )
    if source is None or not source.enabled:
        return jsonify(
            {
                "ok": False,
                "message": "The selected company source is unavailable or disabled.",
                "source_id": source_id,
            }
        ), 404
    try:
        result = _run_discovery_source_refresh(owner_id, [source])
    except Exception as exc:
        current_app.logger.exception(
            "Interactive company refresh failed actor=%s source=%s",
            owner_id,
            source_id,
        )
        return jsonify(
            {
                "ok": False,
                "source_id": source.id,
                "company_name": source.company_name,
                "outcome": "error",
                "message": f"{source.company_name} could not be refreshed.",
                "issues": [str(exc)],
            }
        ), 500
    return jsonify(_discovery_source_refresh_payload(source, result))


@_routes.post('/discovery/refresh')
def refresh_discovered_jobs():
    """No-JavaScript fallback that refreshes one source per request.

    The normal browser flow calls ``refresh_discovered_job_source`` once for
    each source and displays progress. Keeping this route bounded prevents a
    bulk form submission from exceeding the gateway timeout.
    """

    _require_job_catalog_manager()
    owner_id = _application_owner_id()
    return_to_settings = str(request.form.get("return_to") or "").strip() == "settings"
    redirect_url = (
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-settings"
        if return_to_settings
        else _discovery_results_url()
    )
    sources = discovery_store.list_company_sources(
        SHARED_CATALOG_SOURCE_OWNER_ID, enabled_only=True
    )
    if not sources:
        flash(
            "No enabled job sources are configured. Add a company source before refreshing jobs.",
            "warning",
        )
        return redirect(redirect_url)

    requested_source_id = str(request.form.get("source_id") or "").strip()
    selected_source = next(
        (source for source in sources if source.id == requested_source_id),
        None,
    )
    if requested_source_id and selected_source is None:
        flash(
            "The selected company source is unavailable or disabled. Enable it before scanning.",
            "warning",
        )
        return redirect(redirect_url)
    if selected_source is None:
        selected_source = min(
            sources,
            key=lambda source: (source.last_checked_at or "", source.company_name.casefold()),
        )

    result = _run_discovery_source_refresh(owner_id, [selected_source])
    _try_prebuild_discovery_result_index(
        owner_id,
        current=state(),
        filters=_discovery_result_filters(request.form),
    )
    payload = _discovery_source_refresh_payload(selected_source, result)
    message = payload["message"]
    if len(sources) > 1 and not requested_source_id:
        message += (
            " This fallback refresh processes one company at a time; "
            "click Refresh jobs again for the next company."
        )
    if payload["issues"]:
        message += f" {len(payload['issues'])} issue{'s' if len(payload['issues']) != 1 else ''} need review."
    flash(message, "warning" if payload["issues"] else "success")
    return redirect(redirect_url)


_EXPORT_NAMES = (
    'assess_pending_discovered_jobs',
    'discovery_assessment_job_status',
    'cancel_discovery_assessment_job',
    'retry_discovery_assessment_job',
    'prebuild_discovery_result_index',
    'hydrate_discovered_jobs_from_shared_catalog',
    'refresh_discovered_job_source',
    'refresh_discovered_jobs',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
