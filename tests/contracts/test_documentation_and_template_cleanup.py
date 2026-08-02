"""Contracts for canonical templates and current production copy."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DocumentationAndTemplateCleanupTests(unittest.TestCase):
    def test_resume_taylor_has_one_canonical_template_set(self) -> None:
        template_root = ROOT / "products" / "resume_taylor" / "templates"
        for obsolete_name in (
            "applications.html",
            "base.html",
            "career_bridge_navbar.html",
            "index.html",
            "interview_preparation.html",
        ):
            self.assertFalse((template_root / obsolete_name).exists(), obsolete_name)

        canonical = template_root / "application_builder"
        for required_name in (
            "applications.html",
            "base.html",
            "index.html",
            "interview_preparation.html",
        ):
            self.assertTrue((canonical / required_name).is_file(), required_name)

    def test_current_user_surfaces_have_no_prototype_copy(self) -> None:
        paths = (
            ROOT / "products/resume_taylor/templates/application_builder/base.html",
            ROOT / "products/resume_taylor/templates/application_builder/applications.html",
            ROOT / "products/resume_taylor/templates/application_builder/index.html",
            ROOT / "products/reunia/templates/_marketing_content.html",
            ROOT / "products/reunia/templates/settings.html",
            ROOT / "products/reunia/templates/user-guide.html",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for stale in (
            "Career Bridge MVP",
            "Hackathon MVP",
            "Top 20",
            "in-memory workflow",
            "selected OpenAI model",
            "Browser-only MVP",
            "Initial, Job-Aligned",
        ):
            self.assertNotIn(stale, combined)

    def test_primary_documentation_is_current_and_canonical(self) -> None:
        combined = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in (
                "README.md",
                "docs/README.md",
                "docs/job_discovery.md",
                "docs/validation/final_integration_checks.md",
                "docs/validation/validation_report.md",
                "reports/README.md",
            )
        )
        self.assertIn("Baseline Resume", combined)
        self.assertIn("Application Baseline", combined)
        self.assertIn("Progress & Outcomes", combined)
        self.assertIn("Generated files under `reports/validation/`", combined)
        self.assertNotIn("The MVP never submits", combined)
        self.assertNotIn("the MVP can save/ignore", combined)
        self.assertNotIn("Date: 2026-07-29", combined)


if __name__ == "__main__":
    unittest.main()
