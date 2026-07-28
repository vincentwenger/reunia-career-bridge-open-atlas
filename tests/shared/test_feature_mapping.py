from __future__ import annotations

import unittest
from pathlib import Path

from career_bridge.presentation.feature_mapping import (
    feature_by_legacy_name,
    repurposed_features,
)


class FeatureRepurposingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[2]

    def test_csv_mapping_is_fully_represented(self) -> None:
        features = repurposed_features()
        self.assertEqual(len(features), 15)
        self.assertEqual(len({item.legacy_name for item in features}), 15)

    def test_practice_and_safety_boundaries_are_explicit(self) -> None:
        self.assertEqual(
            feature_by_legacy_name("Browser Meeting Recorder").career_bridge_name,
            "Mock Interview Recorder",
        )
        self.assertFalse(
            feature_by_legacy_name("Windows Desktop Recorder").available_in_mvp
        )
        self.assertFalse(feature_by_legacy_name("Live Q&A").available_in_mvp)

    def test_admin_capabilities_remain_unchanged(self) -> None:
        self.assertEqual(
            feature_by_legacy_name("Admin Analytics").career_bridge_name,
            "Admin Analytics",
        )
        self.assertEqual(
            feature_by_legacy_name("Incidents").career_bridge_name,
            "Incidents",
        )

    def test_reunia_uses_clean_career_routes_with_legacy_compatibility(self) -> None:
        navbar = (self.root / "products/reunia/templates/navbar.html").read_text(
            encoding="utf-8"
        )
        routes = (
            self.root
            / "products/reunia/meeting_assistant/blueprints/knowledge/routes.py"
        ).read_text(encoding="utf-8")
        for route in (
            "/career-profile",
            "/application-builder",
            "/interview-preparation",
            "/mock-interview",
            "/interview-review",
            "/career-action-plan",
            "/progress",
        ):
            self.assertIn(route, navbar)
        self.assertIn('/api/career/application-workspaces', routes)
        self.assertIn('/api/career/application-materials', routes)
        self.assertIn('/api/career/evidence/search', routes)
        self.assertIn('/api/knowledge/upcoming-meetings', routes)

    def test_candidate_clients_prefer_career_api_aliases(self) -> None:
        expected = {
            "products/reunia/static/js/pages/knowledge.js": (
                "/api/career/application-workspaces",
                "/api/career/application-materials",
                "/api/career/evidence/search",
            ),
            "products/reunia/static/js/pages/meeting-recorder.js": (
                "/api/career/mock-interviews/sessions",
            ),
            "products/reunia/static/js/pages/meeting-review.js": (
                "/api/career/interview-reviews",
                "/api/career/actions",
            ),
            "products/reunia/static/js/pages/action-center.js": (
                "/api/career/actions",
                "/api/career/interview-reviews",
            ),
        }
        for relative_path, aliases in expected.items():
            source = (self.root / relative_path).read_text(encoding="utf-8")
            for alias in aliases:
                self.assertIn(alias, source)

    def test_retired_mvp_features_are_not_advertised_to_candidates(self) -> None:
        recorder = (
            self.root / "products/reunia/templates/meeting-recorder.html"
        ).read_text(encoding="utf-8")
        settings = (self.root / "products/reunia/templates/settings.html").read_text(
            encoding="utf-8"
        )
        marketing = (
            self.root / "products/reunia/templates/_marketing_content.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn('id="enableLiveQA"', recorder)
        self.assertNotIn('id="openLiveQALink"', recorder)
        self.assertNotIn('id="live-qa-settings"', settings)
        self.assertNotIn('id="desktop-recorder"', marketing)

    def test_retired_endpoints_return_gone_and_skip_live_service(self) -> None:
        recorder_routes = (
            self.root
            / "products/reunia/meeting_assistant/blueprints/recorder/routes.py"
        ).read_text(encoding="utf-8")
        transcript_routes = (
            self.root
            / "products/reunia/meeting_assistant/blueprints/transcripts/routes.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("BrowserRecorderLiveService", recorder_routes)
        self.assertIn('"stage": "retired_live_assistance"', recorder_routes)
        self.assertIn("), 410", recorder_routes)
        self.assertIn("Retired desktop-recorder compatibility endpoint", transcript_routes)
        self.assertIn('"replacement": "/mock-interview"', transcript_routes)

    def test_interview_preparation_and_evidence_library_are_distinct(self) -> None:
        template = (self.root / "products/reunia/templates/knowledge.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("{% set page_title = 'Interview Preparation' %}", template)
        self.assertIn("<strong>Career Evidence Library</strong>", template)
        self.assertIn("<strong>Interview Preparation</strong>", template)
        self.assertIn("Create Application Workspace", template)
        self.assertIn("Save Application Materials", template)

    def test_interview_scorecard_names_all_five_dimensions(self) -> None:
        prompt = (
            self.root
            / "products/reunia/meeting_assistant/prompts/scorecard_grading.py"
        ).read_text(encoding="utf-8").lower()
        panel = (
            self.root
            / "products/reunia/templates/meeting/_scorecard_panel.html"
        ).read_text(encoding="utf-8").lower()
        for dimension in ("relevance", "evidence", "structure", "clarity", "delivery"):
            self.assertIn(dimension, prompt)
            self.assertIn(dimension, panel)


if __name__ == "__main__":
    unittest.main()
