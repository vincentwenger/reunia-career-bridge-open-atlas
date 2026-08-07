from __future__ import annotations

from typing import Any


def register(namespace: dict[str, Any]) -> dict[str, Any]:
    """Register this feature's Application Builder routes and return shared helpers."""

    globals().update(namespace)
    def _application_owner_id() -> str:
        return str(getattr(g, "application_owner_id", session.get("workflow_sid", "")))

    def _optional_score(form_key: str) -> float | None:
        raw_value = request.form.get(form_key, "").strip()
        if not raw_value:
            return None
        try:
            return max(0.0, min(100.0, float(raw_value)))
        except ValueError:
            return None

    def _applications_with_calculated_readiness(applications):
        application_list = list(applications)
        try:
            from meeting_assistant.services.interview_readiness_service import (
                InterviewReadinessService,
            )

            assessments = InterviewReadinessService(
                application_store=application_store
            ).build_for_applications(_application_owner_id(), application_list)
        except Exception:
            current_app.logger.exception(
                "Could not calculate automatic interview readiness"
            )
            assessments = {}
        enriched = [
            replace(
                application,
                interview_readiness=(
                    assessments[application.id].score
                    if application.id in assessments
                    else None
                ),
            )
            for application in application_list
        ]
        return enriched, assessments

    def _workflow_state_for_application(application_id: str) -> WorkflowState:
        workflow_key = f"{_application_owner_id()}:application:{application_id}"
        if str(getattr(g, "workflow_key", "") or "") == workflow_key:
            return g.workflow_state
        return store.get(workflow_key)

    def _resume_findings_for_application(application_id: str) -> ResumeFindingsSnapshot:
        application = application_store.get(_application_owner_id(), application_id)
        workflow_state = _workflow_state_for_application(application_id)
        snapshot_state = workflow_state
        if application is not None and (
            normalize_job_description(workflow_state.job_description)
            != normalize_job_description(application.job_description)
            or normalize_target_title(workflow_state.target_title)
            != normalize_target_title(application.role)
        ):
            snapshot_state = WorkflowState(source_profile=workflow_state.source_profile)
            snapshot_state.job_description = application.job_description
            snapshot_state.target_title = application.role
        live_snapshot = build_resume_findings_snapshot(
            snapshot_state,
            company=application.company if application is not None else "",
            role=application.role if application is not None else "",
            job_description=(
                application.job_description if application is not None else workflow_state.job_description
            ),
        )
        if live_snapshot.has_findings():
            return live_snapshot

        stored = application_store.get_resume_findings(
            _application_owner_id(), application_id
        )
        if stored is not None:
            try:
                stored_snapshot = ResumeFindingsSnapshot.model_validate_json(
                    stored.snapshot_json
                )
                if (
                    stored_snapshot.application_context_fingerprint
                    == live_snapshot.application_context_fingerprint
                ):
                    return stored_snapshot
            except Exception:
                pass
        return live_snapshot

    def _persist_resume_findings(
        application_id: str, snapshot: ResumeFindingsSnapshot
    ) -> str:
        fingerprint = resume_findings_fingerprint(snapshot)
        existing = application_store.get_resume_findings(
            _application_owner_id(), application_id
        )
        if existing is not None and existing.fingerprint == fingerprint:
            return fingerprint
        application_store.save_resume_findings(
            _application_owner_id(),
            application_id,
            snapshot_json=snapshot.model_dump_json(),
            fingerprint=fingerprint,
        )
        return fingerprint

    def _selected_interview_application():
        applications = application_store.list_for_owner(_application_owner_id())
        requested_id = (
            str(request.args.get("application_id") or "").strip()
            or str(request.form.get("application_id") or "").strip()
            or str(session.get("active_application_id") or "").strip()
        )
        selected = (
            application_store.get(_application_owner_id(), requested_id)
            if requested_id
            else None
        )
        if selected is None and applications:
            selected = applications[0]
        if selected is not None:
            session["active_application_id"] = selected.id
            g.active_application = selected
        return applications, selected

    return {
        name: value
        for name, value in locals().items()
        if name != "namespace" and not name.startswith("__")
    }
