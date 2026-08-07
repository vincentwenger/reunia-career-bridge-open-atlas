from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Application-facing commands for saving, ignoring, and adopting discovered jobs."""

_routes = DeferredRouteRegistry()

@_routes.post('/discovery/jobs/<source_id>/<job_id>/save')
def save_discovered_job(source_id: str, job_id: str):
    owner_id = _application_owner_id()
    try:
        _job_action_service().save(owner_id, source_id, job_id)
    except LookupError:
        abort(404)
    _try_prebuild_discovery_result_index(
        owner_id,
        current=state(),
        filters=_discovery_result_filters(request.form),
    )
    flash("Job saved for later review.", "success")
    return redirect(_discovery_results_url(anchor=f"discovered-job-{job_id}"))


@_routes.post('/discovery/jobs/<source_id>/<job_id>/ignore')
def ignore_discovered_job(source_id: str, job_id: str):
    owner_id = _application_owner_id()
    try:
        _job_action_service().ignore(owner_id, source_id, job_id)
    except LookupError:
        abort(404)
    _try_prebuild_discovery_result_index(
        owner_id,
        current=state(),
        filters=_discovery_result_filters(request.form),
    )
    flash("Job ignored. You can save it later to restore it.", "success")
    return redirect(_discovery_results_url(anchor=f"discovered-job-{job_id}"))


@_routes.post('/discovery/jobs/<source_id>/<job_id>/create-application')
def create_application_from_discovered_job(source_id: str, job_id: str):
    try:
        result = _job_action_service().create_application_workspace(
            _application_owner_id(), source_id, job_id
        )
    except LookupError:
        abort(404)
    session["active_application_id"] = result.application.id
    if result.previous_job_description:
        previous_fingerprint = hashlib.sha256(
            normalize_job_description(
                result.previous_job_description
            ).encode("utf-8")
        ).hexdigest()
        session["pending_application_job_description_refresh"] = {
            "application_id": result.application.id,
            "previous_fingerprint": previous_fingerprint,
        }
    if result.description_refreshed:
        flash(
            "The full job description was loaded from the employer posting "
            "and added to Application and Job Setup.",
            "success",
        )
    else:
        flash(
            "Application workspace created from the discovered posting."
            if result.created
            else "This discovered posting already has an application workspace.",
            "success" if result.created else "info",
        )
    if result.description_fetch_error:
        current_app.logger.info(
            "Posting detail lookup kept stored summary owner=%s source=%s "
            "job=%s error=%s",
            _application_owner_id(),
            source_id,
            job_id,
            result.description_fetch_error,
        )
        flash(
            "The employer site did not allow the complete description to be "
            "retrieved, so the available posting details were kept.",
            "warning",
        )
    return redirect(
        url_for(
            "application_builder.open_application_builder",
            application_id=result.application.id,
        )
    )


_EXPORT_NAMES = (
    'save_discovered_job',
    'ignore_discovered_job',
    'create_application_from_discovered_job',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
