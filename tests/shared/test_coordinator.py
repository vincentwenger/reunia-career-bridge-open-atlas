from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any

from career_bridge.application.coordinator import CareerBridgeCoordinator
from career_bridge.domain.enums import ApplicationStatus, ImprovementArea
from career_bridge.domain.models import ImprovementAction, JobApplication


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


class InMemoryRepository:
    def __init__(self) -> None:
        self.applications: dict[str, JobApplication] = {}
        self.actions: dict[str, ImprovementAction] = {}

    def save_application(self, application: JobApplication) -> JobApplication:
        self.applications[application.id] = application
        return application

    def get_application(self, application_id: str) -> JobApplication | None:
        return self.applications.get(application_id)

    def save_improvement_action(self, action: ImprovementAction) -> ImprovementAction:
        self.actions[action.id] = action
        return action

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(name)


class CoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ids = iter(("application-1", "action-1"))
        self.repository = InMemoryRepository()
        self.coordinator = CareerBridgeCoordinator(
            self.repository,  # type: ignore[arg-type]
            id_factory=lambda: next(self.ids),
            clock=lambda: NOW,
        )

    def create_application(self) -> JobApplication:
        return self.coordinator.create_application(
            user_id="user-1",
            candidate_profile_id="candidate-1",
            career_background_id="background-1",
            resume_id="resume-1",
            target_job_description_id="job-1",
            evidence_library_id="library-1",
        )

    def test_creates_and_moves_job_application(self) -> None:
        application = self.create_application()
        self.assertEqual(application.id, "application-1")
        self.assertEqual(application.status, ApplicationStatus.DRAFT)
        self.assertEqual(application.status_history[-1].to_status, ApplicationStatus.DRAFT)

        preparing = self.coordinator.change_application_status(
            application.id,
            ApplicationStatus.PREPARING,
            reason="Started tailoring",
        )
        self.assertEqual(preparing.status, ApplicationStatus.PREPARING)
        self.assertEqual(len(preparing.status_history), 2)

    def test_creates_and_attaches_application_scoped_improvement_action(self) -> None:
        application = self.create_application()
        action = self.coordinator.create_improvement_action(
            application_id=application.id,
            owner_user_id="user-1",
            title="Practice concise STAR response",
            area=ImprovementArea.INTERVIEW_DELIVERY,
        )

        self.assertEqual(action.application_id, application.id)
        self.assertEqual(action.id, "action-1")
        saved_application = self.repository.get_application(application.id)
        self.assertIsNotNone(saved_application)
        assert saved_application is not None
        self.assertEqual(saved_application.improvement_action_ids, (action.id,))

    def test_action_requires_an_existing_application(self) -> None:
        with self.assertRaises(LookupError):
            self.coordinator.create_improvement_action(
                application_id="missing",
                owner_user_id="user-1",
                title="Impossible action",
            )


if __name__ == "__main__":
    unittest.main()
