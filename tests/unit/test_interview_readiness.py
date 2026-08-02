from __future__ import annotations

import unittest

from career_bridge.application.interview_readiness import (
    READY_THRESHOLD,
    build_interview_readiness_assessments,
    calculate_interview_readiness,
)


class InterviewReadinessTests(unittest.TestCase):
    def test_readiness_requires_saved_preparation_and_scored_practice_to_reach_threshold(self) -> None:
        preparation_only = calculate_interview_readiness(
            "app-1", preparation_ready=True, latest_mock_score=None
        )
        practice_only = calculate_interview_readiness(
            "app-1", preparation_ready=False, latest_mock_score=100
        )
        complete = calculate_interview_readiness(
            "app-1", preparation_ready=True, latest_mock_score=75
        )

        self.assertEqual(preparation_only.score, 40.0)
        self.assertEqual(practice_only.score, 60.0)
        self.assertEqual(complete.score, 85.0)
        self.assertGreaterEqual(complete.score, READY_THRESHOLD)
        self.assertTrue(complete.is_ready)

    def test_no_evidence_returns_not_calculated(self) -> None:
        assessment = calculate_interview_readiness(
            "app-1", preparation_ready=False, latest_mock_score=None
        )
        self.assertIsNone(assessment.score)
        self.assertEqual(assessment.label, "Not calculated")
        self.assertEqual(assessment.status_label, "Not started")

    def test_latest_linked_scored_mock_interview_is_used(self) -> None:
        assessments = build_interview_readiness_assessments(
            ["app-1", "app-2"],
            prepared_application_ids=["app-1"],
            reviews=[
                {
                    "career_application_id": "app-1",
                    "timestamp": "2026-07-01T10:00:00Z",
                    "scorecard_type": "interview",
                    "interview_scorecard": {"overall_score": 50},
                },
                {
                    "career_application_id": "app-1",
                    "timestamp": "2026-07-02T10:00:00Z",
                    "scorecard_type": "interview",
                    "overall_score": 80,
                },
                {
                    "career_application_id": "app-2",
                    "timestamp": "2026-07-03T10:00:00Z",
                    "scorecard_type": "meeting",
                    "overall_score": 99,
                },
            ],
        )
        self.assertEqual(assessments["app-1"].latest_mock_score, 80.0)
        self.assertEqual(assessments["app-1"].scored_mock_interviews, 2)
        self.assertEqual(assessments["app-1"].score, 88.0)
        self.assertIsNone(assessments["app-2"].score)


if __name__ == "__main__":
    unittest.main()
