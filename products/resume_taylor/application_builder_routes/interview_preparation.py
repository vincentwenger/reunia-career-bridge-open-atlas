from __future__ import annotations

from typing import Any


def register(namespace: dict[str, Any]) -> dict[str, Any]:
    """Register this feature's Application Builder routes and return shared helpers."""

    globals().update(namespace)
    @application_builder_bp.get("/interview-preparation")
    def interview_preparation_workspace():
        applications, application = _selected_interview_application()
        preparation = None
        preparation_record = None
        evidence = None
        evidence_lookup: dict[str, str] = {}
        preparation_is_stale = False
        preparation_load_error = ""
        resume_findings = None
        resume_findings_fingerprint_value = ""
        reusable_profile = _load_reusable_career_profile(_application_owner_id())

        if application is not None:
            workflow_state = _workflow_state_for_application(application.id)
            evidence = build_verified_evidence_bundle(
                workflow_state, submitted_resume_bytes=application.resume_bytes
            )
            evidence_lookup = {item.id: item.text for item in evidence.items}
            resume_findings = _resume_findings_for_application(application.id)
            resume_findings_fingerprint_value = resume_findings_fingerprint(
                resume_findings
            )
            preparation_record = application_store.get_interview_preparation(
                _application_owner_id(), application.id
            )
            if preparation_record is not None:
                try:
                    saved_evidence = json.loads(
                        preparation_record.evidence_snapshot_json or "{}"
                    )
                    if isinstance(saved_evidence, dict):
                        for evidence_id, evidence_text in saved_evidence.items():
                            evidence_lookup.setdefault(
                                str(evidence_id), str(evidence_text)
                            )
                except (TypeError, json.JSONDecodeError):
                    pass
                try:
                    preparation = InterviewPreparationWorkspace.model_validate_json(
                        preparation_record.content_json
                    )
                except Exception:
                    preparation_load_error = (
                        "The saved interview preparation could not be read. Regenerate it "
                        "from the current job description and verified evidence."
                    )
                preparation_is_stale = bool(
                    preparation_record.job_description_fingerprint
                    != job_description_fingerprint(
                        application.job_description,
                        company=application.company,
                        role=application.role,
                        interview_audience=application.interview_audience,
                        career_profile_fingerprint=reusable_profile.fingerprint,
                    )
                    or preparation_record.evidence_fingerprint != evidence.fingerprint
                    or preparation_record.resume_findings_fingerprint
                    != resume_findings_fingerprint_value
                )

        active_interview_preparation_job = None
        if application is not None:
            active_interview_preparation_job = next(
                (
                    job
                    for job in async_job_store.list_for_owner(
                        _application_owner_id(), limit=50
                    )
                    if job.job_type is AsyncJobType.INTERVIEW_PREPARATION
                    and not job.status.terminal
                    and str(job.payload.get("application_id") or "") == application.id
                ),
                None,
            )

        return render_template(
            "application_builder/interview_preparation.html",
            active_tab="interview_preparation",
            career_section="interview_preparation",
            applications=applications,
            selected_application=application,
            preparation=preparation,
            preparation_record=preparation_record,
            preparation_is_stale=preparation_is_stale,
            preparation_load_error=preparation_load_error,
            evidence=evidence,
            evidence_lookup=evidence_lookup,
            resume_findings=resume_findings,
            reusable_career_profile=reusable_profile.as_prompt_dict(),
            active_interview_preparation_job=active_interview_preparation_job,
        )

    def _interview_async_job_response(job: AsyncJob) -> dict[str, Any]:
        payload = job.to_public_dict()
        payload.update(
            {
                "ok": True,
                "successful": job.status is not AsyncJobStatus.FAILED,
                "status_url": url_for(
                    "application_builder.interview_preparation_job_status",
                    job_id=job.id,
                ),
                "cancel_url": url_for(
                    "application_builder.cancel_interview_preparation_job",
                    job_id=job.id,
                ),
                "retry_url": url_for(
                    "application_builder.retry_interview_preparation_job",
                    job_id=job.id,
                ),
            }
        )
        return payload

    @application_builder_bp.post("/interview-preparation/generate")
    def generate_interview_preparation():
        """Queue interview-preparation generation outside the Flask request."""

        wants_json = request.is_json or "application/json" in str(
            request.headers.get("Accept") or ""
        )
        _, application = _selected_interview_application()
        if application is None:
            message = "Create a job application before generating interview preparation."
            if wants_json:
                return jsonify({"ok": False, "message": message}), 409
            flash(message, "error")
            return redirect(url_for("application_builder.interview_preparation_workspace"))
        redirect_url = (
            url_for(
                "application_builder.interview_preparation_workspace",
                application_id=application.id,
            )
            + "#interview-workspace"
        )
        if not application.job_description.strip():
            message = "Add the target job description before generating interview preparation."
            if wants_json:
                return jsonify({"ok": False, "message": message}), 409
            flash(message, "error")
            return redirect(redirect_url)

        active = next(
            (
                job
                for job in async_job_store.list_for_owner(
                    _application_owner_id(), limit=50
                )
                if job.job_type is AsyncJobType.INTERVIEW_PREPARATION
                and not job.status.terminal
                and str(job.payload.get("application_id") or "") == application.id
            ),
            None,
        )
        if active is not None:
            response = _interview_async_job_response(active)
            response["message"] = (
                "Interview preparation is already running. Its saved progress has been reopened."
            )
            if wants_json:
                return jsonify(response), 202
            flash(response["message"], "info")
            return redirect(redirect_url)

        workflow_state = _workflow_state_for_application(application.id)
        evidence = build_verified_evidence_bundle(
            workflow_state, submitted_resume_bytes=application.resume_bytes
        )
        if not evidence.items:
            message = (
                "Verified candidate evidence is required. Complete Confirm Relevant Experience "
                "or attach the evidence-reviewed Final Resume first."
            )
            if wants_json:
                return jsonify({"ok": False, "message": message}), 409
            flash(message, "error")
            return redirect(redirect_url)

        resume_findings = _resume_findings_for_application(application.id)
        resume_findings_fingerprint_value = _persist_resume_findings(
            application.id, resume_findings
        )
        reusable_profile = _load_reusable_career_profile(_application_owner_id())
        try:
            models = resolve_models(workflow_state)
        except ValueError as exc:
            if wants_json:
                return jsonify({"ok": False, "message": str(exc)}), 409
            flash(str(exc), "error")
            return redirect(redirect_url)

        evidence_snapshot = {
            item.id: item.text for item in evidence.items
        }
        snapshot = {
            "application_id": application.id,
            "company": application.company,
            "role": application.role,
            "interview_audience": application.interview_audience,
            "job_description": application.job_description,
            "job_description_fingerprint": job_description_fingerprint(
                application.job_description,
                company=application.company,
                role=application.role,
                interview_audience=application.interview_audience,
                career_profile_fingerprint=reusable_profile.fingerprint,
            ),
            "career_profile_context": reusable_profile.as_prompt_dict(),
            "evidence_items": [
                {"id": item.id, "text": item.text, "source": item.source}
                for item in evidence.items
            ],
            "evidence_fingerprint": evidence.fingerprint,
            "evidence_source_label": evidence.source_label,
            "evidence_snapshot_json": json.dumps(
                evidence_snapshot, ensure_ascii=False, sort_keys=True
            ),
            "resume_findings_fingerprint": resume_findings_fingerprint_value,
            "resume_findings_json": resume_findings.model_dump_json(),
            "model_name": models.analysis_tailoring_model,
            "reasoning_effort": models.analysis_tailoring_reasoning_effort,
        }
        snapshot_bytes = json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        snapshot_fingerprint = hashlib.sha256(snapshot_bytes).hexdigest()
        job = AsyncJob.queued(
            owner_id=_application_owner_id(),
            job_type=AsyncJobType.INTERVIEW_PREPARATION,
            payload={"application_id": application.id},
            total_count=1,
            message=(
                "Interview preparation was queued. You can leave this page and return later."
            ),
        )
        snapshot_key = application_object_key(
            current_app.config,
            _application_owner_id(),
            application.id,
            f"interview-preparation-input-{job.id}.json",
            category="async-jobs",
            fingerprint=snapshot_fingerprint,
        )
        try:
            document_store.put(
                snapshot_key,
                snapshot_bytes,
                "application/json",
                metadata={
                    "application-id": application.id,
                    "artifact-type": "async-interview-preparation-input",
                    "job-id": job.id,
                },
            )
        except ObjectStorageError as exc:
            message = f"Interview preparation could not be queued: {exc}"
            if wants_json:
                return jsonify({"ok": False, "message": message}), 503
            flash(message, "error")
            return redirect(redirect_url)
        job = replace(
            job,
            payload={
                "application_id": application.id,
                "company": application.company,
                "role": application.role,
                "snapshot_key": snapshot_key,
                "snapshot_fingerprint": snapshot_fingerprint,
            },
        )
        try:
            stored = async_job_store.create(job)
        except Exception:
            document_store.delete(snapshot_key)
            raise
        if wants_json:
            return jsonify(_interview_async_job_response(stored)), 202
        flash(stored.message, "success")
        return redirect(redirect_url)

    @application_builder_bp.get("/interview-preparation/jobs/<job_id>")
    def interview_preparation_job_status(job_id: str):
        job = async_job_store.get(_application_owner_id(), job_id)
        if job is None or job.job_type is not AsyncJobType.INTERVIEW_PREPARATION:
            abort(404)
        return jsonify(_interview_async_job_response(job))

    @application_builder_bp.post("/interview-preparation/jobs/<job_id>/cancel")
    def cancel_interview_preparation_job(job_id: str):
        job = async_job_store.request_cancel(_application_owner_id(), job_id)
        if job is None or job.job_type is not AsyncJobType.INTERVIEW_PREPARATION:
            abort(404)
        return jsonify(_interview_async_job_response(job))

    @application_builder_bp.post("/interview-preparation/jobs/<job_id>/retry")
    def retry_interview_preparation_job(job_id: str):
        owner_id = _application_owner_id()
        previous = async_job_store.get(owner_id, job_id)
        if previous is None or previous.job_type is not AsyncJobType.INTERVIEW_PREPARATION:
            abort(404)
        if not previous.status.terminal:
            return jsonify(
                {"ok": False, "message": "Wait for the current background job to finish."}
            ), 409
        active = next(
            (
                job
                for job in async_job_store.list_for_owner(owner_id, limit=50)
                if job.job_type is AsyncJobType.INTERVIEW_PREPARATION
                and not job.status.terminal
                and str(job.payload.get("application_id") or "")
                == str(previous.payload.get("application_id") or "")
            ),
            None,
        )
        if active is not None:
            return jsonify(
                {
                    "ok": False,
                    "message": "Interview preparation is already running for this application.",
                }
            ), 409
        retry = AsyncJob.queued(
            owner_id=owner_id,
            job_type=AsyncJobType.INTERVIEW_PREPARATION,
            payload=previous.payload,
            total_count=1,
            message="Interview preparation was queued again.",
        )
        return jsonify(
            _interview_async_job_response(async_job_store.create(retry))
        ), 202

    return {
        name: value
        for name, value in locals().items()
        if name != "namespace" and not name.startswith("__")
    }
