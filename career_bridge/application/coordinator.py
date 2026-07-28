from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from career_bridge.domain.enums import (
    ActionPriority,
    ActionStatus,
    ApplicationStatus,
    ImprovementArea,
)
from career_bridge.domain.models import (
    ApplicationStatusChange,
    ImprovementAction,
    JobApplication,
    utc_now,
)
from career_bridge.ports import CareerBridgeRepository


class CareerBridgeCoordinator:
    """Application orchestration without Flask or database assumptions."""

    def __init__(
        self,
        repository: CareerBridgeRepository,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.id_factory = id_factory or (lambda: str(uuid4()))
        self.clock = clock or utc_now

    def create_application(
        self,
        *,
        user_id: str,
        candidate_profile_id: str,
        career_background_id: str,
        resume_id: str,
        target_job_description_id: str,
        evidence_library_id: str,
    ) -> JobApplication:
        now = self.clock()
        application = JobApplication(
            id=self.id_factory(),
            user_id=user_id,
            candidate_profile_id=candidate_profile_id,
            career_background_id=career_background_id,
            resume_id=resume_id,
            target_job_description_id=target_job_description_id,
            evidence_library_id=evidence_library_id,
            status_history=(
                ApplicationStatusChange(
                    from_status=None,
                    to_status=ApplicationStatus.DRAFT,
                    changed_at=now,
                    reason="Application created",
                ),
            ),
            created_at=now,
            updated_at=now,
        )
        return self.repository.save_application(application)

    def change_application_status(
        self,
        application_id: str,
        status: ApplicationStatus,
        *,
        reason: str = "",
    ) -> JobApplication:
        application = self.repository.get_application(application_id)
        if application is None:
            raise LookupError(f"job application not found: {application_id}")
        updated = application.transition_to(
            status,
            changed_at=self.clock(),
            reason=reason,
        )
        return self.repository.save_application(updated)

    def create_improvement_action(
        self,
        *,
        application_id: str,
        owner_user_id: str,
        title: str,
        area: ImprovementArea = ImprovementArea.OTHER,
        priority: ActionPriority = ActionPriority.MEDIUM,
        description: str = "",
        due_at: datetime | None = None,
        source_ref: str = "",
    ) -> ImprovementAction:
        application = self.repository.get_application(application_id)
        if application is None:
            raise LookupError(f"job application not found: {application_id}")

        now = self.clock()
        action = ImprovementAction(
            id=self.id_factory(),
            application_id=application_id,
            title=title,
            owner_user_id=owner_user_id,
            area=area,
            priority=priority,
            status=ActionStatus.OPEN,
            description=description,
            due_at=due_at,
            source_ref=source_ref,
            created_at=now,
            updated_at=now,
        )
        saved_action = self.repository.save_improvement_action(action)
        updated_application = application.with_improvement_action(
            saved_action.id,
            changed_at=now,
        )
        self.repository.save_application(updated_application)
        return saved_action
