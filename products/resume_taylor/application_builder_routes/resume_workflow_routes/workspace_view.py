from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Resume Workflow page view-model and template rendering."""

_routes = DeferredRouteRegistry()

def render_resume_workflow_index():
    active_tab = request.args.get("tab", "applications")
    if active_tab not in {"tailoring", "reports", "applications", "configuration"}:
        active_tab = "applications"
    if active_tab == "configuration" and not bool(session.get("is_admin")):
        abort(403, description="Administrator access is required for AI configuration.")

    current = state()
    ensure_recommended_resume_style(current)

    if active_tab == "applications":
        owner_id = g.application_owner_id
        applications, readiness_by_application = (
            _applications_with_calculated_readiness(
                application_store.list_for_owner(owner_id)
            )
        )
        style_options = resume_style_options()
        return render_template(
            "application_builder/applications.html",
            active_tab=active_tab,
            applications=applications,
            readiness_by_application=readiness_by_application,
            application_metrics=build_application_metrics(applications),
            application_status_options=APPLICATION_STATUS_OPTIONS,
            resume_version_options=RESUME_VERSION_OPTIONS,
            upcoming_event_type_options=UPCOMING_EVENT_TYPE_OPTIONS,
            interview_audience_suggestions=INTERVIEW_AUDIENCE_SUGGESTIONS,
            resume_style_options=style_options,
            resume_style_labels={
                option["key"]: f'{option["label"]} — {option["audience"]}'
                for option in style_options
            },
        )

    active_guided_stage = guided_stage_for_state(current)
    selected_workflow_stage = normalize_workflow_step(
        request.args.get("stage"), fallback=active_guided_stage
    )
    selected_workflow_panel = WORKFLOW_PANEL_BY_STEP[selected_workflow_stage]

    report_view_name = request.args.get("report", "initial")
    if report_view_name not in {"initial", "draft", "final", "comparison"}:
        report_view_name = "initial"

    try:
        models = resolve_models(current)
        model_error = ""
    except ValueError as exc:
        models = ActiveModels("", "", None, None)
        model_error = str(exc)

    current_input = input_fingerprint(current, models)
    source_profile = current.source_profile
    application_baseline_frozen = _application_baseline_is_frozen(current)
    application_baseline_status = str(
        getattr(g, "application_baseline_status", "") or ""
    )
    application_baseline_outdated = bool(
        application_baseline_frozen
        and application_baseline_status == "frozen"
    )
    profile = current.confirmed_profile or source_profile
    analysis = current.analysis
    proposal = working_proposal_for_stage(current)
    if analysis is not None and proposal is not None:
        proposal = _apply_confirmed_title_interpretations(
            str(getattr(g, "application_owner_id", "") or ""),
            profile,
            proposal,
        )
    draft_proposal = current.draft_proposal
    final_proposal = current.final_proposal
    input_is_current = bool(
        analysis
        and proposal
        and current.analyzed_input_fingerprint == current_input
    )
    analysis_is_current = bool(
        analysis and current.analysis_input_fingerprint == current_input
    )

    initial_report_is_current = bool(
        current.initial_report
        and current.initial_report_input_fingerprint
        == initial_report_fingerprint(current)
    )
    initial_report_data = None
    if (
        initial_report_is_current
        and current.initial_report
        and current.initial_report_analysis
        and current.initial_report_proposal
    ):
        initial_report_data = report_view(
            current.initial_report,
            profile=source_profile,
            analysis=current.initial_report_analysis,
            proposal=current.initial_report_proposal,
            candidate_answers=None,
        )

    initial_editor_proposal = build_initial_resume_proposal(
        source_profile, current.initial_evidence_proposal
    )
    initial_editor_data = proposal_view_data(
        source_profile,
        analysis,
        initial_editor_proposal,
        current_title=initial_resume_title(source_profile),
    )

    setup_snapshot = current.workflow_step_snapshots.get("initial")
    confirmation_snapshot = current.workflow_step_snapshots.get("confirmation")
    alignment_snapshot = current.workflow_step_snapshots.get("draft")
    edit_setup_snapshot = bool(
        selected_workflow_stage == "setup"
        and current.workflow_stage != "initial"
        and request.args.get("edit") == "setup"
    )

    job_aligned_proposal = (
        alignment_snapshot.proposal
        if current.workflow_stage == "final"
        and alignment_snapshot is not None
        and alignment_snapshot.proposal is not None
        else draft_proposal
    )
    job_aligned_profile = (
        alignment_snapshot.profile
        if current.workflow_stage == "final"
        and alignment_snapshot is not None
        and alignment_snapshot.profile is not None
        else profile
    )

    draft_fingerprint = _proposal_fingerprint(draft_proposal)
    draft_report_is_current = bool(
        current.updated_report
        and current.updated_report_input_fingerprint == current_input
        and current.updated_report_proposal_fingerprint == draft_fingerprint
    )
    draft_report_data = None
    if draft_report_is_current and current.updated_report and draft_proposal:
        draft_report_data = report_view(
            current.updated_report,
            profile=profile,
            analysis=analysis,
            proposal=draft_proposal,
            candidate_answers=current.candidate_answers,
        )

    tailoring_changes = None
    if analysis and job_aligned_proposal and current.confirmation_complete:
        tailoring_changes = summarize_tailoring_changes(
            initial_editor_proposal,
            job_aligned_proposal,
            job_aligned_profile,
            analysis,
            reference_title=initial_resume_title(source_profile),
            current_title=analysis.target_title,
        )
        report_impacts = (
            tailoring_report_impacts(
                current.initial_report, current.updated_report, analysis
            )
            if initial_report_is_current
            and current.initial_report
            and draft_report_is_current
            and current.updated_report
            else {
                "available": False,
                "title": None,
                "summary": None,
                "skills": {},
                "requirements": {},
            }
        )
        tailoring_changes["report_impacts"] = report_impacts
        if report_impacts.get("available"):
            bullet_impacts = attributable_bullet_report_impacts(
                initial_editor_proposal,
                job_aligned_proposal,
                analysis,
                report_impacts.get("requirements", {}),
                set(tailoring_changes["bullet_details"]),
            )
            for source_id, detail in tailoring_changes["bullet_details"].items():
                detail["report_impact"] = bullet_impacts.get(source_id)

    tailor_stage_editor_data = (
        proposal_view_data(
            job_aligned_profile,
            analysis,
            job_aligned_proposal,
            comparison_proposal=initial_editor_proposal,
            comparison_label=INITIAL_RESUME_LABEL,
            current_label=JOB_ALIGNED_RESUME_LABEL,
            comparison_title=initial_resume_title(source_profile),
            current_title=analysis.target_title,
            bullet_tailoring_details=(
                tailoring_changes["bullet_details"]
                if tailoring_changes
                else {}
            ),
        )
        if analysis and job_aligned_proposal and current.confirmation_complete
        else None
    )
    if (
        tailor_stage_editor_data is not None
        and current.workflow_stage == "final"
        and alignment_snapshot is not None
    ):
        tailor_stage_editor_data["workflow_snapshot"] = True
        tailor_stage_editor_data["snapshot_stage"] = "draft"
        tailor_stage_editor_data["snapshot_label"] = (
            "Step 3 · Review Tailored Resume"
        )
        tailor_stage_editor_data["snapshot_captured_at"] = (
            alignment_snapshot.captured_at
        )

    final_reference_proposal = job_aligned_proposal or draft_proposal
    final_comparison_proposal = (
        final_reference_proposal
        if final_reference_proposal is not None
        and final_proposal is not None
        and _proposal_json(final_reference_proposal)
        != _proposal_json(final_proposal)
        else None
    )
    final_resume_title = effective_final_resume_title(current)
    final_editor_data = (
        proposal_view_data(
            profile,
            analysis,
            final_proposal,
            comparison_proposal=final_comparison_proposal,
            comparison_label=JOB_ALIGNED_RESUME_LABEL,
            current_label=FINAL_RESUME_LABEL,
            comparison_title=analysis.target_title,
            current_title=final_resume_title,
            include_comparison_reasons=True,
        )
        if analysis and final_proposal and current.confirmation_complete
        else None
    )

    deterministic_issues = (
        validate_proposal(profile, analysis, proposal)
        if analysis and proposal
        else []
    )
    proposal_data = (
        proposal_view_data(profile, analysis, proposal)
        if analysis and proposal
        else None
    )

    final_fingerprint = _proposal_fingerprint(final_proposal)
    final_report_is_current = bool(
        current.final_report
        and current.final_report_input_fingerprint == current_input
        and current.final_report_proposal_fingerprint == final_fingerprint
    )
    final_report_data = None
    if (
        current.final_report
        and current.final_report_proposal
        and current.final_report_profile
        and analysis
    ):
        final_report_data = report_view(
            current.final_report,
            profile=current.final_report_profile,
            analysis=analysis,
            proposal=current.final_report_proposal,
            candidate_answers=current.final_report_candidate_answers,
        )

    comparisons_data: dict[str, Any] = {}
    if initial_report_is_current and current.initial_report:
        if draft_report_is_current and current.updated_report:
            comparisons_data["initial_draft"] = comparison_view(
                current.initial_report,
                current.updated_report,
                initial_label=INITIAL_RESUME_LABEL,
                updated_label=JOB_ALIGNED_RESUME_LABEL,
            )
        if current.final_report:
            comparisons_data["initial_final"] = comparison_view(
                current.initial_report,
                current.final_report,
                initial_label=INITIAL_RESUME_LABEL,
                updated_label=FINAL_RESUME_LABEL,
            )
    if draft_report_is_current and current.updated_report and current.final_report:
        comparisons_data["draft_final"] = comparison_view(
            current.updated_report,
            current.final_report,
            initial_label=JOB_ALIGNED_RESUME_LABEL,
            updated_label=FINAL_RESUME_LABEL,
        )

    requirement_lookup = (
        {item.id: item for item in analysis.requirements} if analysis else {}
    )
    bullet_experience_lookup = {
        bullet.id: experience.id
        for experience in profile.experiences
        for bullet in experience.bullets
    }
    confirmation_rows = []
    if proposal:
        ordered_questions = order_candidate_questions_for_display(
            proposal.candidate_questions
        )
        for display_position, question in enumerate(ordered_questions, start=1):
            confirmation_rows.append(
                {
                    "question": question,
                    "display_id": candidate_question_display_label(
                        question, display_position
                    ),
                    "requirement": requirement_lookup.get(
                        question.requirement_id
                    ),
                    "choice": current.confirmation_draft.get(
                        f"choice__{question.id}", ""
                    ),
                    "answer": current.confirmation_draft.get(
                        f"answer__{question.id}", ""
                    ),
                    "experience_id": current.confirmation_draft.get(
                        f"experience__{question.id}",
                        bullet_experience_lookup.get(question.source_id, ""),
                    ),
                    "placement": current.confirmation_draft.get(
                        f"placement__{question.id}",
                        "update_existing"
                        if question.id.startswith("FQ")
                        else "auto",
                    ),
                }
            )

    confirmation_display_answers = (
        confirmation_snapshot.candidate_answers
        if confirmation_snapshot is not None
        else current.candidate_answers
    )
    confirmation_display_profile = (
        confirmation_snapshot.profile
        if confirmation_snapshot is not None
        and confirmation_snapshot.profile is not None
        else current.confirmed_profile
    )
    confirmation_display_proposal = (
        confirmation_snapshot.proposal
        if confirmation_snapshot is not None
        and confirmation_snapshot.proposal is not None
        else proposal
    )
    confirmed_answer_dispositions = confirmation_dispositions(
        confirmation_display_profile,
        confirmation_display_proposal,
        confirmation_display_answers,
    )

    api_ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    ai_ready = api_ready and bool(models.analysis_tailoring_model and models.evidence_review_model)
    blocking_local = any(
        issue.severity == "blocking" for issue in deterministic_issues
    )
    guided_workflow = build_guided_workflow(
        workflow_stage=current.workflow_stage,
        input_is_current=input_is_current,
        confirmation_complete=current.confirmation_complete,
        blocking_local=blocking_local,
        resume_ready=bool(current.final_resume_bytes),
        quality_review_started=getattr(
            current, "quality_review_started", False
        ),
        final_proposal_ready=current.final_proposal is not None,
        application_id=(g.active_application.id if g.active_application else ""),
    )

    if g.active_application is not None:
        if current.final_resume_bytes:
            dashboard_resume_version = FINAL_RESUME_LABEL
        elif current.draft_proposal is not None:
            dashboard_resume_version = (
                f"Tailored Resume v{max(1, current.draft_revision)}"
            )
        else:
            dashboard_resume_version = INITIAL_RESUME_LABEL
        dashboard_status = g.active_application.status
        if (
            dashboard_status in {"draft", "considering"}
            and guided_workflow["current_key"] != "setup"
        ):
            dashboard_status = "preparing"
        g.active_application = application_store.update_builder_progress(
            g.application_owner_id,
            g.active_application.id,
            workflow_step=guided_workflow["current_key"],
            resume_version=dashboard_resume_version,
            company=(
                current.analysis.target_company
                if current.analysis is not None
                else g.active_application.company
            ),
            role=current.target_title or g.active_application.role,
            job_description=current.job_description,
            status=dashboard_status,
        )
        if g.active_application is not None:
            _persist_resume_findings(
                g.active_application.id,
                build_resume_findings_snapshot(
                    current,
                    company=g.active_application.company,
                    role=g.active_application.role,
                    job_description=g.active_application.job_description,
                ),
            )

    initial_editor_filename = (
        safe_filename(f"{source_profile.name}_Initial_Resume") + ".docx"
    )
    job_aligned_editor_filename = (
        safe_filename(f"{profile.name}_Job_Aligned_Resume") + ".docx"
    )
    final_editor_filename = final_resume_filename(
        profile, final_resume_title, "docx"
    )
    application_records = application_store.list_for_owner(
        g.application_owner_id
    )
    preliminary_fit = (
        preliminary_application_fit(current, application_records)
        if input_is_current
        else None
    )
    application_fit = (
        current_application_fit(current, application_records)
        if input_is_current
        else None
    )
    career_translation_assessment = career_translation_assessment_view(
        proposal
    )
    reusable_profile = getattr(
        g, "reusable_career_profile", ReusableCareerProfile()
    )
    resume_language_choice = _resolved_resume_language(current)
    active_resume_job = _visible_resume_job()
    return render_template(
        "application_builder/index.html",
        state=current,
        active_tab=active_tab,
        resume_async_job=(
            _resume_job_response(active_resume_job)
            if active_resume_job is not None
            else None
        ),
        guided_workflow=guided_workflow,
        preliminary_application_fit=preliminary_fit,
        application_fit=application_fit,
        career_translation_assessment=career_translation_assessment,
        career_background=_effective_career_background(current),
        country_options=COUNTRY_OPTIONS,
        career_background_additions=_career_background_application_additions(
            current.career_background,
            reusable_profile,
        ),
        resume_language_choice=resume_language_choice,
        resume_language_options=resume_language_options(),
        selected_resume_language=current.career_background.resume_language,
        resume_labels=resume_labels(resume_language_choice.code),
        selected_workflow_stage=selected_workflow_stage,
        selected_workflow_panel=selected_workflow_panel,
        edit_setup_snapshot=edit_setup_snapshot,
        setup_snapshot=setup_snapshot,
        confirmation_snapshot=confirmation_snapshot,
        report_view_name=report_view_name,
        models=models,
        model_error=model_error,
        api_ready=api_ready,
        ai_ready=ai_ready,
        source_profile=source_profile,
        application_baseline_frozen=application_baseline_frozen,
        application_baseline_outdated=application_baseline_outdated,
        application_baseline_status=application_baseline_status,
        profile=profile,
        input_is_current=input_is_current,
        analysis_is_current=analysis_is_current,
        initial_report_is_current=initial_report_is_current,
        initial_report_stale=bool(
            current.initial_report and not initial_report_is_current
        ),
        initial_report=initial_report_data,
        analysis=analysis,
        proposal=proposal,
        proposal_data=proposal_data,
        initial_editor_data=initial_editor_data,
        tailor_stage_editor_data=tailor_stage_editor_data,
        tailor_stage_profile=job_aligned_profile,
        tailor_stage_tailoring_changes=tailoring_changes,
        final_editor_data=final_editor_data,
        initial_editor_filename=initial_editor_filename,
        job_aligned_editor_filename=job_aligned_editor_filename,
        final_editor_filename=final_editor_filename,
        final_resume_title=final_resume_title,
        blocking_local=blocking_local,
        confirmation_rows=confirmation_rows,
        confirmation_experiences=source_profile.experiences,
        confirmation_display_answers=confirmation_display_answers,
        confirmed_answer_dispositions=confirmed_answer_dispositions,
        draft_report=draft_report_data,
        draft_report_stale=bool(
            current.updated_report and not draft_report_is_current
        ),
        final_report=final_report_data,
        optimization_summary=final_optimization_summary(
            current.optimization_report_before,
            current.optimization_report_after,
        ),
        optimization_remaining=final_optimization_recommendations(
            current.optimization_report_after or current.final_report
        ),
        career_stage_options=career_stage_options(),
        resume_format_options=resume_format_options(),
        visual_design_options=visual_design_options(),
        selected_career_stage=normalize_career_stage(current.resume_career_stage),
        selected_resume_format=normalize_resume_format(current.resume_format),
        selected_visual_design=normalize_visual_design(current.resume_visual_design),
        recommended_career_stage=recommend_career_stage(
            current.job_description,
            analysis.target_title if analysis is not None else current.target_title,
            candidate_profile=profile,
            candidate_answers=current.candidate_answers,
        ),
        recommended_resume_format=recommend_resume_format(
            current.job_description,
            analysis.target_title if analysis is not None else current.target_title,
            candidate_profile=profile,
            candidate_answers=current.candidate_answers,
        ),
        recommended_visual_design=recommend_visual_design(
            current.job_description,
            analysis.target_title if analysis is not None else current.target_title,
            resume_format=current.resume_format,
            career_stage=current.resume_career_stage,
            candidate_profile=profile,
            candidate_answers=current.candidate_answers,
        ),
        selected_resume_preference_label=current_resume_preference_label(current),
        final_report_stale=bool(
            current.final_report and not final_report_is_current
        ),
        final_report_created_at=current.final_report_created_at,
        comparisons=comparisons_data,
        initial_resume_title=initial_resume_title,
        profile_stats={
            "skills": len(source_profile.skills.all_non_language_skills()),
            "bullets": len(source_profile.bullet_lookup()),
        },
    )


_EXPORT_NAMES = (
    'render_resume_workflow_index',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
