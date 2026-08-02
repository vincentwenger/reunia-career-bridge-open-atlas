"""Contracts for Progress & Outcomes and automatic interview readiness."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ProgressOutcomesAndReadinessContractTests(unittest.TestCase):
    def test_progress_area_uses_production_name_everywhere(self) -> None:
        paths = (
            ROOT / "products/reunia/templates/navbar.html",
            ROOT / "products/reunia/templates/analytics.html",
            ROOT / "products/reunia/templates/user-guide.html",
            ROOT / "career_bridge/presentation/navigation.py",
            ROOT / "career_bridge/presentation/feature_mapping.py",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertIn("Progress & Outcomes", combined)
        self.assertNotIn("Impact & Progress", combined)
        self.assertNotIn("Social Impact & Career Progress", combined)
        self.assertNotIn("Réunia - Social Impact", combined)

    def test_application_forms_do_not_accept_manual_readiness(self) -> None:
        template_paths = (
            ROOT / "products/resume_taylor/templates/application_builder/applications.html",
        )
        for path in template_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('name="interview_readiness"', text)
            self.assertNotIn("new-readiness", text)
            self.assertIn("calculated automatically", text)
            self.assertIn("readiness_by_application", text)

        app_source = (ROOT / "products/resume_taylor/app.py").read_text(encoding="utf-8")
        self.assertNotIn('_optional_score("interview_readiness")', app_source)
        self.assertIn("_applications_with_calculated_readiness", app_source)

    def test_progress_endpoint_and_dashboard_expose_calculated_readiness(self) -> None:
        service = (
            ROOT
            / "products/reunia/meeting_assistant/services/career_impact_service.py"
        ).read_text(encoding="utf-8")
        template = (ROOT / "products/reunia/templates/analytics.html").read_text(
            encoding="utf-8"
        )
        script = (
            ROOT / "products/reunia/static/js/pages/analytics.js"
        ).read_text(encoding="utf-8")
        self.assertIn('"interview_readiness"', service)
        self.assertIn('"interview_ready_applications"', service)
        self.assertIn('id="impact-ready"', template)
        self.assertIn("item.interview_readiness", script)
        self.assertIn("summary.interview_ready_applications", script)

    def test_storage_can_list_preparation_ids_without_loading_s3_documents(self) -> None:
        protocol = (
            ROOT / "products/resume_taylor/resume_tailor/storage.py"
        ).read_text(encoding="utf-8")
        dynamo = (
            ROOT / "products/resume_taylor/resume_tailor/dynamodb_storage.py"
        ).read_text(encoding="utf-8")
        self.assertIn("list_interview_preparation_application_ids", protocol)
        self.assertIn("list_interview_preparation_application_ids", dynamo)
        method = dynamo[dynamo.index("def list_interview_preparation_application_ids") :]
        method = method[: method.index("def get_interview_preparation")]
        self.assertIn("_query_prefix", method)
        self.assertNotIn("_read_json_document", method)


if __name__ == "__main__":
    unittest.main()
