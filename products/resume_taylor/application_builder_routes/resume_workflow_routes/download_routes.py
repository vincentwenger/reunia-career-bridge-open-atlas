from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Snapshot downloads, durable export commands, and async-job status controllers."""

_routes = DeferredRouteRegistry()

@_routes.get('/download/workflow-snapshot/<stage>')
def download_workflow_snapshot(stage: str):
    """Download the exact resume stored for a completed workflow step."""
    if stage not in {"draft", "final"}:
        abort(404)
    current = state()
    snapshot = current.workflow_step_snapshots.get(stage)
    if snapshot is None or snapshot.proposal is None or snapshot.profile is None:
        abort(404)
    title = snapshot.target_title or (
        current.analysis.target_title if current.analysis is not None else ""
    )
    try:
        approved = _approved_resume_from_proposal(
            snapshot.profile, title, snapshot.proposal, current.analysis
        )
        document_bytes = export_resume_docx(
            resume_template_path(current.resume_career_stage),
            snapshot.profile,
            approved,
            **resume_export_kwargs(current),
        )
    except (TemplateError, ValueError) as exc:
        abort(409, description=str(exc))

    filename = (
        safe_filename(f"{snapshot.profile.name}_Job_Aligned_Resume") + ".docx"
        if stage == "draft"
        else final_resume_filename(snapshot.profile, title, "docx")
    )
    return send_file(
        BytesIO(document_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@_routes.get('/download/resume-version/<version>')
def download_resume_version(version: str):
    """Download the Application Baseline, Job-Aligned, or Final resume version."""
    if version not in {"initial", "draft", "final"}:
        abort(404)
    current = state()
    document_bytes = None
    if version == "initial":
        profile = current.source_profile
        proposal = build_initial_resume_proposal(profile, current.initial_evidence_proposal)
        title = initial_resume_title(profile)
        suffix = "Initial_Resume"
    elif version == "draft":
        if current.analysis is None or current.draft_proposal is None:
            abort(404)
        profile = current.confirmed_profile or current.source_profile
        proposal = current.draft_proposal
        title = current.analysis.target_title
        suffix = "Job_Aligned_Resume"
    else:
        if current.analysis is None or current.final_proposal is None:
            abort(404)
        profile = current.confirmed_profile or current.source_profile
        proposal = current.final_proposal
        title = effective_final_resume_title(current)
        suffix = ""
        if (
            current.final_resume_bytes is not None
            and current.final_report_proposal_fingerprint
            == _proposal_fingerprint(proposal)
        ):
            document_bytes = current.final_resume_bytes

    try:
        # Validate the proposal even when a cached DOCX already exists.
        approved = _approved_resume_from_proposal(
            profile, title, proposal, current.analysis
        )
        if document_bytes is None:
            document_bytes = export_resume_docx(
                resume_template_path(current.resume_career_stage),
                profile,
                approved,
                **resume_export_kwargs(current),
            )
    except (TemplateError, ValueError) as exc:
        abort(409, description=str(exc))

    download_name = (
        final_resume_filename(profile, title, "docx")
        if version == "final"
        else safe_filename(f"{profile.name}_{suffix}") + ".docx"
    )
    return send_file(
        BytesIO(document_bytes),
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@_routes.get('/download/source-profile')
def download_source_profile():
    current = state()
    source_profile = current.original_source_profile or current.source_profile
    if not source_profile.all_source_text().strip():
        abort(404)
    payload = json.dumps(source_profile.model_dump(), ensure_ascii=False, indent=2)
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="candidate_profile.json"'},
    )


@_routes.get('/download/confirmed-profile')
def download_confirmed_profile():
    current = state()
    if not current.save_confirmed_profile or current.confirmed_profile is None:
        abort(404)
    payload = json.dumps(current.confirmed_profile.model_dump(), ensure_ascii=False, indent=2)
    return Response(
        payload,
        mimetype="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="candidate_profile_with_confirmed_evidence.json"'
        },
    )


@_routes.get('/download/proposal')
def download_proposal():
    current = state()
    proposal = current.draft_proposal
    if proposal is None:
        abort(404)
    profile = current.confirmed_profile or current.source_profile
    payload = json.dumps(
        {
            "proposal": proposal.model_dump(),
            "candidate_answers": [answer.model_dump() for answer in current.candidate_answers],
            "supplemental_evidence": [
                item.model_dump() for item in profile.supplemental_evidence
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="tailoring_proposal.json"'},
    )


@_routes.post('/workflow/export')
def queue_final_resume_export():
    current = state()
    expects_json = request.accept_mimetypes.best == "application/json"
    if current.analysis is None or current.final_proposal is None:
        message = "Complete the Final Resume before preparing downloads."
        if expects_json:
            return jsonify(ok=False, message=message), 409
        flash(message, "error")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="final")
            + "#final-resume-actions"
        )
    try:
        job, created = _queue_export_job(refresh_all_reports=False)
    except (ValueError, WorkflowConflictError) as exc:
        if expects_json:
            return jsonify(ok=False, message=str(exc)), 409
        flash(str(exc), "error")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="final")
            + "#final-resume-actions"
        )
    if expects_json:
        return jsonify(_resume_job_response(job)), 202
    flash(
        job.message if created else "Word and PDF export generation is already running.",
        "success" if created else "info",
    )
    return redirect(
        url_for("application_builder.index", tab="tailoring", stage="final")
        + "#final-resume-actions"
    )


@_routes.get('/workflow/jobs/<job_id>')
def resume_async_job_status(job_id: str):
    job = async_job_store.get(_application_owner_id(), job_id)
    if job is None or not is_resume_async_job(job):
        abort(404)
    return jsonify(_resume_job_response(job))


@_routes.post('/workflow/jobs/<job_id>/cancel')
def cancel_resume_async_job(job_id: str):
    job = async_job_store.request_cancel(_application_owner_id(), job_id)
    if job is None or not is_resume_async_job(job):
        abort(404)
    return jsonify(_resume_job_response(job))


@_routes.post('/workflow/jobs/<job_id>/retry')
def retry_resume_async_job(job_id: str):
    owner_id = _application_owner_id()
    previous = async_job_store.get(owner_id, job_id)
    if previous is None or not is_resume_async_job(previous):
        abort(404)
    if not previous.status.terminal:
        return jsonify(
            ok=False, message="Wait for the current background job to finish."
        ), 409
    workflow_key = str(previous.payload.get("workflow_key") or "")
    if workflow_key != str(getattr(g, "workflow_key", "") or ""):
        return jsonify(
            ok=False,
            message="Open the application that owns this resume job before retrying it.",
        ), 409
    active = _active_resume_job()
    if active is not None:
        return jsonify(
            ok=False,
            message="Resume processing is already running for this application.",
        ), 409
    current = state()
    models = resolve_models(current)
    payload = dict(previous.payload)
    payload["guard"] = resume_job_guard(
        current,
        models,
        workflow_input_fingerprint=input_fingerprint(current, models),
    )
    retry = AsyncJob.queued(
        owner_id=owner_id,
        job_type=previous.job_type,
        payload=payload,
        total_count=previous.total_count,
        message="Resume processing was queued again.",
    )
    return jsonify(_resume_job_response(async_job_store.create(retry))), 202


_EXPORT_NAMES = (
    'download_workflow_snapshot',
    'download_resume_version',
    'download_source_profile',
    'download_confirmed_profile',
    'download_proposal',
    'queue_final_resume_export',
    'resume_async_job_status',
    'cancel_resume_async_job',
    'retry_resume_async_job',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
