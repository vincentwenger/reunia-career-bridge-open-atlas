from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Thin HTTP controllers for Job Discovery workspace and result details."""

_routes = DeferredRouteRegistry()

@_routes.get("/job-discovery")
def job_discovery_workspace():
    return render_job_discovery_workspace()


@_routes.get("/job-discovery/results.json")
def job_discovery_results_json():
    return build_job_discovery_results_response()


@_routes.get('/discovery/jobs/<source_id>/<job_id>/analysis')
def discovered_job_analysis(source_id: str, job_id: str):
    owner_id = _application_owner_id()
    job = discovery_store.get_discovered_job(owner_id, source_id, job_id)
    if job is None:
        abort(404)
    profile = _discovery_candidate_profile(state(), owner_id=owner_id)
    fit = discovery_store.get_fit_snapshot(
        owner_id,
        job.id,
        profile.fingerprint,
        job.description_fingerprint,
    ) or discovery_store.get_fit_snapshot(
        owner_id, job.id, profile.fingerprint
    )
    analysis_error = ""
    if fit is None:
        result = JobDiscoveryService(store=discovery_store).assess_existing_jobs(
            [job], profile
        )
        if result.ranked_jobs:
            fit = result.ranked_jobs[0].fit_snapshot
        elif result.analysis_errors:
            analysis_error = result.analysis_errors[0].message
    return render_template(
        "application_builder/_discovery_job_analysis.html",
        analysis=_discovery_card_analysis(job, profile, fit),
        analysis_error=analysis_error,
    )


_EXPORT_NAMES = (
    'job_discovery_workspace',
    'job_discovery_results_json',
    'discovered_job_analysis',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
