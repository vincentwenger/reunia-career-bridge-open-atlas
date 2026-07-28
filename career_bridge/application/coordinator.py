from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from career_bridge.domain.enums import ActionStatus, JourneyStage
from career_bridge.domain.models import CareerAction, CareerJourney, utc_now
from career_bridge.ports import CareerBridgeRepository


class CareerBridgeCoordinator:
    """Small domain orchestrator with no Flask or database assumptions."""

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

    def create_journey(
        self,
        *,
        user_id: str,
        target_role: str,
        company: str = "",
        job_description_document_id: str = "",
    ) -> CareerJourney:
        now = self.clock()
        journey = CareerJourney(
            id=self.id_factory(),
            user_id=user_id,
            target_role=target_role,
            company=company,
            job_description_document_id=job_description_document_id,
            created_at=now,
            updated_at=now,
        )
        return self.repository.save_journey(journey)

    def move_journey(self, journey_id: str, stage: JourneyStage) -> CareerJourney:
        journey = self.repository.get_journey(journey_id)
        if journey is None:
            raise LookupError(f"career journey not found: {journey_id}")
        return self.repository.save_journey(journey.advance_to(stage, changed_at=self.clock()))

    def create_follow_up(
        self,
        *,
        journey_id: str,
        owner_user_id: str,
        title: str,
        due_at: datetime | None = None,
        source_ref: str = "",
    ) -> CareerAction:
        return CareerAction(
            id=self.id_factory(),
            journey_id=journey_id,
            title=title,
            owner_user_id=owner_user_id,
            status=ActionStatus.OPEN,
            due_at=due_at,
            source_ref=source_ref,
            created_at=self.clock(),
            updated_at=self.clock(),
        )
