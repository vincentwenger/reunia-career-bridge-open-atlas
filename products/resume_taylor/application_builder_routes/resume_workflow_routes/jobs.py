from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Durable Resume Workflow job lookup, enqueueing, and artifact invalidation."""

_routes = DeferredRouteRegistry()

def _resume_jobs_for_current_workflow(*, limit: int = 50):
    owner_id = str(getattr(g, "application_owner_id", "") or "")
    workflow_key = str(getattr(g, "workflow_key", "") or "")
    return [
        job
        for job in async_job_store.list_for_owner(owner_id, limit=limit)
        if is_resume_async_job(job)
        and str(job.payload.get("workflow_key") or "") == workflow_key
    ]


def _active_resume_job(operation: str = ""):
    return active_resume_job_for_workflow(
        _resume_jobs_for_current_workflow(),
        str(getattr(g, "workflow_key", "") or ""),
        operation=operation,
    )


def _visible_resume_job():
    jobs = _resume_jobs_for_current_workflow()
    active = active_resume_job_for_workflow(
        jobs, str(getattr(g, "workflow_key", "") or "")
    )
    if active is not None:
        return active
    latest = jobs[0] if jobs else None
    if latest is not None and latest.status in {
        AsyncJobStatus.FAILED,
        AsyncJobStatus.CANCELED,
    }:
        return latest
    return None


def _queue_current_resume_job(
    *,
    job_type,
    operation: str,
    total_count: int,
    message: str,
    result_url: str,
    models=None,
    extra_payload=None,
):
    current = state()
    if models is None:
        models = resolve_models(current)
    current_guard = resume_job_guard(
        current,
        models,
        workflow_input_fingerprint=input_fingerprint(current, models),
    )

    active = _active_resume_job()
    if active is not None:
        same_operation = (
            active.job_type is job_type
            and str(active.payload.get("operation") or "") == operation
        )
        active_guard = dict(active.payload.get("guard") or {})
        if same_operation and active_guard == current_guard:
            return active, False
        if same_operation:
            canceled = async_job_store.request_cancel(
                str(getattr(g, "application_owner_id", "") or ""),
                active.id,
            )
            if canceled is not None and canceled.status.terminal:
                active = None
            else:
                raise ValueError(
                    "A previous resume process is still finishing with older inputs. "
                    "Its cancellation was requested. Start tailoring again after it stops."
                )
        else:
            raise ValueError(
                "Another Resume Workflow operation is already running for this application. "
                "Wait for it to finish or cancel it before starting a different action."
            )

    _persist_workflow_state_now()
    current = state()
    if models is None:
        models = resolve_models(current)
    current_guard = resume_job_guard(
        current,
        models,
        workflow_input_fingerprint=input_fingerprint(current, models),
    )
    active_application = getattr(g, "active_application", None)
    job = queued_resume_job(
        owner_id=str(getattr(g, "application_owner_id", "") or ""),
        job_type=job_type,
        workflow_key=str(getattr(g, "workflow_key", "") or ""),
        operation=operation,
        application_id=(active_application.id if active_application is not None else ""),
        guard=current_guard,
        result_url=result_url,
        total_count=total_count,
        message=message,
        extra_payload=extra_payload,
    )
    return async_job_store.create(job), True


def _resume_job_response(job):
    return resume_job_public_payload(job, url_for=url_for)


def _queue_report_job(report_name: str):
    current = state()
    models = resolve_models(current)
    result_url = (
        url_for(
            "application_builder.index",
            tab="reports",
            report=report_name,
        )
        + f"#reports-{report_name}"
    )
    return _queue_current_resume_job(
        job_type=AsyncJobType.RESUME_REPORT,
        operation=report_name,
        total_count=2 if report_name == "initial" else 1,
        message=f"{report_name.title()} Resume Report generation was queued.",
        result_url=result_url,
        models=models,
        extra_payload={"report_name": report_name},
    )


def _queue_export_job(*, refresh_all_reports: bool = False):
    current = state()
    models = resolve_models(current)
    return _queue_current_resume_job(
        job_type=AsyncJobType.RESUME_EXPORT,
        operation="export",
        total_count=1,
        message=(
            "Resume Reports and Word/PDF exports were queued."
            if refresh_all_reports
            else "Word and PDF export generation was queued."
        ),
        result_url=(
            url_for(
                "application_builder.index",
                tab="tailoring",
                stage="final",
            )
            + "#final-resume-actions"
        ),
        models=models,
        extra_payload={"refresh_all_reports": bool(refresh_all_reports)},
    )


def _invalidate_final_report_and_exports(current) -> None:
    """Clear style-dependent report/export artifacts without erasing optimization history."""
    current.final_report = None
    current.final_report_input_fingerprint = None
    current.final_report_proposal_fingerprint = None
    current.final_report_proposal = None
    current.final_report_profile = None
    current.final_report_candidate_answers = []
    current.final_report_created_at = ""
    current.final_report_filename = ""
    current.final_report_error = ""
    current.final_report_exact = False
    current.final_resume_bytes = None
    current.final_resume_pdf_bytes = None
    current.final_resume_docx_key = ""
    current.final_resume_pdf_key = ""
    current.final_resume_docx_fingerprint = ""
    current.final_resume_pdf_fingerprint = ""
    current.final_resume_pdf_error = ""


_EXPORT_NAMES = (
    '_resume_jobs_for_current_workflow',
    '_active_resume_job',
    '_visible_resume_job',
    '_queue_current_resume_job',
    '_resume_job_response',
    '_queue_report_job',
    '_queue_export_job',
    '_invalidate_final_report_and_exports',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
