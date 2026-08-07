"""Contracts for hiding model configuration from normal Career Bridge users."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class AIConfigurationVisibilityContractTests(unittest.TestCase):
    def test_canonical_navigation_excludes_ai_configuration(self) -> None:
        text = (ROOT / "career_bridge" / "presentation" / "navigation.py").read_text(encoding="utf-8")
        self.assertNotIn('key="builder_configuration"', text)
        self.assertNotIn('label="AI Configuration"', text)

    def test_all_navbars_limit_ai_configuration_to_admins(self) -> None:
        paths = (ROOT / "products" / "reunia" / "templates" / "navbar.html",)
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8")
                link_index = text.index("<strong>AI Configuration</strong>")
                guard_index = text.rfind("{% if is_admin_session %}", 0, link_index)
                guard_end = text.index("{% endif %}", link_index)
                self.assertGreaterEqual(guard_index, 0)
                self.assertGreater(guard_end, link_index)

    def test_normal_resume_workflow_does_not_expose_model_identifiers(self) -> None:
        for relative_path in (
            "products/resume_taylor/templates/application_builder/index.html",
        ):
            with self.subTest(template=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                configuration_end = text.index("{% else %}", text.index("{% if active_tab == 'configuration' %}"))
                normal_workflow = text[configuration_end:]
                self.assertNotIn("models.analysis_tailoring_model", normal_workflow)
                self.assertNotIn("models.evidence_review_model", normal_workflow)
                self.assertNotRegex(normal_workflow, r"gpt-[0-9]")


if __name__ == "__main__":
    unittest.main()
