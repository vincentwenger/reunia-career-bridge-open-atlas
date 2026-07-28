from __future__ import annotations

import unittest
from datetime import datetime, timezone

from career_bridge.domain.enums import JourneyStage, ScoreKind
from career_bridge.domain.models import CareerJourney, Score, UserProfile


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


class DomainModelTests(unittest.TestCase):
    def test_user_email_is_normalized(self) -> None:
        profile = UserProfile(id="user-1", email=" Vincent@Example.com ", created_at=NOW)
        self.assertEqual(profile.email, "alex.morgan@example.com")

    def test_journey_uses_controlled_transitions(self) -> None:
        journey = CareerJourney(
            id="journey-1",
            user_id="user-1",
            target_role="Software Engineer",
            created_at=NOW,
            updated_at=NOW,
        )
        applying = journey.advance_to(JourneyStage.APPLICATION, changed_at=NOW)
        self.assertEqual(applying.stage, JourneyStage.APPLICATION)
        with self.assertRaises(ValueError):
            journey.advance_to(JourneyStage.INTERVIEW, changed_at=NOW)

    def test_score_is_bounded(self) -> None:
        score = Score(
            id="score-1",
            journey_id="journey-1",
            kind=ScoreKind.JOB_FIT,
            value=83.333,
            confidence=0.8,
            created_at=NOW,
        )
        self.assertEqual(score.value, 83.33)
        with self.assertRaises(ValueError):
            Score(
                id="bad",
                journey_id="journey-1",
                kind=ScoreKind.OVERALL,
                value=101,
                created_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
