"""Contracts ensuring Application Baseline uses Foundation as its source of truth."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "index.html"
)


class ApplicationBaselineFoundationSyncContractTests(unittest.TestCase):
    def test_application_baseline_syncs_from_foundation_until_frozen(self) -> None:
        source = APP.read_text(encoding="utf-8")

        self.assertIn("def _sync_application_from_foundation", source)
        self.assertIn("def _application_baseline_is_frozen", source)
        self.assertIn(
            "g.application_baseline_status = _sync_application_from_foundation",
            source,
        )
        self.assertIn("workflow_state.clear_results()", source)
        self.assertIn('return "frozen" if differs else "current"', source)

    def test_application_resume_upload_is_managed_only_in_foundation(self) -> None:
        source = APP.read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('if not is_career_translation:', source)
        self.assertIn("Application Baseline is managed in Foundation", source)
        self.assertNotIn('name="return_to" value="setup"', template)
        self.assertNotIn('id="mvp-profile-file"', template)
        self.assertNotIn('id="profile-file"', template)
        self.assertIn("Review Foundation Baseline Resume", template)

    def test_frozen_application_can_explicitly_restart_from_current_foundation(self) -> None:
        source = APP.read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")

        self.assertIn(
            '"/applications/<application_id>/baseline/refresh"', source
        )
        self.assertIn("Restart with current Baseline Resume", template)
        self.assertIn("Previous tailoring results were cleared", source)


if __name__ == "__main__":
    unittest.main()
