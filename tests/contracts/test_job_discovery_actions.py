from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
SETTINGS_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_settings.html"
)
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_results.html"
)
DISCOVERY_PAGE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "job_discovery.html"
)
ANALYSIS_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_job_analysis.html"
)
APPLICATIONS_PAGE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "applications.html"
)
APPLICATION_TRACKER = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "application_tracker.py"
)


class JobDiscoveryActionContractTests(unittest.TestCase):
    def test_every_required_result_action_is_present(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        analysis = ANALYSIS_TEMPLATE.read_text(encoding="utf-8")
        for label in (
            "View posting",
            "View analysis",
            "Ignore",
            "Save",
            "Create Application Workspace",
        ):
            self.assertIn(label, template)
        self.assertIn("csrf_token()", template)
        self.assertIn("target=\"_blank\"", template)
        self.assertIn("rel=\"noopener noreferrer\"", template)
        self.assertIn("data-discovery-analysis-url", template)
        self.assertIn("Strongest matches", analysis)
        self.assertIn("Important gaps", analysis)
        self.assertIn("Why this matches", analysis)
        self.assertIn("data-record-id", analysis)
        self.assertIn("Career Evidence Library", analysis)
        self.assertNotIn("{% for item in fit.supported_requirements %}", analysis)

    def test_user_ready_source_and_preference_controls_are_present(self) -> None:
        settings = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        for label in (
            "Company sources",
            "Add a company source",
            "Greenhouse",
            "Lever",
            "Ashby",
            "Workday",
            "Manual career-page URL",
            "Desired job titles",
            "Preferred locations",
            "Accepted workplace types",
            "Preferred keywords",
            "Required keywords",
            "Save search preferences",
            "Save refresh schedule",
        ):
            self.assertIn(label, settings)
        source = APP.read_text(encoding="utf-8")
        for route in (
            "/discovery/sources",
            "/discovery/sources/<source_id>/update",
            "/discovery/sources/<source_id>/toggle",
            "/discovery/sources/<source_id>/delete",
            "/discovery/preferences",
        ):
            self.assertIn(route, source)


    def test_discovery_has_a_dedicated_page_and_navigation_boundary(self) -> None:
        app = APP.read_text(encoding="utf-8")
        discovery_page = DISCOVERY_PAGE.read_text(encoding="utf-8")
        applications_page = APPLICATIONS_PAGE.read_text(encoding="utf-8")
        self.assertIn('@application_builder_bp.get("/job-discovery")', app)
        self.assertIn('_discovery_settings.html', discovery_page)
        self.assertIn('_discovery_results.html', discovery_page)
        self.assertNotIn('_discovery_settings.html', applications_page)
        self.assertNotIn('_discovery_results.html', applications_page)
        self.assertIn('Open Job Discovery', applications_page)

    def test_results_use_server_side_pagination_tabs_and_compact_cards(self) -> None:
        source = APP.read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")
        page = DISCOVERY_PAGE.read_text(encoding="utf-8")
        self.assertIn('_DISCOVERY_PAGE_SIZES = (10, 20, 50)', source)
        self.assertIn('discovery_store.list_result_records_page(', source)
        self.assertIn('_discovery_paginate(', source)
        self.assertIn('DiscoveryResultIndexSummary(', source)
        self.assertIn('result_tab=result_tab', source)
        for result_group in (
            '"recommended"',
            '"possible"',
            '"pending"',
            '"low_match"',
            '"saved"',
            '"ignored"',
        ):
            self.assertIn(result_group, source)
        self.assertIn('/analysis"', source)
        self.assertIn("Results per page", template)
        self.assertIn("Minimum Job Fit", template)
        self.assertIn("High and Medium", source)
        self.assertIn("Possible matches", source)
        self.assertIn("Low matches", source)
        self.assertIn("Recommended", source)
        self.assertIn("Awaiting assessment", template)
        self.assertIn("data-discovery-analysis-url", template)
        self.assertIn("Manage catalog &amp; preferences", page)
        self.assertIn("Search Priority", source)


    def test_workday_browser_refresh_is_bounded_and_defers_new_ai_analysis(self) -> None:
        source = APP.read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("_interactive_discovery_source", source)
        self.assertIn('"detail_fetch_limit": capped_int("detail_fetch_limit", 10, 10)', source)
        self.assertIn('"fetch_budget_seconds": capped_float(', source)
        self.assertIn("analyze_new_jobs=False", source)
        self.assertIn("Awaiting assessment", template)

    def test_routes_are_explicit_user_actions(self) -> None:
        source = APP.read_text(encoding="utf-8")
        for route in (
            "/discovery/jobs/<source_id>/<job_id>/save",
            "/discovery/jobs/<source_id>/<job_id>/ignore",
            "/discovery/jobs/<source_id>/<job_id>/create-application",
        ):
            self.assertIn(route, source)
        self.assertIn("DiscoveredJobApplicationService", source)

    def test_application_record_has_dedicated_source_job_id(self) -> None:
        tree = ast.parse(APPLICATION_TRACKER.read_text(encoding="utf-8"))
        record = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ApplicationRecord"
        )
        annotated_names = {
            node.target.id
            for node in record.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
        }
        self.assertIn("source_job_id", annotated_names)
        source = APPLICATION_TRACKER.read_text(encoding="utf-8")
        self.assertIn("applications_owner_source_job_idx", source)
        self.assertIn("WHERE source_job_id <> ''", source)


if __name__ == "__main__":
    unittest.main()
