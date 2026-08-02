from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
SETTINGS = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_settings.html"
)
STYLES = ROOT / "products" / "resume_taylor" / "static" / "styles.css"


class JobDiscoveryScanStatusUiContractTests(unittest.TestCase):
    def test_settings_builds_status_from_persisted_public_catalog_metadata(self) -> None:
        app = APP.read_text(encoding="utf-8")

        self.assertIn("def _discovery_source_scan_status", app)
        self.assertIn("list_public_catalog_statuses", app)
        self.assertIn("statuses_by_key.get(public_source_key(source))", app)
        self.assertIn('"discovery_source_scan_statuses"', app)
        self.assertIn('status.last_error', app)
        self.assertIn('status.last_attempt_at', app)
        self.assertIn('status.last_success_at', app)
        self.assertIn('status.job_count', app)
        self.assertIn('status.complete_scan', app)

    def test_each_company_source_shows_last_scan_result_and_issue_message(self) -> None:
        template = SETTINGS.read_text(encoding="utf-8")

        self.assertIn("Last scan result", template)
        self.assertIn("discovery_source_scan_statuses[source.id]", template)
        self.assertIn("{{ scan.label }}", template)
        self.assertIn("{{ scan.attempt_label }}", template)
        self.assertIn("{{ scan.message }}", template)
        self.assertIn("{{ scan.success_label }}", template)
        self.assertIn("{{ scan.job_count_label }}", template)
        self.assertIn("scan-{{ scan.state }}", template)

    def test_status_styles_distinguish_success_limited_issue_and_unscanned(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")

        for state in (
            "scan-success",
            "scan-limited",
            "scan-issue",
            "scan-permission_required",
            "scan-not_scanned",
        ):
            self.assertIn(state, styles)
        self.assertIn("discovery-source-scan-result", styles)
        self.assertIn("discovery-source-scan-message", styles)

    def test_robots_denial_is_presented_as_permission_required(self) -> None:
        app = APP.read_text(encoding="utf-8")

        self.assertIn('state = "permission_required"', app)
        self.assertIn('label = "Permission required"', app)
        self.assertIn("Career Bridge will not bypass that policy", app)
        self.assertIn("Previously collected jobs remain available", app)

    def test_indexed_timeout_is_presented_as_retryable_not_permanent(self) -> None:
        app = APP.read_text(encoding="utf-8")

        self.assertIn('label = "Retry recommended"', app)
        self.assertIn("reason to disable or remove the source", app)
        self.assertIn("Retry the source scan", app)
        self.assertIn("transient_index_issue", app)

    def test_settings_exposes_radancy_talentbrew_source_help(self) -> None:
        template = SETTINGS.read_text(encoding="utf-8")

        self.assertIn("Radancy / TalentBrew", template)
        self.assertIn("https://jobs.boeing.com/search-jobs", template)


if __name__ == "__main__":
    unittest.main()
