from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Durable tailoring and report command controllers."""

_routes = DeferredRouteRegistry()

@_routes.post('/workflow/start')
def start_workflow():
    current = state()
    update_job_fields()
    action = request.form.get("action", "")
    if not current.source_profile.all_source_text().strip():
        flash("Create the Foundation Baseline Resume before starting this application.", "error")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="setup")
            + "#resume-import"
        )
    if not current.job_description.strip():
        flash("Paste or upload a job description first.", "error")
        return redirect(url_for("application_builder.index", tab="tailoring"))
    if action == "save_inputs":
        flash("Job description and target title saved.", "success")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="setup")
            + "#job-input"
        )
    if action not in {"initial_report", "tailor"}:
        flash("Unknown workflow action.", "error")
        return redirect(url_for("application_builder.index", tab="tailoring"))
    try:
        models = resolve_models(current)
        result_url = (
            url_for(
                "application_builder.index",
                tab="tailoring",
                stage="confirmation",
            )
            + "#confirmation-stage"
            if action == "tailor"
            else url_for(
                "application_builder.index",
                tab="reports",
                report="initial",
            )
            + "#reports-initial"
        )
        job, created = _queue_current_resume_job(
            job_type=AsyncJobType.RESUME_TAILORING,
            operation=action,
            total_count=4,
            message=(
                "Job analysis and initial tailoring were queued. You can leave this page and return later."
                if action == "tailor"
                else "Application Baseline Report generation was queued."
            ),
            result_url=result_url,
            models=models,
        )
        flash(
            job.message
            if created
            else "Resume processing is already running. Its saved progress has been reopened.",
            "success" if created else "info",
        )
    except (ResumeAIError, TemplateError, ValueError, WorkflowConflictError) as exc:
        flash(str(exc), "error")
    return redirect(
        url_for("application_builder.index", tab="tailoring", stage="setup")
        + "#job-input"
    )


@_routes.post('/reports/initial')
def run_initial_report():
    current = state()
    if not current.job_description.strip():
        flash(
            "Save a job description in Application and Job Setup before retrying the report.",
            "error",
        )
        return redirect(url_for("application_builder.index", tab="reports", report="initial"))
    try:
        job, created = _queue_report_job("initial")
        flash(
            job.message if created else "Initial Resume Report generation is already running.",
            "success" if created else "info",
        )
    except (ValueError, WorkflowConflictError) as exc:
        flash(str(exc), "error")
    return redirect(
        url_for("application_builder.index", tab="reports", report="initial")
        + "#reports-initial"
    )


@_routes.post('/reports/draft')
def run_draft_report():
    current = state()
    if current.analysis is None or current.draft_proposal is None or not current.confirmation_complete:
        flash("Complete the Job-Aligned Resume before retrying its report.", "error")
        return redirect(url_for("application_builder.index", tab="reports", report="draft"))
    try:
        job, created = _queue_report_job("draft")
        flash(
            job.message if created else "Job-Aligned Resume Report generation is already running.",
            "success" if created else "info",
        )
    except (ValueError, WorkflowConflictError) as exc:
        flash(str(exc), "error")
    return redirect(
        url_for("application_builder.index", tab="reports", report="draft")
        + "#reports-draft"
    )


@_routes.post('/reports/auto/<report_name>')
def run_automatic_report(report_name: str):
    if report_name not in {"initial", "draft", "final"}:
        abort(404)
    current = state()
    if report_name == "initial" and (
        current.analysis is None or current.initial_evidence_proposal is None
    ):
        return jsonify(ok=False, message="Initial report inputs are not ready."), 409
    if report_name == "draft" and (
        current.analysis is None
        or current.draft_proposal is None
        or not current.confirmation_complete
    ):
        return jsonify(ok=False, message="Job-Aligned report inputs are not ready."), 409
    if report_name == "final" and (
        current.analysis is None or current.final_proposal is None
    ):
        return jsonify(ok=False, message="Final report inputs are not ready."), 409
    try:
        job, _created = _queue_report_job(report_name)
    except (ValueError, WorkflowConflictError) as exc:
        return jsonify(ok=False, message=str(exc)), 409
    return jsonify(_resume_job_response(job)), 202


@_routes.post('/reports/final')
def run_final_report():
    current = state()
    if current.analysis is None or current.final_proposal is None:
        flash(
            "Complete Improve Resume Quality before retrying the Final Resume Report.",
            "error",
        )
        return redirect(url_for("application_builder.index", tab="reports", report="final"))
    try:
        job, created = _queue_report_job("final")
        flash(
            job.message if created else "Final Resume Report generation is already running.",
            "success" if created else "info",
        )
    except (ValueError, WorkflowConflictError) as exc:
        flash(str(exc), "error")
    return redirect(
        url_for("application_builder.index", tab="reports", report="final")
        + "#reports-final"
    )


_EXPORT_NAMES = (
    'start_workflow',
    'run_initial_report',
    'run_draft_report',
    'run_automatic_report',
    'run_final_report',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
