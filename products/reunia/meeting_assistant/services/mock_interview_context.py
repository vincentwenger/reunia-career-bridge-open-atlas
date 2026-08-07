from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module

class MockInterviewContextMixin:
    """Canonical application and verified-evidence context assembly."""

    def _workspace_context(
        self, user_id: str, workspace_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        selected_id = str(workspace_id or "").strip()
        if not selected_id:
            return {}, {}

        source, separator, raw_id = selected_id.partition(":")
        application_id = raw_id if separator else selected_id
        if source not in {"", "builder"}:
            raise ValidationError(
                "Only canonical job applications can be used for mock interviews."
            )
        builder_result = self._builder_application_context(user_id, application_id)
        if builder_result is None:
            raise ValidationError("The selected job application no longer exists.")
        return builder_result

    def _builder_application_context(
        self,
        user_id: str,
        application_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        application_store = current_app.extensions.get("career_bridge_application_store")
        if application_store is None:
            return None
        try:
            application = application_store.get(user_id, application_id)
        except Exception as exc:
            raise ExternalServiceError(
                "The selected job application could not be loaded."
            ) from exc
        if application is None:
            return None

        company = str(getattr(application, "company", "") or "").strip()
        role = str(getattr(application, "role", "") or "").strip()
        interview_audience = str(
            getattr(application, "interview_audience", "") or ""
        ).strip()
        title = " at ".join(value for value in (role, company) if value)
        if not title:
            title = "Untitled application"

        preparation_content: dict[str, Any] = {}
        verified_evidence: list[dict[str, str]] = []
        evidence_source = ""
        try:
            preparation_record = application_store.get_interview_preparation(
                user_id,
                application_id,
            )
        except Exception:
            current_app.logger.exception(
                "Could not load saved interview preparation for application %s",
                application_id,
            )
            preparation_record = None

        if preparation_record is not None:
            preparation_content = _json_object(
                getattr(preparation_record, "content_json", "")
            )
            evidence_snapshot = _json_object(
                getattr(preparation_record, "evidence_snapshot_json", "")
            )
            verified_evidence = [
                {"id": str(evidence_id), "text": str(evidence_text)[:1800]}
                for evidence_id, evidence_text in list(evidence_snapshot.items())[:80]
                if str(evidence_text or "").strip()
            ]
            evidence_source = str(
                getattr(preparation_record, "evidence_source_label", "") or ""
            ).strip()

        if not verified_evidence:
            verified_evidence, evidence_source = self._workflow_verified_evidence(
                user_id,
                application_id,
                getattr(application, "resume_bytes", None),
            )

        status = str(getattr(application, "status", "") or "").strip()
        notes = str(getattr(application, "notes", "") or "").strip()
        next_action = str(getattr(application, "next_action", "") or "").strip()
        workspace = {
            "id": f"builder:{application_id}",
            "application_id": application_id,
            "source": "application_builder",
            "title": title,
            "company": company,
            "role": role,
            "purpose": next_action or notes,
            "status": status,
            "scheduled_at": str(
                getattr(application, "upcoming_event_date", "") or ""
            ),
            "participants": [interview_audience] if interview_audience else [],
            "interview_audience": interview_audience,
        }
        readiness = InterviewReadinessService(
            application_store=application_store,
            transcript_service=self.transcript_service,
        ).build_for_applications(user_id, [application]).get(application_id)
        try:
            application_materials = self.materials_service.get_materials(
                user_id, application_id
            )
            application_context = dict(
                application_materials.get("application_context") or {}
            )
        except Exception:
            current_app.logger.exception(
                "Could not load Application Materials context for mock interview %s",
                application_id,
            )
            application_context = {}

        context = {
            "company": company,
            "target_role": role,
            "job_description": str(
                getattr(application, "job_description", "") or ""
            )[:40000],
            "job_url": str(getattr(application, "job_url", "") or "")[:2000],
            "interview_audience": interview_audience,
            "application_status": status,
            "application_notes": notes[:5000],
            "next_action": next_action[:1000],
            "interview_readiness": (
                readiness.score if readiness is not None else None
            ),
            "saved_interview_preparation": preparation_content,
            "verified_evidence_source": evidence_source,
            "verified_candidate_evidence": verified_evidence,
            "application_materials_context": application_context,
        }
        return workspace, context

    def _workflow_verified_evidence(
        self,
        user_id: str,
        application_id: str,
        resume_bytes: bytes | None,
    ) -> tuple[list[dict[str, str]], str]:
        workflow_store = current_app.extensions.get("career_bridge_workflow_store")
        if workflow_store is None:
            return [], ""
        try:
            from resume_tailor.interview_preparation import (  # type: ignore
                build_verified_evidence_bundle,
            )

            workflow_state = workflow_store.get(
                f"{user_id}:application:{application_id}"
            )
            bundle = build_verified_evidence_bundle(
                workflow_state,
                submitted_resume_bytes=resume_bytes,
            )
        except Exception:
            current_app.logger.exception(
                "Could not build verified evidence for mock interview application %s",
                application_id,
            )
            return [], ""

        items = []
        for evidence_item in list(getattr(bundle, "items", ()) or ())[:80]:
            evidence_id = str(getattr(evidence_item, "id", "") or "").strip()
            evidence_text = str(getattr(evidence_item, "text", "") or "").strip()
            if evidence_text:
                items.append({"id": evidence_id, "text": evidence_text[:1800]})
        return items, str(getattr(bundle, "source_label", "") or "").strip()

    def _interview_evaluation_context_text(self, session: dict[str, Any]) -> str:
        workspace = session.get("application_workspace") or {}
        workspace_context = session.get("workspace_context") or {}
        verified_evidence = workspace_context.get("verified_candidate_evidence")
        if not isinstance(verified_evidence, list):
            verified_evidence = []

        reusable_profile = ReusableCareerProfile.from_mapping(
            session.get("candidate_context") or {}
        ).as_prompt_dict()
        compact = {
            "interview_type": str(session.get("interview_type_label") or "Mock Interview"),
            "custom_focus": str(session.get("custom_focus") or ""),
            "role_context": {
                "company": workspace_context.get("company") or workspace.get("company"),
                "target_role": workspace_context.get("target_role") or workspace.get("role"),
                "job_description": workspace_context.get("job_description"),
                "application_status": workspace_context.get("application_status"),
                "next_action": workspace_context.get("next_action"),
                "interview_audience": workspace_context.get("interview_audience"),
            },
            "reusable_career_profile_context": reusable_profile,
            "confirmed_candidate_evidence": verified_evidence[:80],
            "confirmed_evidence_source": workspace_context.get("verified_evidence_source"),
            "grounding_rule": (
                "The current candidate answer and confirmed_candidate_evidence are the only "
                "candidate-fact sources allowed in the sample improved answer. The reusable "
                "Career Profile may guide topic selection and career-direction coaching, but it "
                "is not verified evidence. Role context describes the opportunity and must never "
                "be presented as candidate experience."
            ),
        }
        return _truncate_json(compact, 14000)

    def _context_text(
        self,
        *,
        interview_type: str,
        custom_focus: str,
        workspace: dict[str, Any],
        workspace_context: dict[str, Any],
        candidate_context: dict[str, Any],
    ) -> str:
        candidate_profile = ReusableCareerProfile.from_mapping(
            candidate_context
        ).as_prompt_dict()
        compact = {
            "interview_type": _INTERVIEW_TYPES.get(interview_type, interview_type),
            "custom_focus": custom_focus,
            "application_workspace": {
                key: workspace.get(key)
                for key in ("title", "purpose", "participants", "scheduled_at")
                if workspace.get(key)
            },
            "role_and_application_context": workspace_context,
            "candidate_profile_context": candidate_profile,
        }
        return _truncate_json(compact, 14000)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
