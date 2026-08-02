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
    / "_discovery_results.html"
)
SETTINGS_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_settings.html"
)
JAVASCRIPT = ROOT / "products" / "resume_taylor" / "static" / "app.js"


class JobDiscoveryBulkRefreshBatchContractTests(unittest.TestCase):
    def test_bulk_refresh_has_one_source_json_endpoint(self) -> None:
        app = APP.read_text(encoding="utf-8")
        self.assertIn('post("/discovery/refresh/source")', app)
        self.assertIn("def refresh_discovered_job_source", app)
        self.assertIn("_run_discovery_source_refresh(owner_id, [source])", app)
        self.assertIn("source_fetch_transform=_interactive_discovery_source", app)

    def test_standard_form_fallback_is_bounded_to_one_company(self) -> None:
        app = APP.read_text(encoding="utf-8")
        start = app.index("def refresh_discovered_jobs")
        segment = app[start : start + 2500]
        self.assertIn("refreshes one source per request", segment)
        self.assertIn("[selected_source]", segment)
        self.assertNotIn(".discover(\n            sources,", segment)

    def test_results_page_exposes_progressive_refresh_controls(self) -> None:
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )
        self.assertIn("data-discovery-batch-refresh", template)
        self.assertIn("data-source-refresh-url", template)
        self.assertIn("data-discovery-refresh-progress", template)
        self.assertIn("data-discovery-refresh-sources", template)
        self.assertNotIn("loading-form discovery-refresh-form", template)

    def test_company_source_cards_expose_single_source_scan(self) -> None:
        settings = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Scan this source", settings)
        self.assertIn("data-discovery-source-scan", settings)
        self.assertIn("data-source-refresh-url", settings)
        self.assertIn('name="source_id" value="{{ source.id }}"', settings)
        self.assertIn('name="return_to" value="settings"', settings)
        self.assertIn("Enable this source before scanning it.", settings)

        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertIn("careerBridgeDiscoverySourceScanSummary", javascript)
        self.assertIn("body: JSON.stringify({ source_id: sourceId })", javascript)
        self.assertIn("Scanning…", javascript)
        self.assertIn("data-discovery-source-scan-feedback", javascript)

    def test_single_source_form_fallback_returns_to_settings(self) -> None:
        app = APP.read_text(encoding="utf-8")
        start = app.index("def refresh_discovered_jobs")
        segment = app[start : start + 3500]
        self.assertIn('request.form.get("return_to")', segment)
        self.assertIn('view="settings"', segment)
        self.assertIn("requested_source_id and selected_source is None", segment)
        self.assertIn("return redirect(redirect_url)", segment)

    def test_browser_runs_company_requests_sequentially(self) -> None:
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertIn("for (const source of sources)", javascript)
        self.assertIn("body: JSON.stringify({ source_id: source.id })", javascript)
        self.assertIn("Stop after current company", javascript)
        self.assertIn("window.location.reload()", javascript)

    def test_interactive_limits_cover_url_driven_sources(self) -> None:
        app = APP.read_text(encoding="utf-8")
        start = app.index("def _interactive_discovery_source")
        segment = app[start : start + 5600]
        self.assertIn("JobSourceType.WORKDAY", segment)
        self.assertIn("JobSourceType.SUCCESSFACTORS", segment)
        self.assertIn("JobSourceType.ORACLE_CLOUD_HCM", segment)
        self.assertIn("JobSourceType.ICIMS", segment)
        self.assertIn("JobSourceType.GENERIC_JSONLD", segment)
        self.assertIn('"fetch_budget_seconds"', segment)
        self.assertIn('"max_pages"', segment)
        self.assertIn('"timeout_seconds"', segment)


if __name__ == "__main__":
    unittest.main()
