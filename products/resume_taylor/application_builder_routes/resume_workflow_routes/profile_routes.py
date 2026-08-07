from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Profile import, reset, and application-baseline controllers."""

_routes = DeferredRouteRegistry()

@_routes.post('/profile/upload')
def upload_profile():
    uploaded = request.files.get("profile_file")
    import_strategy = str(request.form.get("import_strategy") or "").strip().casefold()
    return_to = str(request.form.get("return_to") or "").strip().casefold()
    is_career_translation = return_to == "career_translation"
    redirect_target = (
        url_for("application_builder.career_translation_workspace")
        if is_career_translation
        else (
            url_for("application_builder.index", tab="tailoring", stage="setup") + "#resume-import"
            if return_to == "setup"
            else url_for("application_builder.index", tab="configuration") + "#candidate-profile"
        )
    )
    current = state()
    existing_creation_method = _baseline_creation_method(current)
    manual_profile_to_merge: CandidateProfile | None = None

    if not is_career_translation:
        flash(
            "Application Baseline is managed in Foundation. Update the Baseline Resume there; applications that have not started tailoring will sync automatically.",
            "info",
        )
        return redirect(
            url_for("application_builder.career_translation_workspace")
        )

    if is_career_translation:
        reusable_profile = getattr(
            g, "reusable_career_profile", ReusableCareerProfile()
        )
        current.career_background.target_country = (
            reusable_profile.target_country if reusable_profile.enabled else ""
        )
        current.career_background.resume_language = " ".join(
            str(request.form.get("resume_language") or "").split()
        )
        current.career_background.target_role = normalize_target_title(
            str(request.form.get("target_role") or "")
        )

    if not uploaded or not uploaded.filename:
        if not is_career_translation:
            flash("Choose a PDF, Word, text, Markdown, or Verified Resume Evidence JSON file.", "error")
            return redirect(redirect_target)
        if not current.source_profile.all_source_text().strip():
            flash(
                "Baseline Resume preferences saved. Import a resume to generate the Baseline Resume.",
                "success",
            )
            return redirect(redirect_target)
        try:
            models = resolve_models(current)
            choice = _resolved_resume_language(current)
            source_language = _source_resume_language_code(current)
            if source_language and source_language == choice.code:
                _ensure_target_language_profile(current, None)
                if (
                    _baseline_creation_method(current) == "mixed"
                    and current.manual_source_profile is not None
                ):
                    current.source_profile = merge_candidate_profiles(
                        current.source_profile, current.manual_source_profile
                    )
                roles_synced = _sync_baseline_roles_to_evidence_library(current)
                flash(
                    f"Baseline Resume saved in {choice.name}. The imported resume already uses that language, so no translation was needed.",
                    "success",
                )
                if not roles_synced:
                    flash(
                        "The Baseline Resume was saved, but its employment roles could not be synchronized to Career Evidence Library.",
                        "warning",
                    )
            else:
                result_url = (
                    url_for("application_builder.career_translation_workspace")
                    + "#initial-resume"
                )
                job, created = _queue_current_resume_job(
                    job_type=AsyncJobType.RESUME_BASELINE_TRANSLATION,
                    operation="baseline_translation",
                    total_count=2,
                    message="Baseline Resume translation was queued. You can leave this page and return later.",
                    result_url=result_url,
                    models=models,
                )
                flash(
                    job.message if created else "Baseline Resume generation is already running.",
                    "success" if created else "info",
                )
        except (ResumeAIError, ValueError, RuntimeError, WorkflowConflictError) as exc:
            flash(
                "Baseline Resume preferences were saved, but generation could not be queued: "
                + str(exc),
                "warning",
            )
        return redirect(redirect_target)

    if (
        existing_creation_method in {"manual", "mixed"}
        and current.source_profile.all_source_text().strip()
    ):
        if import_strategy not in {"replace", "merge"}:
            flash(
                "Choose whether the imported resume should replace the manually entered Baseline Resume or merge new information for review.",
                "error",
            )
            return redirect(redirect_target)
        if import_strategy == "merge":
            manual_profile_to_merge = (
                current.manual_source_profile.model_copy(deep=True)
                if current.manual_source_profile is not None
                else current.source_profile.model_copy(deep=True)
            )

    filename = uploaded.filename
    data = uploaded.read()
    replacing_existing_baseline = bool(
        current.source_resume_key
        or current.source_resume_fingerprint
        or current.profile_upload_name
        or current.source_profile.all_source_text().strip()
    )
    translation_ai: ResumeAI | None = None
    import_adjustments: list[str] = []
    detected_import_language = ""
    try:
        if resume_extension(filename) == ".json":
            profile = load_candidate_profile_bytes(data)
            detected_import_language = detect_text_language(
                profile.all_source_text()
            )
        else:
            resume_text = extract_resume_text(data, filename)
            detected_import_language = detect_text_language(resume_text)
            models = resolve_models(current)
            translation_ai = ResumeAI(
                models.analysis_tailoring_model,
                reasoning_effort=models.analysis_tailoring_reasoning_effort,
            )
            profile = translation_ai.create_candidate_profile_from_resume(
                resume_text=resume_text,
                filename=filename,
            )
            import_adjustments = list(
                getattr(translation_ai, "last_resume_import_adjustments", [])
                or []
            )
    except (ResumeAIError, ValueError, RuntimeError) as exc:
        flash(f"Could not import the resume: {exc}", "error")
        return redirect(redirect_target)
    except Exception as exc:
        current_app.logger.exception("Unexpected resume import failure")
        flash(f"Could not import the resume: {exc}", "error")
        return redirect(redirect_target)

    source_fingerprint = hashlib.sha256(data).hexdigest()
    source_object_key = workflow_object_key(
        current_app.config,
        str(getattr(g, "application_owner_id", "") or ""),
        str(getattr(g, "workflow_key", "") or "scratch"),
        "original-resume",
        filename,
        source_fingerprint,
    )
    document_store.put(
        source_object_key,
        data,
        uploaded.mimetype or "application/octet-stream",
        metadata={
            "artifact-type": "original-resume",
            "source-fingerprint": source_fingerprint,
        },
    )
    previous_source_key = current.source_resume_key
    current.original_source_profile = profile.model_copy(deep=True)
    current.source_profile = profile
    current.source_resume_language = (
        detected_import_language
        or detect_text_language(profile.all_source_text())
    )
    current.source_profile_language = ""
    current.source_profile_translation_fingerprint = ""
    current.profile_upload_name = filename
    current.source_resume_key = source_object_key
    current.source_resume_fingerprint = source_fingerprint
    current.source_resume_contact_links_fingerprint = source_fingerprint
    current.baseline_creation_method = "import"
    current.manual_source_profile = None

    translation_warning = ""
    translation_queued = False
    translated_choice = _resolved_resume_language(current)
    source_language = _source_resume_language_code(current)
    if source_language and source_language == translated_choice.code:
        try:
            # This path is deterministic and does not instantiate or call the
            # AI provider when the imported resume already uses the selected
            # Baseline Resume language.
            _ensure_target_language_profile(current, None)
        except (ResumeAIError, ValueError, RuntimeError) as exc:
            translation_warning = str(exc)
    else:
        translation_queued = True

    if manual_profile_to_merge is not None:
        current.manual_source_profile = manual_profile_to_merge
        current.baseline_creation_method = "mixed"
        if not translation_queued:
            current.source_profile = merge_candidate_profiles(
                current.source_profile,
                manual_profile_to_merge,
            )

    active_application = getattr(g, "active_application", None)
    if active_application is not None:
        application_store.update_builder_progress(
            str(getattr(g, "application_owner_id", "") or ""),
            active_application.id,
            workflow_step=active_application.workflow_step,
            original_resume_key=source_object_key,
        )
    current.clear_results()

    # Commit the replacement before removing the document referenced by the
    # previously saved baseline. This prevents the redirect from reloading an
    # older profile and avoids leaving the stored workflow pointed at a file
    # that was already deleted when a concurrent update wins.
    try:
        _persist_workflow_state_now()
    except WorkflowConflictError as exc:
        if source_object_key != previous_source_key:
            document_store.delete(source_object_key)
        return workflow_conflict_response(exc)
    except Exception:
        if source_object_key != previous_source_key:
            document_store.delete(source_object_key)
        raise

    if previous_source_key and previous_source_key != source_object_key:
        try:
            document_store.delete(previous_source_key)
        except Exception:
            current_app.logger.exception(
                "Could not remove the replaced Baseline Resume source document"
            )

    roles_synced = True
    translation_job = None
    if translation_queued and not translation_warning:
        try:
            models = resolve_models(current)
            translation_job, _created = _queue_current_resume_job(
                job_type=AsyncJobType.RESUME_BASELINE_TRANSLATION,
                operation="baseline_translation",
                total_count=2,
                message="Baseline Resume translation was queued. You can leave this page and return later.",
                result_url=(
                    url_for("application_builder.career_translation_workspace")
                    + "#initial-resume"
                ),
                models=models,
            )
        except (ValueError, WorkflowConflictError) as exc:
            translation_warning = str(exc)
    elif not translation_warning:
        roles_synced = _sync_baseline_roles_to_evidence_library(current)

    # A revision-specific redirect also prevents a browser or intermediary
    # from presenting the pre-import preview after a successful replacement.
    redirect_target = (
        url_for(
            "application_builder.career_translation_workspace",
            baseline_revision=source_fingerprint[:12],
        )
        + "#initial-resume"
    )
    if translation_warning:
        flash(
            "The resume was imported successfully, but its target-language generation could not be queued. "
            f"Details: {translation_warning}",
            "warning",
        )
    elif translation_job is not None:
        flash(
            "The resume was imported successfully. Its target-language Baseline Resume is generating in the background; you can leave this page and return later.",
            "success",
        )
    else:
        destination = (
            f" for {translated_choice.country}"
            if translated_choice.country
            else (
                " for your Baseline Resume"
                if is_career_translation
                else " for this application"
            )
        )
        source_language = _source_resume_language_code(current)
        no_translation_needed = bool(
            source_language and source_language == translated_choice.code
        )
        preserved_message = (
            "The verified original was preserved. New application workspaces can reuse this baseline."
            if is_career_translation
            else "The verified original was preserved and previous analysis results were cleared."
        )
        if manual_profile_to_merge is not None:
            import_summary = (
                "The resume was imported and merged with the manually entered Baseline Resume for review"
            )
        else:
            import_summary = (
                "The new resume replaced the existing Baseline Resume"
                if replacing_existing_baseline
                else "The resume was imported as the Baseline Resume"
            )
        if no_translation_needed:
            flash(
                f"{import_summary} in {translated_choice.name}. It already matches the Baseline Resume language, so no translation was needed. "
                + preserved_message,
                "success",
            )
        else:
            flash(
                f"{import_summary} and translated into {translated_choice.name}{destination}. "
                + preserved_message,
                "success",
            )
        if import_adjustments:
            flash(
                "The new resume was accepted. A small amount of extractor-generated wording that was not explicit in the uploaded file was omitted. Review the extracted summary, skills, education, and employment fields before using the baseline.",
                "warning",
            )
        if not roles_synced:
            flash(
                "The Baseline Resume was created, but its employment roles could not be synchronized to Career Evidence Library. Regenerate the Baseline Resume to retry.",
                "warning",
            )
    return redirect(redirect_target)


@_routes.post('/profile/default')
def restore_default_profile():
    if getattr(g, "active_application", None) is not None:
        flash(
            "Application Baseline is managed in Foundation. Clear or replace the Baseline Resume there instead.",
            "info",
        )
        return redirect(
            url_for("application_builder.career_translation_workspace")
        )
    current = state()
    previous_source_key = current.source_resume_key
    current.source_profile = _empty_candidate_profile()
    current.original_source_profile = None
    current.baseline_creation_method = ""
    current.manual_source_profile = None
    current.source_resume_language = ""
    current.source_profile_language = ""
    current.source_profile_translation_fingerprint = ""
    current.profile_upload_name = ""
    current.source_resume_key = ""
    current.source_resume_fingerprint = ""
    current.source_resume_contact_links_fingerprint = ""
    current.foundation_baseline_fingerprint = ""
    active_application = getattr(g, "active_application", None)
    if active_application is not None:
        application_store.update_builder_progress(
            str(getattr(g, "application_owner_id", "") or ""),
            active_application.id,
            workflow_step=active_application.workflow_step,
            original_resume_key="",
        )
    current.clear_results()
    if previous_source_key:
        document_store.delete(previous_source_key)
    flash("Verified Resume Evidence cleared. No sample candidate data is loaded.", "success")
    return redirect(url_for("application_builder.index", tab="configuration"))


@_routes.post('/reset')
def reset_workflow():
    current = state()
    current.clear_results()
    flash("Workflow results were reset. Your configuration and current inputs were preserved.", "success")
    return redirect(url_for("application_builder.index", tab="configuration"))


@_routes.post('/applications/<application_id>/baseline/refresh')
def refresh_application_baseline(application_id: str):
    active_application = getattr(g, "active_application", None)
    if active_application is None or active_application.id != application_id:
        abort(404)

    current = state()
    previous_source_key = current.source_resume_key
    status = _sync_application_from_foundation(
        _application_owner_id(), current, force=True
    )
    if status == "missing":
        flash(
            "Create the Foundation Baseline Resume before refreshing this application.",
            "error",
        )
        return redirect(
            url_for("application_builder.career_translation_workspace")
        )

    application_store.update_builder_progress(
        _application_owner_id(),
        active_application.id,
        workflow_step="setup",
        original_resume_key="",
    )
    if previous_source_key:
        document_store.delete(previous_source_key)
    flash(
        "Application Baseline refreshed from the current Foundation Baseline Resume. Previous tailoring results were cleared.",
        "success",
    )
    return redirect(
        url_for(
            "application_builder.index",
            tab="tailoring",
            stage="setup",
            application_id=active_application.id,
        )
        + "#resume-import"
    )


_EXPORT_NAMES = (
    'upload_profile',
    'restore_default_profile',
    'reset_workflow',
    'refresh_application_baseline',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
