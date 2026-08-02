"""Contracts for the production Career Bridge home dashboard."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "products" / "reunia" / "templates" / "index.html"
SCRIPT = ROOT / "products" / "reunia" / "static" / "js" / "pages" / "index.js"
ROUTES = (
    ROOT
    / "products"
    / "reunia"
    / "meeting_assistant"
    / "blueprints"
    / "main"
    / "routes.py"
)


class HomeDashboardContractTests(unittest.TestCase):
    def test_dashboard_uses_compact_summary_endpoint(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")
        routes = ROUTES.read_text(encoding="utf-8")

        self.assertIn("/api/career/dashboard-summary", template)
        self.assertIn("/api/career/dashboard-summary", script)
        self.assertIn('@main_bp.get("/api/career/dashboard-summary")', routes)
        self.assertIn("def dashboard_summary", routes)
        self.assertNotIn("/api/career/mvp-progress", template)
        self.assertNotIn("/api/career/mvp-progress", script)
        self.assertNotIn("def mvp_progress", routes)

    def test_dashboard_keeps_foundation_and_application_scope_separate(self) -> None:
        routes = ROUTES.read_text(encoding="utf-8")
        self.assertIn('f"{user_id}:career-foundation:translation"', routes)
        self.assertIn('f"{user_id}:application:{selected_application_id}"', routes)
        self.assertIn('"Create your Baseline Resume"', routes)
        self.assertIn('"Discover jobs"', routes)
        self.assertIn('"Continue Resume Workflow"', routes)

    def test_dashboard_has_no_hackathon_copy(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in (TEMPLATE, SCRIPT, ROUTES)
        )
        for phrase in (
            "Hackathon MVP",
            "hackathon journey",
            "Recommended demonstration",
            "MVP journey",
            "data-mvp-step",
            "guided=1",
        ):
            self.assertNotIn(phrase, combined)


if __name__ == "__main__":
    unittest.main()
