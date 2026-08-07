from pathlib import Path
import re
import unittest

from tests.source_aggregates import ADMIN_ANALYTICS_SOURCE


ROOT = Path(__file__).resolve().parents[2]
REUNIA_TEMPLATES = ROOT / "products" / "reunia" / "templates"
BUILDER_TEMPLATES = ROOT / "products" / "resume_taylor" / "templates" / "application_builder"


class ExplicitFeatureAnalyticsContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_every_rendered_page_has_an_explicit_feature_identifier(self):
        base = self.read("products/reunia/templates/base.html")
        marketing_base = self.read("products/reunia/templates/marketing-base.html")
        builder_base = self.read(
            "products/resume_taylor/templates/application_builder/base.html"
        )
        self.assertIn('data-feature="{% block feature_id %}', base)
        self.assertIn('data-feature="marketing"', marketing_base)
        self.assertIn('data-feature="{% block feature_id %}', builder_base)

        for template in sorted(REUNIA_TEMPLATES.glob("*.html")):
            text = template.read_text(encoding="utf-8")
            if re.search(r"\{% extends ['\"]base\.html['\"] %\}", text):
                self.assertIn(
                    "{% block feature_id %}",
                    text,
                    f"{template.name} must declare its page feature explicitly",
                )

    def test_tracker_reads_data_feature_instead_of_inferring_from_url(self):
        tracker = self.read("products/reunia/static/js/analytics-tracker.js")

        self.assertIn("body.dataset.feature", tracker)
        self.assertIn("feature: currentFeature", tracker)
        self.assertNotIn("path.includes('action-center')", tracker)
        self.assertNotIn("path.endsWith('/analytics.html')", tracker)
        self.assertNotIn("new URLSearchParams(window.location.search)", tracker)
        self.assertIn("currentFeature === 'interview_review'", tracker)

    def test_application_builder_loads_the_shared_tracker(self):
        builder_base = self.read(
            "products/resume_taylor/templates/application_builder/base.html"
        )
        self.assertIn("js/analytics-tracker.js", builder_base)
        for feature in (
            "job-applications",
            "baseline-resume",
            "job-discovery",
            "interview-preparation",
            "resume-reports",
            "ai-configuration",
            "resume-workflow",
        ):
            self.assertIn(feature, builder_base)

    def test_server_uses_canonical_current_feature_names(self):
        service = ADMIN_ANALYTICS_SOURCE.read_text()
        repository = self.read(
            "products/reunia/meeting_assistant/repositories/analytics_repository.py"
        )

        for feature in (
            '"career_evidence_library": "Career Evidence Library"',
            '"mock_interview": "Mock Interview"',
            '"interview_review": "Interview Review"',
            '"career_action_plan": "Career Action Plan"',
            '"progress": "Progress & Outcomes"',
        ):
            self.assertIn(feature, service)
        self.assertIn('"browser_recorder": "mock_interview"', service)
        self.assertIn('"action_center": "career_action_plan"', service)
        self.assertIn('item.get("last_feature")', service)
        self.assertIn('updated["last_feature"]', repository)
        self.assertIn('set_parts.append("#last_feature = :last_feature")', repository)


if __name__ == "__main__":
    unittest.main()
