from __future__ import annotations

import unittest
from pathlib import Path

from tests.application_builder_source import application_builder_source
from tests.source_helpers import function_source, package_source


ROOT = Path(__file__).resolve().parents[2]
JOB_DISCOVERY_ROUTES = (
    ROOT
    / "products"
    / "resume_taylor"
    / "application_builder_routes"
    / "job_discovery_routes"
)
WORKSPACE_ROUTES = JOB_DISCOVERY_ROUTES / "workspace_routes.py"
WORKSPACE_VIEW = JOB_DISCOVERY_ROUTES / "workspace_view.py"
RESULT_QUERY = JOB_DISCOVERY_ROUTES / "result_query.py"
OPERATION_ROUTES = JOB_DISCOVERY_ROUTES / "operation_routes.py"
OPERATIONS = JOB_DISCOVERY_ROUTES / "operations.py"
SOURCE_ROUTES = JOB_DISCOVERY_ROUTES / "source_routes.py"
SOURCE_SUPPORT = JOB_DISCOVERY_ROUTES / "source_support.py"
APP = ROOT / "products" / "resume_taylor" / "app.py"
SHELL = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_results.html"
)
CONTENT = SHELL.with_name("_discovery_results_content.html")
SETTINGS = SHELL.with_name("_discovery_settings.html")
JAVASCRIPT = ROOT / "products" / "resume_taylor" / "static" / "app-job-discovery.js"
STYLES = ROOT / "products" / "resume_taylor" / "static" / "styles.css"


class JobDiscoveryAsyncResultContractTests(unittest.TestCase):
    def test_initial_get_renders_a_skeleton_and_defers_result_reads(self) -> None:
        shell = SHELL.read_text(encoding="utf-8")
        route = function_source(WORKSPACE_VIEW, "render_job_discovery_workspace")

        self.assertIn('discovery_results_inline = request.args.get("render_results") == "1"', route)
        self.assertIn("if discovery_results_inline:", route)
        self.assertIn('g.job_discovery_timing_index_state = "deferred_json"', route)
        self.assertLess(
            route.index("if discovery_results_inline:"),
            route.index("discovery_profile = _discovery_candidate_profile("),
        )
        self.assertIn("data-discovery-results-url", shell)
        self.assertIn("data-discovery-results-content", shell)
        self.assertIn("discovery-results-skeleton", shell)
        self.assertIn("aria-busy", shell)
        self.assertIn("render_results=1", route)

    def test_private_json_endpoint_returns_only_the_result_fragment(self) -> None:
        route = function_source(
            WORKSPACE_VIEW, "build_job_discovery_results_response"
        )

        self.assertIn("state(hydrate_documents=False)", route)
        self.assertIn("_discovery_result_cards(", route)
        self.assertIn('"application_builder/_discovery_results_content.html"', route)
        self.assertIn('response.headers["Cache-Control"] = "private, no-store"', route)
        self.assertIn('response.headers["Vary"] = "Cookie"', route)
        self.assertIn("jd_json_index", route)
        self.assertIn('"html": results_html', route)
        self.assertNotIn('"application_builder/job_discovery.html"', route)

    def test_browser_fetches_and_injects_cards_without_full_navigation(self) -> None:
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        content = CONTENT.read_text(encoding="utf-8")

        self.assertIn("const loadDiscoveryResults = async", javascript)
        self.assertIn("fetch(requestUrl", javascript)
        self.assertIn("target.innerHTML = payload.html", javascript)
        self.assertIn("showDiscoveryResultsSkeleton", javascript)
        self.assertIn("initializeDiscoveryAnalysis(target)", javascript)
        self.assertIn("window.history.pushState", javascript)
        self.assertIn("window.addEventListener('popstate'", javascript)
        self.assertIn("data-discovery-results-navigation", content)
        self.assertIn("data-discovery-page-size-form", content)
        self.assertIn("data-discovery-filter-auto-submit", content)
        self.assertIn("results.addEventListener('change'", javascript)
        self.assertIn("form.requestSubmit()", javascript)
        self.assertNotIn("this.form.requestSubmit()", content)

    def test_job_discovery_controls_do_not_require_inline_script(self) -> None:
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        content = CONTENT.read_text(encoding="utf-8")
        settings = SETTINGS.read_text(encoding="utf-8")
        templates = f"{content}\n{settings}"

        for inline_handler in ("onchange=", "onsubmit=", "onclick="):
            self.assertNotIn(inline_handler, templates)
        self.assertIn("data-discovery-confirm", settings)
        self.assertIn("form.matches('[data-discovery-confirm]')", javascript)
        self.assertIn("window.confirm(message)", javascript)

    def test_skeleton_respects_reduced_motion(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")
        self.assertIn(".discovery-results-skeleton", styles)
        self.assertIn("@keyframes discovery-skeleton-pulse", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)


if __name__ == "__main__":
    unittest.main()
