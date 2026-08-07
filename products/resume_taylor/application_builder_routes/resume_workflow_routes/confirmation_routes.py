from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Evidence confirmation and Career Evidence Library controllers."""

_routes = DeferredRouteRegistry()

@_routes.post('/confirmation/apply')
def apply_confirmation():
    current = state()
    if current.analysis is None or current.provisional_proposal is None:
        flash("Analyze the job and resume before confirming relevant experience.", "error")
        return redirect(url_for("application_builder.index", tab="tailoring", stage="initial"))

    redirect_stage = "draft"
    redirect_anchor = "#tailored-resume"
    try:
        models = resolve_models(current)
        if current.analyzed_input_fingerprint != input_fingerprint(current, models):
            raise ValueError(
                "The job description changed. Return to Application and Job Setup and select Start tailoring again."
            )

        questions = current.provisional_proposal.candidate_questions
        answers, draft = collect_candidate_answers(questions, request.form)
        # Library persistence is an explicit, separate Step 2 action. Older
        # workflow states may still contain this legacy flag from the removed
        # checkbox/button behavior, so clear it rather than saving implicitly
        # while creating the tailored resume.
        current.save_confirmation_to_library = False
        current.confirmation_draft = draft
        errors = validate_candidate_answers(questions, answers)
        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="confirmation")
                + "#confirmation"
            )

        base_profile = current.confirmed_profile or current.source_profile
        confirmed_profile = (
            build_profile_with_candidate_answers(
                base_profile,
                current.analysis,
                questions,
                answers,
            )
            if questions
            else base_profile.model_copy(deep=True)
        )
        all_answers = _merge_candidate_answers(current.candidate_answers, answers)

        proposal_for_refinement = current.provisional_proposal.model_copy(deep=True)
        proposal_for_refinement.candidate_questions = []

        # Creating the tailored resume must remain comfortably below the web
        # gateway timeout. The provisional proposal is already grounded in the
        # Application Baseline and job analysis. Candidate answers become
        # first-class verified evidence in ``confirmed_profile`` above, so apply
        # them locally and let deterministic validation/selection build Step 3.
        #
        # Do not call ``refine_proposal`` or the independent evidence auditor
        # inside this interactive request. Those two sequential model calls were
        # the source of the Target-Market Review HTTP 504 errors.
        refined = apply_final_follow_up_answers_locally(
            confirmed_profile,
            proposal_for_refinement,
            questions,
            answers,
        )
        refined = repair_missing_bullet_proposals(confirmed_profile, refined)
        refined.skills = balance_skill_categories(
            confirmed_profile, current.analysis, refined.skills
        )
        refined = ensure_confirmed_answers_visible(confirmed_profile, refined)
        refined.candidate_questions = []
        refined = ensure_career_translation_assessment(
            confirmed_profile,
            current.analysis,
            refined,
            _effective_career_background(current),
        )
        refined = _apply_confirmed_title_interpretations(
            str(getattr(g, "application_owner_id", "") or ""),
            confirmed_profile,
            refined,
        )
        refined, _ = apply_all_until_valid(
            confirmed_profile, current.analysis, refined
        )

        current.confirmed_profile = confirmed_profile
        current.candidate_answers = all_answers
        current.save_confirmed_profile = False
        current.workflow_stage = "draft"
        current.quality_review_started = False
        current.provisional_proposal = refined.model_copy(deep=True)
        current.draft_proposal = None
        current.previous_draft_proposal = None
        current.draft_revision = 0
        current.previous_draft_revision = None
        current.draft_last_change_label = ""
        current.draft_last_changed_at = ""
        current.final_proposal = None
        current.confirmation_draft = {}
        current.clear_draft_report()
        current.clear_final_report()

        # Step 2 now completes in one bounded request. The deterministic
        # selector resolves inclusion and exclusion; the UI does not create a
        # second AI-generated follow-up round before Review Tailored Resume.
        current.provisional_proposal = refined.model_copy(deep=True)
        current.draft_proposal = refined.model_copy(deep=True)
        current.draft_revision = 1
        current.draft_last_change_label = (
            "Tailored resume created from confirmed evidence"
        )
        current.draft_last_changed_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        current.confirmation_complete = True
        current.confirmation_follow_up_count = 0
        capture_workflow_step_snapshot(
            current,
            "confirmation",
            proposal=refined,
            profile=confirmed_profile,
        )
        # Review Tailored Resume opens immediately. Its report is generated
        # automatically after the page becomes interactive.
        current.clear_draft_report()
        flash(
            "Experience confirmation is complete and the Job-Aligned Resume is ready for review. "
            "Its Resume Report is generating automatically without blocking Step 3.",
            "success",
        )
    except (ResumeAIError, ValueError) as exc:
        flash(str(exc), "error")
        redirect_stage = "confirmation"
        redirect_anchor = "#confirmation"

    return redirect(
        url_for("application_builder.index", tab="tailoring", stage=redirect_stage) + redirect_anchor
    )


@_routes.post('/confirmation/save-to-library')
def save_confirmation_to_library():
    """Persist Step 2 answers without creating or changing the tailored resume."""
    current = state()
    submitted_answers = any(
        key.startswith(("choice__", "answer__", "experience__", "placement__"))
        for key in request.form
    )

    answers_to_save = [
        answer.model_copy(deep=True) for answer in current.candidate_answers
    ]
    if submitted_answers:
        if current.analysis is None or current.provisional_proposal is None:
            flash(
                "Analyze the job and resume before saving confirmation answers.",
                "error",
            )
            return redirect(
                url_for(
                    "application_builder.index",
                    tab="tailoring",
                    stage="initial",
                )
            )

        questions = current.provisional_proposal.candidate_questions
        submitted, draft = collect_candidate_answers(questions, request.form)
        current.confirmation_draft = draft
        errors = validate_candidate_answers(questions, submitted)
        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(
                url_for(
                    "application_builder.index",
                    tab="tailoring",
                    stage="confirmation",
                )
                + "#confirmation"
            )
        answers_to_save = _merge_candidate_answers(
            current.candidate_answers,
            submitted,
        )
        # Keep the entered answers available on this application, but do not
        # complete Step 2 or generate a proposal. The primary action remains
        # the only path that advances to Review Tailored Resume.
        current.candidate_answers = [
            answer.model_copy(deep=True) for answer in answers_to_save
        ]

    if not answers_to_save:
        flash("There are no confirmation answers to save yet.", "warning")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="confirmation")
            + "#confirmation"
        )

    try:
        saved_count = _save_confirmation_answers_to_library(
            str(getattr(g, "application_owner_id", "") or ""),
            current,
            answers_to_save,
        )
        # Preserve the field for backward-compatible workflow serialization,
        # but never use it to trigger an implicit save during resume creation.
        current.save_confirmation_to_library = False
        current.saved_library_evidence_count = max(
            current.saved_library_evidence_count, saved_count
        )
        flash(
            f"{saved_count} confirmation answer{'s were' if saved_count != 1 else ' was'} saved to Career Evidence Library. You remain in Confirm Relevant Experience.",
            "success",
        )
    except Exception as exc:
        current_app.logger.exception(
            "Could not save confirmation answers to Career Evidence Library"
        )
        flash(
            "The confirmation answers could not be saved to Career Evidence Library: "
            + str(exc),
            "error",
        )
    return redirect(
        url_for("application_builder.index", tab="tailoring", stage="confirmation")
        + "#confirmation"
    )


@_routes.post('/confirmation/reopen')
def reopen_confirmation():
    current = state()
    if current.provisional_proposal is not None:
        confirmation_snapshot = current.workflow_step_snapshots.get("confirmation")
        saved_answers = (
            confirmation_snapshot.candidate_answers
            if confirmation_snapshot is not None
            else current.candidate_answers
        )
        restored_draft: dict[str, str] = {}
        for answer in saved_answers:
            if answer.yes_no is True:
                choice = "yes"
            elif answer.yes_no is False:
                choice = "no"
            else:
                choice = "yes" if answer.text.strip() else ""
            restored_draft[f"choice__{answer.question_id}"] = choice
            restored_draft[f"answer__{answer.question_id}"] = answer.text
            restored_draft[f"experience__{answer.question_id}"] = (
                answer.experience_id
            )
            restored_draft[f"placement__{answer.question_id}"] = answer.placement

        reopened = current.provisional_proposal.model_copy(deep=True)
        current.workflow_stage = "draft"
        current.quality_review_started = False
        current.provisional_proposal = reopened
        current.draft_proposal = None
        current.previous_draft_proposal = None
        current.draft_revision = 0
        current.previous_draft_revision = None
        current.draft_last_change_label = ""
        current.draft_last_changed_at = ""
        current.final_proposal = None
        current.confirmation_complete = False
        current.candidate_answers = []
        current.confirmed_profile = None
        current.confirmation_draft = restored_draft
        current.confirmation_follow_up_round = 0
        current.confirmation_follow_up_count = 0
        discard_workflow_step_snapshots_after(
            current, "confirmation", include_stage=True
        )
        current.clear_draft_report()
        current.clear_final_report()
        flash("Confirmation questions reopened. Confirm the answers again to create a new tailored resume.", "success")
    return redirect(
        url_for("application_builder.index", tab="tailoring", stage="confirmation")
        + "#confirmation"
    )


_EXPORT_NAMES = (
    'apply_confirmation',
    'save_confirmation_to_library',
    'reopen_confirmation',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
