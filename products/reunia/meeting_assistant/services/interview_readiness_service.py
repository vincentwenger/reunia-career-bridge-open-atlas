from __future__ import annotations

from typing import Any, Iterable

from flask import current_app

from career_bridge.application.interview_readiness import (
    InterviewReadinessAssessment,
    build_interview_readiness_assessments,
)
from meeting_assistant.services.transcript_service import TranscriptService


class InterviewReadinessService:
    """Load saved preparation and practice records and calculate readiness."""

    def __init__(
        self,
        *,
        application_store: Any | None = None,
        transcript_service: TranscriptService | None = None,
    ) -> None:
        self.application_store = application_store
        self.transcript_service = transcript_service or TranscriptService()

    def build_for_applications(
        self,
        user_id: str,
        applications: Iterable[Any],
    ) -> dict[str, InterviewReadinessAssessment]:
        application_list = list(applications)
        application_ids = [
            str(getattr(application, "id", "") or "").strip()
            for application in application_list
        ]
        application_ids = [value for value in application_ids if value]
        if not application_ids:
            return {}

        store = self.application_store or current_app.extensions.get(
            "career_bridge_application_store"
        )
        prepared_application_ids: tuple[str, ...] = ()
        if store is not None:
            list_prepared = getattr(
                store, "list_interview_preparation_application_ids", None
            )
            if callable(list_prepared):
                try:
                    prepared_application_ids = tuple(list_prepared(user_id))
                except Exception:
                    current_app.logger.exception(
                        "Could not list saved Interview Preparation records for readiness"
                    )
            else:
                prepared: list[str] = []
                getter = getattr(store, "get_interview_preparation", None)
                if callable(getter):
                    for application_id in application_ids:
                        try:
                            if getter(user_id, application_id) is not None:
                                prepared.append(application_id)
                        except Exception:
                            current_app.logger.exception(
                                "Could not load Interview Preparation for readiness application=%s",
                                application_id,
                            )
                prepared_application_ids = tuple(prepared)

        try:
            reviews = self.transcript_service.list_for_user(user_id)
        except Exception:
            current_app.logger.exception(
                "Could not load mock-interview reviews for automatic readiness"
            )
            reviews = []

        return build_interview_readiness_assessments(
            application_ids,
            prepared_application_ids=prepared_application_ids,
            reviews=reviews,
        )
