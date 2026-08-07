from __future__ import annotations

from typing import Any


def register(namespace: dict[str, Any]) -> dict[str, Any]:
    """Register this feature's Application Builder routes and return shared helpers."""

    globals().update(namespace)
    @application_builder_bp.get("/applications/<application_id>/builder")
    def open_application_builder(application_id: str):
        application = application_store.get(_application_owner_id(), application_id)
        if application is None:
            abort(404)
        session["active_application_id"] = application.id
        try:
            application_store.set_active_application_id(
                _application_owner_id(), application.id
            )
        except Exception:
            current_app.logger.warning(
                "Could not synchronize active application %s",
                application.id,
                exc_info=True,
            )
        current = state()
        if not current.target_title:
            current.target_title = application.role
        if not current.job_description and application.job_description:
            current.job_description = application.job_description
        workflow_step = application.workflow_step or "setup"
        destination = url_for(
            "application_builder.index",
            tab="tailoring",
            stage=workflow_step,
            application_id=application.id,
        )
        if workflow_step == "confirmation":
            destination += "#confirmation-needed"
        return redirect(destination)

    @application_builder_bp.post("/applications/<application_id>/activate")
    def activate_application_builder(application_id: str):
        application = application_store.get(_application_owner_id(), application_id)
        if application is None:
            abort(404)
        session["active_application_id"] = application.id
        try:
            application_store.set_active_application_id(
                _application_owner_id(), application.id
            )
        except Exception:
            current_app.logger.warning(
                "Could not synchronize active application %s",
                application.id,
                exc_info=True,
            )
        return redirect(
            url_for(
                "application_builder.open_application_builder",
                application_id=application.id,
            )
        )

    @application_builder_bp.post("/applications/from-final")
    def save_final_as_application():
        current = state()
        if current.final_resume_bytes is None or current.final_proposal is None:
            flash("Create the Final Resume before saving an application.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="finalize")
                + "#finalize-resume"
            )

        analysis = current.analysis
        if analysis is None:
            flash("Run job analysis before saving an application.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="finalize")
                + "#finalize-resume"
            )
        profile = current.confirmed_profile or current.source_profile
        try:
            _approved_resume_from_proposal(
                profile,
                effective_final_resume_title(current),
                current.final_proposal,
                analysis,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="finalize")
                + "#finalize-resume"
            )
        company = (analysis.target_company if analysis is not None else "").strip()
        role = effective_final_resume_title(current).strip()
        report = current.final_report or current.optimization_report_after or current.updated_report
        fit_assessment = current_application_fit(
            current, application_store.list_for_owner(_application_owner_id())
        )
        resume_fingerprint = hashlib.sha256(current.final_resume_bytes).hexdigest()
        active = getattr(g, "active_application", None)
        alignment_score = (
            fit_assessment.score
            if fit_assessment is not None
            else report.job_match_score()
            if report is not None
            else None
        )
        overall_score = report.overall_score() if report is not None else None
        resume_filename = (
            current.final_report_filename
            or current_final_resume_filename(current, "docx")
        )

        if active is not None:
            saved = application_store.attach_resume_snapshot(
                _application_owner_id(),
                active.id,
                resume_version=FINAL_RESUME_LABEL,
                resume_style=normalize_resume_style(current.resume_style),
                alignment_score=alignment_score,
                overall_score=overall_score,
                resume_filename=resume_filename,
                resume_bytes=current.final_resume_bytes,
                resume_fingerprint=resume_fingerprint,
                resume_pdf_filename=current_final_resume_filename(current, "pdf"),
                resume_pdf_bytes=current.final_resume_pdf_bytes,
            )
            if saved is None:
                abort(404)
            application_store.save_impact_snapshot(
                _application_owner_id(),
                saved.id,
                build_workflow_impact_snapshot(current),
            )
            _persist_resume_findings(
                saved.id,
                build_resume_findings_snapshot(
                    current,
                    company=saved.company,
                    role=saved.role,
                    job_description=saved.job_description,
                ),
            )
            flash("Final Resume saved to this job application.", "success")
            return redirect(
                url_for("application_builder.index", tab="applications")
                + f"#application-{saved.id}"
            )

        existing = application_store.find_snapshot(
            _application_owner_id(),
            resume_fingerprint=resume_fingerprint,
            company=company or "Company not specified",
            role=role or "Role not specified",
        )
        if existing is not None:
            flash("This Final Resume is already attached to an application.", "info")
            return redirect(
                url_for("application_builder.index", tab="applications")
                + f"#application-{existing.id}"
            )

        created = application_store.create(
            _application_owner_id(),
            company=company or "Company not specified",
            role=role or "Role not specified",
            application_date="",
            status="ready_to_apply",
            resume_version=FINAL_RESUME_LABEL,
            resume_style=normalize_resume_style(current.resume_style),
            alignment_score=alignment_score,
            overall_score=overall_score,
            notes="Created from the completed Application Builder workflow.",
            next_action="",
            workflow_step="evidence_export",
            job_description=current.job_description,
            resume_filename=resume_filename,
            resume_bytes=current.final_resume_bytes,
            resume_fingerprint=resume_fingerprint,
            resume_pdf_filename=current_final_resume_filename(current, "pdf"),
            resume_pdf_bytes=current.final_resume_pdf_bytes,
        )
        application_store.save_impact_snapshot(
            _application_owner_id(),
            created.id,
            build_workflow_impact_snapshot(current),
        )
        _persist_resume_findings(
            created.id,
            build_resume_findings_snapshot(
                current,
                company=created.company,
                role=created.role,
                job_description=created.job_description,
            ),
        )
        session["active_application_id"] = created.id
        flash("Application created and Final Resume attached.", "success")
        return redirect(
            url_for("application_builder.index", tab="applications") + f"#application-{created.id}"
        )

    @application_builder_bp.post("/applications/create")
    def create_application_record():
        company = request.form.get("company", "").strip()
        role = request.form.get("role", "").strip()
        if not company or not role:
            flash("Company and job title are required.", "error")
            return redirect(url_for("application_builder.index", tab="applications") + "#new-application")

        raw_job_url = request.form.get("job_url", "").strip()
        job_url = normalize_job_url(raw_job_url)
        job_description = request.form.get("job_description", "").strip()
        if raw_job_url and not job_url:
            flash("Enter a valid HTTP or HTTPS job posting link.", "error")
            return redirect(url_for("application_builder.index", tab="applications") + "#new-application")
        if not job_url and not job_description:
            flash("Add a job posting link or paste the job description.", "error")
            return redirect(url_for("application_builder.index", tab="applications") + "#new-application")

        created = application_store.create(
            _application_owner_id(),
            company=company,
            role=role,
            job_url=job_url,
            interview_audience="",
            application_date="",
            status="draft",
            resume_version="Not started",
            resume_style="",
            alignment_score=None,
            notes="",
            next_action="",
            next_follow_up_date="",
            upcoming_event_date="",
            upcoming_event_type="",
            job_description=job_description,
            workflow_step="setup",
        )
        session["active_application_id"] = created.id
        flash("Job application created. Continue with Application and Job Setup.", "success")
        if request.form.get("start_builder") == "1":
            return redirect(
                url_for("application_builder.open_application_builder", application_id=created.id)
            )
        return redirect(
            url_for("application_builder.index", tab="applications") + f"#application-{created.id}"
        )

    @application_builder_bp.post("/applications/<application_id>/update")
    def update_application_record(application_id: str):
        updated = application_store.update(
            _application_owner_id(),
            application_id,
            company=request.form.get("company", ""),
            role=request.form.get("role", ""),
            job_url=request.form.get("job_url", ""),
            application_date=request.form.get("application_date", ""),
            status=request.form.get("status", "draft"),
            screening_received=request.form.get("screening_received") == "on",
            interview_received=request.form.get("interview_received") == "on",
            offer_received=request.form.get("offer_received") == "on",
            notes=request.form.get("notes", ""),
            next_follow_up_date=request.form.get("next_follow_up_date", ""),
            next_action=request.form.get("next_action", ""),
            upcoming_event_date=request.form.get("upcoming_event_date", ""),
            upcoming_event_type=request.form.get("upcoming_event_type", ""),
            job_description=request.form.get("job_description"),
            interview_audience=request.form.get("interview_audience", ""),
        )
        if updated is None:
            abort(404)
        flash("Application updated.", "success")
        return redirect(
            url_for("application_builder.index", tab="applications") + f"#application-{updated.id}"
        )

    @application_builder_bp.post("/applications/<application_id>/delete")
    def delete_application_record(application_id: str):
        owner_id = _application_owner_id()
        workflow_key = f"{owner_id}:application:{application_id}"
        workflow_state = store.peek(workflow_key)
        application = application_store.get(
            owner_id,
            application_id,
            include_resume_bytes=False,
        )
        if application is None:
            abort(404)
        try:
            from meeting_assistant.services.application_materials_service import (
                ApplicationMaterialsService,
            )

            ApplicationMaterialsService().delete_materials(owner_id, application_id)
        except Exception:
            current_app.logger.warning(
                "Could not clean application-only files before deleting %s",
                application_id,
                exc_info=True,
            )
        if not application_store.delete(owner_id, application_id):
            abort(404)
        if application.source_job_id:
            try:
                for discovery_state in discovery_store.list_job_states(owner_id):
                    if (
                        discovery_state.job_id == application.source_job_id
                        and discovery_state.disposition
                        is DiscoveryJobDisposition.APPLICATION_CREATED
                        and discovery_state.application_id == application_id
                    ):
                        discovery_store.put_job_state(
                            replace(
                                discovery_state,
                                disposition=DiscoveryJobDisposition.SAVED,
                                application_id="",
                                updated_at=utc_now_iso(),
                            )
                        )
            except Exception:
                # The application has already been deleted. Do not turn a
                # best-effort discovery-link repair into a failed deletion;
                # the Job Discovery read path also tolerates stale links.
                current_app.logger.warning(
                    "Could not repair Job Discovery state after deleting "
                    "application owner=%s application=%s source_job=%s",
                    owner_id,
                    application_id,
                    application.source_job_id,
                    exc_info=True,
                )
        if workflow_state is not None:
            _delete_workflow_document_objects(
                workflow_state,
                include_source=(
                    configured_application_backend(current_app.config) != "dynamodb"
                ),
            )
        store.delete(workflow_key)
        if str(getattr(g, "workflow_key", "")) == workflow_key:
            g.workflow_state_deleted = True
        if session.get("active_application_id") == application_id:
            session.pop("active_application_id", None)
        flash("Application removed.", "success")
        return redirect(url_for("application_builder.index", tab="applications"))

    @application_builder_bp.get("/applications/<application_id>/resume")
    def download_application_resume(application_id: str):
        application = application_store.get(_application_owner_id(), application_id)
        if application is None or not application.resume_bytes:
            abort(404)
        return send_file(
            BytesIO(application.resume_bytes),
            as_attachment=True,
            download_name=application.resume_filename or "Submitted_Resume.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


    @application_builder_bp.get("/download/final-resume")
    def download_final_resume():
        """Download the already generated final PDF without doing work in the request."""
        current = state()
        if current.analysis is None or current.final_proposal is None:
            abort(404)
        if current.final_resume_pdf_bytes is None:
            flash(
                "The final PDF is not ready yet. Prepare or retry the durable Word/PDF export from the Final Resume page.",
                "info",
            )
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="final")
                + "#final-resume-actions"
            )
        profile = (
            current.final_report_profile
            or current.confirmed_profile
            or current.source_profile
        )
        try:
            _approved_resume_from_proposal(
                profile,
                effective_final_resume_title(current),
                current.final_proposal,
                current.analysis,
            )
        except ValueError as exc:
            abort(409, description=str(exc))
        return send_file(
            BytesIO(current.final_resume_pdf_bytes),
            as_attachment=True,
            download_name=current_final_resume_filename(current, "pdf"),
            mimetype="application/pdf",
        )


    @application_builder_bp.get("/download/final-resume-word")
    def download_final_resume_word():
        """Download the editable Word alternative when an employer requests DOCX."""
        current = state()
        if current.analysis is None or current.final_proposal is None:
            abort(404)
        if current.final_resume_bytes is None:
            flash(
                "The final Word document is not ready yet. Prepare or retry the durable Word/PDF export from the Final Resume page.",
                "info",
            )
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="final")
                + "#final-resume-actions"
            )
        profile = (
            current.final_report_profile
            or current.confirmed_profile
            or current.source_profile
        )
        try:
            _approved_resume_from_proposal(
                profile,
                effective_final_resume_title(current),
                current.final_proposal,
                current.analysis,
            )
        except ValueError as exc:
            abort(409, description=str(exc))
        return send_file(
            BytesIO(current.final_resume_bytes),
            as_attachment=True,
            download_name=current_final_resume_filename(current, "docx"),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    return {
        name: value
        for name, value in locals().items()
        if name != "namespace" and not name.startswith("__")
    }
