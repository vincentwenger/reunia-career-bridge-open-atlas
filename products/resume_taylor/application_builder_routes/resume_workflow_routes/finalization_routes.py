from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Workflow reopening, style selection, finalization, and version-save controllers."""

_routes = DeferredRouteRegistry()

@_routes.post('/workflow/reopen/<stage>')
def reopen_workflow_stage(stage: str):
    """Restore the completed tailored-resume snapshot and invalidate later outputs."""
    internal_stage = "draft" if stage in {"draft", "review"} else stage
    if internal_stage != "draft":
        abort(404)
    current = state()
    snapshot = current.workflow_step_snapshots.get("draft")
    if snapshot is None or snapshot.proposal is None:
        flash("The saved Job-Aligned Resume is no longer available.", "warning")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage=guided_stage_for_state(current))
        )

    restored = snapshot.proposal.model_copy(deep=True)
    current.workflow_stage = "draft"
    current.quality_review_started = False
    current.draft_proposal = restored
    current.final_proposal = None
    current.confirmed_profile = (
        snapshot.profile.model_copy(deep=True)
        if snapshot.profile is not None
        else (current.confirmed_profile or current.source_profile).model_copy(deep=True)
    )
    current.candidate_answers = [
        answer.model_copy(deep=True) for answer in snapshot.candidate_answers
    ]
    current.draft_revision = max(1, snapshot.draft_revision or 1)
    current.previous_draft_proposal = None
    current.previous_draft_revision = None
    current.draft_last_change_label = snapshot.change_label
    current.draft_last_changed_at = snapshot.changed_at
    discard_workflow_step_snapshots_after(current, "draft", include_stage=True)
    current.clear_draft_report()
    current.clear_final_report()
    flash(
        "Review Tailored Resume was reopened. Quality, finalization, and export results were cleared; the Tailored Resume Report will refresh automatically.",
        "warning",
    )
    return redirect(
        url_for("application_builder.index", tab="tailoring", stage="review") + "#tailored-resume"
    )


@_routes.post('/resume-style')
def select_resume_style():
    current = state()
    before_preferences = (
        normalize_career_stage(current.resume_career_stage),
        normalize_resume_format(current.resume_format),
        normalize_visual_design(current.resume_visual_design),
    )
    changed_dimensions: list[str] = []
    supplied_dimension = False

    if "career_stage" in request.form:
        supplied_dimension = True
        raw_stage = request.form.get("career_stage", "").strip().casefold()
        allowed = {option["key"] for option in career_stage_options()}
        if raw_stage not in allowed:
            flash("Choose one of the available career stages.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="final")
                + "#resume-style-selector"
            )
        if normalize_career_stage(current.resume_career_stage) != raw_stage:
            changed_dimensions.append("career stage")
        current.resume_career_stage = raw_stage
        current.resume_career_stage_explicit = True

    if "resume_format" in request.form:
        supplied_dimension = True
        raw_format = request.form.get("resume_format", "").strip().casefold()
        allowed = {option["key"] for option in resume_format_options()}
        if raw_format not in allowed:
            flash("Choose one of the available resume formats.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="final")
                + "#resume-style-selector"
            )
        if normalize_resume_format(current.resume_format) != raw_format:
            changed_dimensions.append("resume format")
        current.resume_format = raw_format
        current.resume_format_explicit = True

    if "visual_design" in request.form:
        supplied_dimension = True
        raw_design = request.form.get("visual_design", "").strip().casefold()
        allowed = {option["key"] for option in visual_design_options()}
        if raw_design not in allowed:
            flash("Choose one of the available visual designs.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="final")
                + "#resume-style-selector"
            )
        if normalize_visual_design(current.resume_visual_design) != raw_design:
            changed_dimensions.append("visual design")
        current.resume_visual_design = raw_design
        current.resume_visual_design_explicit = True

    if "resume_style" in request.form and not supplied_dimension:
        supplied_dimension = True
        raw_legacy = request.form.get("resume_style", "").strip().casefold()
        if raw_legacy not in RESUME_STYLE_THEMES:
            flash("Choose one of the available career stages.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="final")
                + "#resume-style-selector"
            )
        current.resume_career_stage = normalize_career_stage(raw_legacy)
        current.resume_career_stage_explicit = True

    if not supplied_dimension:
        flash("Choose a career stage, resume format, or visual design.", "error")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="final")
            + "#resume-style-selector"
        )

    # Recompute dependent recommendations (for example Technical → Modern)
    # only when the user has not explicitly chosen that dimension.
    ensure_recommended_resume_style(current)
    after_preferences = (
        normalize_career_stage(current.resume_career_stage),
        normalize_resume_format(current.resume_format),
        normalize_visual_design(current.resume_visual_design),
    )
    dimension_names = ("career stage", "resume format", "visual design")
    changed_dimensions = [
        name
        for name, before, after in zip(
            dimension_names, before_preferences, after_preferences
        )
        if before != after
    ]
    preference_label = current_resume_preference_label(current)

    if changed_dimensions and current.analysis is not None:
        try:
            if current.final_proposal is not None:
                _invalidate_final_report_and_exports(current)
                job, created = _queue_export_job(refresh_all_reports=True)
                changed_label = ", ".join(changed_dimensions)
                flash(
                    (
                        f"Updated {changed_label}: {preference_label}. "
                        + job.message
                    )
                    if created
                    else f"Updated {changed_label}: {preference_label}. Export generation is already running.",
                    "success" if created else "info",
                )
            else:
                flash(f"Resume preferences updated: {preference_label}.", "success")
        except (ValueError, WorkflowConflictError) as exc:
            flash(
                f"{preference_label} was selected, but export generation could not be queued: {exc}",
                "warning",
            )
    elif changed_dimensions:
        flash(f"Resume preferences updated: {preference_label}.", "success")
    else:
        flash(f"{preference_label} is already selected.", "info")

    target_stage = (
        "final"
        if current.workflow_stage == "final"
        else guided_stage_for_state(current)
    )
    return redirect(
        url_for("application_builder.index", tab="tailoring", stage=target_stage)
        + ("#resume-style-selector" if target_stage == "final" else "")
    )


@_routes.post('/workflow/start-final')
def start_final_stage():
    """Queue score-guarded optimization, evidence review, reports, and exports."""
    current = state()
    job_aligned = current.draft_proposal
    working = (
        current.final_proposal
        if current.workflow_stage == "final" and current.final_proposal is not None
        else job_aligned
    )
    if (
        current.analysis is None
        or job_aligned is None
        or working is None
        or not current.confirmation_complete
    ):
        flash("Complete the Job-Aligned Resume before running final optimization.", "error")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="draft")
            + "#tailored-resume"
        )
    try:
        if current.workflow_stage != "final":
            current.final_resume_title = current.analysis.target_title
        models = resolve_models(current)
        if current.analyzed_input_fingerprint != input_fingerprint(current, models):
            raise ValueError(
                "The job description or tailoring model changed. Return to Application and Job Setup and select Start tailoring again."
            )
        profile = current.confirmed_profile or current.source_profile
        ensure_recommended_resume_style(current)
        if "professional_summary" in request.form:
            working = proposal_from_form(working, request.form, profile)
        working = repair_missing_bullet_proposals(profile, working)
        working, _ = apply_all_until_valid(profile, current.analysis, working)
        working.candidate_questions = []
        # Persist the exact reviewed input that the worker will optimize. The
        # workflow stage remains unchanged until the background job succeeds.
        current.final_proposal = working.model_copy(deep=True)
        current.optimization_status = "queued"
        current.optimization_notice = (
            "Final optimization and evidence review are running in the background."
        )
        job, created = _queue_current_resume_job(
            job_type=AsyncJobType.RESUME_FINAL_OPTIMIZATION,
            operation="final_optimization",
            total_count=2,
            message=(
                "Final optimization, evidence review, reports, and Word/PDF export were queued. "
                "You can leave this page and return later."
            ),
            result_url=(
                url_for(
                    "application_builder.index",
                    tab="tailoring",
                    stage="final",
                )
                + "#final-review"
            ),
            models=models,
            extra_payload={
                "proposal_fingerprint": _proposal_fingerprint(working),
            },
        )
        flash(
            job.message
            if created
            else "Final Resume processing is already running. Its saved progress has been reopened.",
            "success" if created else "info",
        )
    except (ResumeAIError, TemplateError, ValueError, WorkflowConflictError) as exc:
        flash(str(exc), "error")
    return redirect(
        url_for("application_builder.index", tab="tailoring", stage="draft")
        + "#tailored-resume"
    )


@_routes.post('/resume/save/<version>')
def save_resume_version(version: str):
    if version not in {"draft", "final"}:
        abort(404)
    current = state()
    anchor = "#tailored-resume" if version == "draft" else "#final-resume-editor"
    if current.analysis is None or not current.confirmation_complete:
        flash("Complete the tailoring workflow before editing this resume.", "error")
        return redirect(url_for("application_builder.index", tab="tailoring", stage=version) + anchor)
    if version != current.workflow_stage:
        flash(f"The {version.title()} resume is view-only at this stage.", "warning")
        return redirect(url_for("application_builder.index", tab="tailoring", stage=version) + anchor)

    base = current.draft_proposal if version == "draft" else current.final_proposal
    if base is None:
        flash(f"The {version.title()} resume has not been created yet.", "error")
        return redirect(url_for("application_builder.index", tab="tailoring", stage=version) + anchor)

    changed = False
    try:
        models = resolve_models(current)
        if current.analyzed_input_fingerprint != input_fingerprint(current, models):
            raise ValueError(
                "The job description or tailoring model changed. Return to Application and Job Setup and start tailoring again."
            )
        profile = current.confirmed_profile or current.source_profile
        edited = proposal_from_form(base, request.form, profile)
        proposal_changed = _proposal_json(edited) != _proposal_json(base)
        profile_changed = False
        title_changed = False
        if version == "final":
            edited_profile = profile_with_education_from_form(profile, request.form)
            edited_title = normalize_target_title(
                request.form.get("target_title", effective_final_resume_title(current))
            )
            if not edited_title:
                raise ValueError("The final resume job title cannot be blank.")
            profile_changed = (
                edited_profile.model_dump(mode="json")
                != profile.model_dump(mode="json")
            )
            title_changed = edited_title != effective_final_resume_title(current)
            current.confirmed_profile = edited_profile
            current.final_resume_title = edited_title
            profile = edited_profile
        changed = proposal_changed or profile_changed or title_changed
        store_working_proposal(
            current,
            edited,
            invalidate=changed,
            previous_proposal=base,
            change_label=f"Manual {version.title()} edits",
        )
        export_job = None
        export_created = False
        if changed and version == "final":
            export_job, export_created = _queue_export_job(refresh_all_reports=False)

        if not changed:
            flash(f"No changes were made to the {version.title()} resume.", "info")
        elif version == "final":
            flash(
                "Final Resume changes were saved. "
                + (
                    export_job.message
                    if export_job is not None and export_created
                    else "Word/PDF export generation is already running."
                ),
                "success" if export_created else "info",
            )
        else:
            flash(
                "Job-Aligned Resume changes saved. Its report will refresh automatically.",
                "success",
            )
    except (TemplateError, ValueError) as exc:
        flash(str(exc), "error")

    redirect_args = {"tab": "tailoring", "stage": version}
    if version == "draft" and changed:
        redirect_args["compare"] = "previous"
    return redirect(url_for("application_builder.index", **redirect_args) + anchor)


_EXPORT_NAMES = (
    'reopen_workflow_stage',
    'select_resume_style',
    'start_final_stage',
    'save_resume_version',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
