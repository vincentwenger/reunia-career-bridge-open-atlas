from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
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
JAVASCRIPT = ROOT / "products" / "resume_taylor" / "static" / "app.js"
STYLES = ROOT / "products" / "resume_taylor" / "static" / "styles.css"


class JobDiscoveryAsyncResultContractTests(unittest.TestCase):
    def test_initial_get_renders_a_skeleton_and_defers_result_reads(self) -> None:
        source = APP.read_text(encoding="utf-8")
        shell = SHELL.read_text(encoding="utf-8")
        route_start = source.index("    def job_discovery_workspace():")
        route_end = source.index(
            '    @application_builder_bp.get("/job-discovery/results.json")',
            route_start,
        )
        route = source[route_start:route_end]

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
        source = APP.read_text(encoding="utf-8")
        route_start = source.index("    def job_discovery_results_json():")
        route_end = source.index(
            "    @application_builder_bp.get(\n        \"/discovery/jobs/",
            route_start,
        )
        route = source[route_start:route_end]

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
        self.assertIn("this.form.requestSubmit()", content)

    def test_skeleton_respects_reduced_motion(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")
        self.assertIn(".discovery-results-skeleton", styles)
        self.assertIn("@keyframes discovery-skeleton-pulse", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)


if __name__ == "__main__":
    unittest.main()
