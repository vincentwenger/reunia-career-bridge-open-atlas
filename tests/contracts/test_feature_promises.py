"""Contracts keeping product promises aligned with implemented capabilities."""

from __future__ import annotations

import unittest
from pathlib import Path

from career_bridge.presentation.feature_mapping import (
    CURRENT_APPLICATION_FOLLOW_UP_CAPABILITIES,
    SECONDARY_FEATURE_ROADMAP,
    feature_by_legacy_name,
)

ROOT = Path(__file__).resolve().parents[2]


class FeaturePromiseContracts(unittest.TestCase):
    def test_user_facing_copy_does_not_claim_recruiter_message_history(self) -> None:
        paths = (
            ROOT / "products/reunia/templates/_marketing_content.html",
            ROOT / "products/reunia/templates/help-support.html",
            ROOT / "products/reunia/templates/user-guide.html",
            ROOT / "products/reunia/templates/login.html",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for stale_claim in (
            "company notes, and recruiter messages",
            "company notes, recruiter messages",
            "Store the resume, job posting, company notes, and recruiter messages",
        ):
            self.assertNotIn(stale_claim, combined)
        self.assertIn("does not yet provide recruiter-message history", combined)

    def test_calendar_copy_is_explicitly_planned_not_current(self) -> None:
        upcoming = feature_by_legacy_name("Upcoming Meetings")
        self.assertIn("Track interview dates inside the application", upcoming.recommendation)
        self.assertIn("planned, not currently available", upcoming.recommendation)
        self.assertNotIn("Optional calendar integration for interview dates", upcoming.recommendation)

    def test_current_follow_up_capabilities_are_documented(self) -> None:
        self.assertEqual(
            CURRENT_APPLICATION_FOLLOW_UP_CAPABILITIES,
            (
                "Upcoming interview dates",
                "Application follow-up dates",
                "Custom next steps",
                "Interview-preparation actions",
                "Post-interview thank-you actions",
            ),
        )

    def test_secondary_feature_order_is_canonical(self) -> None:
        self.assertEqual(
            SECONDARY_FEATURE_ROADMAP,
            (
                "Communication log attached to an application",
                "Thank-you and follow-up message templates",
                "Recruiter-response drafting",
                "Optional calendar synchronization",
                "Cover-letter generation",
                "Advanced application analytics",
            ),
        )

    def test_readme_distinguishes_current_scope_from_roadmap(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("### Current application follow-up support", readme)
        self.assertIn("### Secondary-feature roadmap", readme)
        self.assertIn("does not currently provide recruiter-message history", readme)
        previous = -1
        for feature in SECONDARY_FEATURE_ROADMAP:
            current = readme.index(feature)
            self.assertGreater(current, previous)
            previous = current


if __name__ == "__main__":
    unittest.main()
