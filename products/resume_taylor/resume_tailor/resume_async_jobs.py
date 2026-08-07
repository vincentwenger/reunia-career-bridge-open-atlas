"""Durable Resume Workflow background-job helpers.

The HTTP layer only validates and persists user input, then creates an AsyncJob.
The external worker loads the canonical WorkflowState, performs one bounded phase,
persists the state with optimistic locking, and advances the durable job cursor.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from career_bridge.async_jobs import AsyncJob, AsyncJobStatus, AsyncJobType


RESUME_ASYNC_JOB_TYPES = {
    AsyncJobType.RESUME_BASELINE_TRANSLATION,
    AsyncJobType.RESUME_TAILORING,
    AsyncJobType.RESUME_REPORT,
    AsyncJobType.RESUME_FINAL_OPTIMIZATION,
    AsyncJobType.RESUME_EXPORT,
}


class ResumeWorkflowJobError(RuntimeError):
    """Raised when a queued job no longer matches the saved workflow."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def resume_job_guard(
    state: Any,
    models: Any | None = None,
    *,
    workflow_input_fingerprint: str = "",
) -> dict[str, str]:
    """Return stable inputs that must not change while a resume job is queued."""

    original_profile = getattr(state, "original_source_profile", None)
    source_profile = original_profile or getattr(state, "source_profile", None)
    if source_profile is not None:
        try:
            source_profile_fingerprint = _sha256_text(
                source_profile.model_dump_json(exclude_none=False)
            )
        except Exception:
            source_profile_fingerprint = _sha256_text(
                getattr(source_profile, "all_source_text", lambda: "")()
            )
    else:
        source_profile_fingerprint = ""
    guard = {
        "source_resume_fingerprint": str(
            getattr(state, "source_resume_fingerprint", "") or source_profile_fingerprint
        ),
        "source_profile_fingerprint": source_profile_fingerprint,
        "job_description_fingerprint": _sha256_text(
            getattr(state, "job_description", "")
        ),
        "target_title": str(getattr(state, "target_title", "") or "").strip(),
        "resume_language": str(
            getattr(getattr(state, "career_background", None), "resume_language", "")
            or ""
        ).strip(),
        "target_country": str(
            getattr(getattr(state, "career_background", None), "target_country", "")
            or ""
        ).strip(),
        "analysis_model": str(
            getattr(models, "analysis_tailoring_model", "") if models is not None else ""
        ).strip(),
        "analysis_effort": str(
            getattr(models, "analysis_tailoring_reasoning_effort", "")
            if models is not None
            else ""
        ).strip(),
        "evidence_model": str(
            getattr(models, "evidence_review_model", "") if models is not None else ""
        ).strip(),
        "evidence_effort": str(
            getattr(models, "evidence_review_reasoning_effort", "")
            if models is not None
            else ""
        ).strip(),
    }
    if workflow_input_fingerprint:
        guard["workflow_input_fingerprint"] = str(workflow_input_fingerprint)
    return guard


def assert_resume_job_guard(
    state: Any,
    expected: Mapping[str, Any],
    models: Any | None = None,
    *,
    workflow_input_fingerprint: str = "",
) -> None:
    actual = resume_job_guard(
        state,
        models,
        workflow_input_fingerprint=workflow_input_fingerprint,
    )
    mismatches = [
        key
        for key, expected_value in dict(expected or {}).items()
        if str(expected_value or "") != str(actual.get(key) or "")
    ]
    if mismatches:
        raise ResumeWorkflowJobError(
            "The resume, job description, target settings, or AI model changed after "
            "this background job was queued. Start the action again with the current inputs."
        )


def is_resume_async_job(job: AsyncJob) -> bool:
    return job.job_type in RESUME_ASYNC_JOB_TYPES


def active_resume_job_for_workflow(
    jobs: list[AsyncJob], workflow_key: str, *, operation: str = ""
) -> AsyncJob | None:
    workflow_key = str(workflow_key or "").strip()
    operation = str(operation or "").strip()
    for job in jobs:
        if not is_resume_async_job(job) or job.status.terminal:
            continue
        if str(job.payload.get("workflow_key") or "") != workflow_key:
            continue
        if operation and str(job.payload.get("operation") or "") != operation:
            continue
        return job
    return None


def resume_job_public_payload(job: AsyncJob, *, url_for: Callable[..., str]) -> dict[str, Any]:
    payload = job.to_public_dict()
    payload.update(
        {
            "ok": job.status not in {AsyncJobStatus.FAILED, AsyncJobStatus.CANCELED},
            "operation": str(job.payload.get("operation") or ""),
            "workflow_key": str(job.payload.get("workflow_key") or ""),
            "application_id": str(job.payload.get("application_id") or ""),
            "result_url": str(job.payload.get("result_url") or ""),
            "status_url": url_for(
                "application_builder.resume_async_job_status", job_id=job.id
            ),
            "cancel_url": url_for(
                "application_builder.cancel_resume_async_job", job_id=job.id
            ),
            "retry_url": url_for(
                "application_builder.retry_resume_async_job", job_id=job.id
            ),
        }
    )
    return payload


def queued_resume_job(
    *,
    owner_id: str,
    job_type: AsyncJobType,
    workflow_key: str,
    operation: str,
    application_id: str,
    guard: Mapping[str, Any],
    result_url: str,
    total_count: int,
    message: str,
    extra_payload: Mapping[str, Any] | None = None,
) -> AsyncJob:
    payload = {
        "workflow_key": str(workflow_key or ""),
        "operation": str(operation or ""),
        "application_id": str(application_id or ""),
        "guard": dict(guard or {}),
        "result_url": str(result_url or ""),
    }
    payload.update(dict(extra_payload or {}))
    return AsyncJob.queued(
        owner_id=owner_id,
        job_type=job_type,
        payload=payload,
        total_count=max(1, int(total_count)),
        message=message,
    )


class ResumeWorkflowAsyncProcessor:
    """Execute Resume Workflow phases against the canonical WorkflowStore."""

    def __init__(
        self,
        *,
        workflow_store: Any,
        document_store: Any,
        application_store: Any | None,
        builder: Any,
        worker_id: str,
    ) -> None:
        self.workflow_store = workflow_store
        self.document_store = document_store
        self.application_store = application_store
        self.builder = builder
        self.worker_id = worker_id

    def _ai(self, job: AsyncJob, *, model: str, reasoning_effort: str | None):
        try:
            configured_timeout = float(
                os.getenv("CAREER_BRIDGE_RESUME_ASYNC_AI_TIMEOUT_SECONDS", "240")
            )
        except (TypeError, ValueError):
            configured_timeout = 240.0
        return self.builder.ResumeAI(
            model=model,
            reasoning_effort=reasoning_effort,
            max_attempts=1,
            user_id=job.owner_id,
            request_timeout_seconds=min(300.0, max(30.0, configured_timeout)),
        )

    def load(self, job: AsyncJob):
        workflow_key = str(job.payload.get("workflow_key") or "").strip()
        if not workflow_key:
            raise ResumeWorkflowJobError("The queued resume job has no workflow key.")
        self.builder._bind_reusable_career_profile_context(job.owner_id)
        loaded = self.workflow_store.load(workflow_key)
        self.builder._hydrate_workflow_documents(loaded.state)
        return workflow_key, loaded

    def save(self, job: AsyncJob, workflow_key: str, loaded: Any):
        self.builder._persist_workflow_documents(
            job.owner_id, workflow_key, loaded.state
        )
        return self.workflow_store.save(
            workflow_key,
            loaded.state,
            expected_version=loaded.version,
            updated_by_request=f"ASYNC-{job.id[:24]}",
        )

    def verify_guard(self, job: AsyncJob, state: Any) -> Any:
        models = self.builder.resolve_models(state)
        assert_resume_job_guard(
            state,
            job.payload.get("guard") or {},
            models,
            workflow_input_fingerprint=self.builder.input_fingerprint(state, models),
        )
        return models

    def translate_baseline(self, job: AsyncJob, state: Any, models: Any) -> None:
        ai = self._ai(
            job,
            model=models.analysis_tailoring_model,
            reasoning_effort=models.analysis_tailoring_reasoning_effort,
        )
        self.builder._ensure_target_language_profile(state, ai)
        if (
            self.builder._baseline_creation_method(state) == "mixed"
            and state.manual_source_profile is not None
        ):
            state.source_profile = self.builder.merge_candidate_profiles(
                state.source_profile,
                state.manual_source_profile,
            )

    def analyze(self, job: AsyncJob, state: Any, models: Any):
        current_input = self.builder.input_fingerprint(state, models)
        if state.analysis and state.analysis_input_fingerprint == current_input:
            return state.analysis, current_input
        ai = self._ai(
            job,
            model=models.analysis_tailoring_model,
            reasoning_effort=models.analysis_tailoring_reasoning_effort,
        )
        analysis = ai.analyze_job(state.job_description, state.target_title)
        state.analysis = analysis
        state.analysis_input_fingerprint = current_input
        self.builder.ensure_recommended_resume_style(state)
        return analysis, current_input

    def create_initial_proposal(self, job: AsyncJob, state: Any, models: Any, analysis: Any, current_input: str):
        if (
            state.initial_evidence_proposal is not None
            and state.initial_evidence_input_fingerprint == current_input
        ):
            proposal = state.initial_evidence_proposal.model_copy(deep=True)
        else:
            ai = self._ai(
                job,
                model=models.analysis_tailoring_model,
                reasoning_effort=models.analysis_tailoring_reasoning_effort,
            )
            proposal = ai.create_proposal(
                state.source_profile,
                analysis,
                self.builder._effective_career_background(state),
            )
        proposal = self.builder.repair_missing_bullet_proposals(
            state.source_profile, proposal
        )
        proposal = self.builder.prioritize_candidate_questions(proposal, analysis)
        proposal = self.builder.ensure_career_translation_assessment(
            state.source_profile,
            analysis,
            proposal,
            self.builder._effective_career_background(state),
        )
        proposal = self.builder._apply_confirmed_title_interpretations(
            job.owner_id, state.source_profile, proposal
        )
        state.initial_evidence_proposal = proposal.model_copy(deep=True)
        state.initial_evidence_input_fingerprint = current_input
        return proposal

    def apply_tailoring_result(self, job: AsyncJob, state: Any, analysis: Any, proposal: Any, current_input: str) -> None:
        proposal, reused_profile, reused_answers, reusable_draft = (
            self.builder._reuse_library_confirmation_answers(
                job.owner_id,
                state.source_profile,
                analysis,
                proposal,
            )
        )
        state.clear_tailoring_results()
        self.builder.capture_workflow_step_snapshot(
            state, "initial", profile=state.source_profile
        )
        state.analysis = analysis
        state.analysis_input_fingerprint = current_input
        self.builder.ensure_recommended_resume_style(state)
        state.workflow_stage = "draft"
        state.provisional_proposal = proposal.model_copy(deep=True)
        state.analyzed_input_fingerprint = current_input
        state.confirmation_complete = False
        state.candidate_answers = [
            answer.model_copy(deep=True) for answer in reused_answers
        ]
        state.confirmation_draft = dict(reusable_draft)
        state.confirmed_profile = (
            reused_profile.model_copy(deep=True)
            if reused_profile is not None
            else None
        )
        state.reused_library_evidence_count = len(reused_answers)
        state.initial_evidence_proposal = proposal.model_copy(deep=True)
        state.initial_evidence_input_fingerprint = current_input
        state.initial_report = None
        state.initial_report_input_fingerprint = None
        state.initial_report_analysis = None
        state.initial_report_proposal = None
        state.initial_report_created_at = ""
        state.initial_report_error = ""

    def refresh_report(self, state: Any, report_name: str, *, force: bool = True) -> None:
        if report_name == "initial":
            if state.analysis is None or state.initial_evidence_proposal is None:
                raise ResumeWorkflowJobError("Initial report inputs are not ready.")
            if not self.builder._refresh_initial_resume_report(
                state,
                state.analysis,
                state.initial_evidence_proposal,
                force=force,
            ):
                raise ResumeWorkflowJobError(
                    state.initial_report_error
                    or f"The {self.builder.APPLICATION_BASELINE_LABEL} Report could not be generated."
                )
            return
        if report_name == "draft":
            if (
                state.analysis is None
                or state.draft_proposal is None
                or not state.confirmation_complete
            ):
                raise ResumeWorkflowJobError("Job-Aligned report inputs are not ready.")
            profile = state.confirmed_profile or state.source_profile
            if not self.builder._refresh_job_aligned_resume_report(
                state, profile, state.draft_proposal, force=force
            ):
                raise ResumeWorkflowJobError(
                    state.updated_report_error
                    or "The Job-Aligned Resume Report could not be generated."
                )
            return
        if report_name == "final":
            if state.analysis is None or state.final_proposal is None:
                raise ResumeWorkflowJobError("Final report inputs are not ready.")
            profile = state.confirmed_profile or state.source_profile
            if state.final_resume_bytes is None:
                self.generate_export(state, include_exact_report=False)
            self.builder._build_final_report_snapshot(
                state, profile, state.final_proposal, state.final_resume_bytes
            )
            state.optimization_report_after = state.final_report
            return
        raise ResumeWorkflowJobError(f"Unknown resume report: {report_name}")

    def run_final_optimization(self, job: AsyncJob, state: Any, models: Any) -> None:
        # A worker can be replaced after the workflow save succeeds but before the
        # AsyncJob cursor is advanced. Treat a saved terminal optimization as the
        # completed phase so a reclaimed job does not repeat the AI calls.
        if (
            state.workflow_stage == "final"
            and state.optimization_started_at
            and state.optimization_status not in {"not_started", "queued", "pending"}
            and state.final_proposal is not None
        ):
            return
        if (
            state.analysis is None
            or state.draft_proposal is None
            or not state.confirmation_complete
        ):
            raise ResumeWorkflowJobError(
                "Complete the Job-Aligned Resume before running final optimization."
            )
        if state.analyzed_input_fingerprint != self.builder.input_fingerprint(state, models):
            raise ResumeWorkflowJobError(
                "The job description or tailoring model changed. Start tailoring again."
            )
        profile = state.confirmed_profile or state.source_profile
        job_aligned = state.draft_proposal.model_copy(deep=True)
        working = (
            state.final_proposal.model_copy(deep=True)
            if state.final_proposal is not None
            else job_aligned.model_copy(deep=True)
        )
        working = self.builder.repair_missing_bullet_proposals(profile, working)
        working, _ = self.builder.apply_all_until_valid(
            profile, state.analysis, working
        )
        working.candidate_questions = []

        # Run the independent evidence review in the worker, with no additional
        # candidate question round. Accuracy fixes are applied before optimization.
        evidence_ai = self._ai(
            job,
            model=models.evidence_review_model,
            reasoning_effort=models.evidence_review_reasoning_effort,
        )
        reviewed, _audit, _questions = self.builder._run_post_confirmation_evidence_review(
            models,
            profile,
            state.analysis,
            working,
            self.builder._effective_career_background(state),
            allow_candidate_questions=False,
            audit_ai=evidence_ai,
        )
        reviewed = self.builder.repair_missing_bullet_proposals(profile, reviewed)
        reviewed, _ = self.builder.apply_all_until_valid(
            profile, state.analysis, reviewed
        )
        reviewed.candidate_questions = []

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.builder.capture_workflow_step_snapshot(
            state, "draft", proposal=job_aligned, profile=profile
        )
        state.draft_proposal = job_aligned.model_copy(deep=True)
        report_filename = self.builder.safe_filename(
            f"{profile.name}_{state.analysis.target_title}_Resume"
        ) + ".docx"
        job_aligned_fingerprint = self.builder._proposal_fingerprint(job_aligned)
        if (
            state.updated_report is not None
            and state.updated_report_input_fingerprint == state.analyzed_input_fingerprint
            and state.updated_report_proposal_fingerprint == job_aligned_fingerprint
        ):
            job_aligned_report = state.updated_report
        else:
            job_aligned_report = self.builder._build_optimization_report(
                state,
                profile,
                job_aligned,
                report_filename,
                exact_page_count=False,
            )
        report_before = self.builder._build_optimization_report(
            state,
            profile,
            reviewed,
            report_filename,
            exact_page_count=False,
        )
        baseline_safe, _ = self.builder.final_optimization_score_guard(
            job_aligned_report, report_before
        )
        baseline_rolled_back = not baseline_safe
        if baseline_rolled_back:
            reviewed = job_aligned.model_copy(deep=True)
            report_before = job_aligned_report

        issue_batches = self.builder.final_optimization_actionable_issue_batches(
            report_before
        )
        report_issues = [issue for batch in issue_batches for issue in batch]
        optimized = reviewed.model_copy(deep=True)
        report_after = report_before
        accepted_issues: list[Any] = []
        accepted_batch_count = 0
        rejected_batch_count = 0
        rejected_issue_count = 0
        unchanged_batch_count = 0
        optimization_status = "not_needed" if not report_issues else "pending"
        optimization_notice = ""
        current_validation_count = len(
            self.builder.validate_proposal(profile, state.analysis, optimized)
        )

        optimization_changed = False
        if report_issues:
            try:
                optimizer = self._ai(
                    job,
                    model=models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
                )
                candidate = optimizer.apply_suggested_fixes(
                    profile,
                    state.analysis,
                    optimized,
                    report_issues,
                    self.builder._effective_career_background(state),
                )
                candidate = self.builder.repair_missing_bullet_proposals(profile, candidate)
                candidate = self.builder.ensure_career_translation_assessment(
                    profile,
                    state.analysis,
                    candidate,
                    self.builder._effective_career_background(state),
                )
                candidate = self.builder._apply_confirmed_title_interpretations(
                    job.owner_id, profile, candidate
                )
                candidate, _ = self.builder.apply_all_until_valid(
                    profile, state.analysis, candidate
                )
                candidate.candidate_questions = []
                if self.builder._proposal_json(candidate) == self.builder._proposal_json(optimized):
                    unchanged_batch_count = 1
                    optimization_status = "completed"
                else:
                    candidate_validation_count = len(
                        self.builder.validate_proposal(profile, state.analysis, candidate)
                    )
                    candidate_report = self.builder._build_optimization_report(
                        state,
                        profile,
                        candidate,
                        report_filename,
                        exact_page_count=False,
                    )
                    score_safe, _ = self.builder.final_optimization_score_guard(
                        report_before, candidate_report
                    )
                    if candidate_validation_count <= current_validation_count and score_safe:
                        optimized = candidate
                        report_after = candidate_report
                        accepted_issues = report_issues
                        accepted_batch_count = 1
                        optimization_status = "applied"
                        optimization_changed = True
                    else:
                        rejected_batch_count = 1
                        rejected_issue_count = len(report_issues)
                        optimization_status = "completed"
            except Exception:
                # The optimization pass is optional. Preserve the independently
                # evidence-reviewed resume and never persist provider details.
                optimized = reviewed.model_copy(deep=True)
                report_after = report_before
                optimization_status = "skipped"
                optimization_notice = (
                    "Optional final resume optimization was skipped. Your approved, "
                    "evidence-reviewed resume was preserved safely and export continued."
                )

        # Verify the exact post-optimization proposal independently only when
        # optional wording changes were accepted. The pre-optimization proposal
        # was already audited above.
        if optimization_changed:
            final_evidence_ai = self._ai(
                job,
                model=models.evidence_review_model,
                reasoning_effort=models.evidence_review_reasoning_effort,
            )
            optimized, _final_audit, _unused_questions = (
                self.builder._run_post_confirmation_evidence_review(
                    models,
                    profile,
                    state.analysis,
                    optimized,
                    self.builder._effective_career_background(state),
                    allow_candidate_questions=False,
                    audit_ai=final_evidence_ai,
                )
            )
            optimized = self.builder.repair_missing_bullet_proposals(profile, optimized)
            optimized, _ = self.builder.apply_all_until_valid(
                profile, state.analysis, optimized
            )
            optimized.candidate_questions = []
            report_after = self.builder._build_optimization_report(
                state,
                profile,
                optimized,
                report_filename,
                exact_page_count=False,
            )

        state.quality_review_started = True
        state.workflow_stage = "final"
        state.final_proposal = optimized.model_copy(deep=True)
        state.clear_final_report()
        state.updated_report = job_aligned_report
        state.updated_report_input_fingerprint = state.analyzed_input_fingerprint
        state.updated_report_proposal_fingerprint = job_aligned_fingerprint
        state.updated_report_created_at = now
        state.updated_report_error = ""
        state.optimization_report_before = report_before
        state.optimization_report_after = report_after
        state.optimization_started_at = now
        state.optimization_applied_issue_count = len(accepted_issues)
        state.optimization_accepted_batch_count = accepted_batch_count
        state.optimization_rejected_batch_count = rejected_batch_count
        state.optimization_rejected_issue_count = rejected_issue_count
        state.optimization_unchanged_batch_count = unchanged_batch_count
        state.optimization_baseline_rolled_back = baseline_rolled_back
        state.optimization_status = optimization_status
        state.optimization_notice = optimization_notice

    def generate_export(self, state: Any, *, include_exact_report: bool = True) -> None:
        if state.analysis is None or state.final_proposal is None:
            raise ResumeWorkflowJobError("The Final Resume is not ready for export.")
        profile = state.confirmed_profile or state.source_profile
        self.builder._store_optimized_final_export(
            state,
            profile,
            state.final_proposal,
            build_exact_report=include_exact_report,
        )
        approved = self.builder._approved_resume_from_proposal(
            profile,
            self.builder.effective_final_resume_title(state),
            state.final_proposal,
            state.analysis,
        )
        state.final_resume_pdf_bytes = self.builder.export_resume_pdf(
            profile,
            approved,
            **self.builder.resume_export_kwargs(state),
        )
        state.final_resume_pdf_error = ""
        if include_exact_report and state.final_report is not None:
            state.optimization_report_after = state.final_report

        return None
