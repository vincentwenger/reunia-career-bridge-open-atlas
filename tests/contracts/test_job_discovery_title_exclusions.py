from __future__ import annotations

import unittest
from pathlib import Path

from tests.application_builder_source import application_builder_source


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_settings.html"
)


class JobDiscoveryTitleExclusionsContractTests(unittest.TestCase):
    def test_settings_expose_distinct_title_only_and_posting_wide_fields(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Excluded job-title terms", template)
        self.assertIn('name="excluded_title_terms"', template)
        self.assertIn("Excluded terms anywhere in the posting", template)
        self.assertIn('name="excluded_terms"', template)
        self.assertIn("appears in its title", template)

    def test_preferences_route_persists_title_only_terms(self) -> None:
        source = application_builder_source()
        self.assertIn("excluded_title_terms=preferences.excluded_title_terms", source)
        self.assertIn('request.form.get("excluded_title_terms", "")', source)


if __name__ == "__main__":
    unittest.main()
