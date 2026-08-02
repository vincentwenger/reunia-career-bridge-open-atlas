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
JAVASCRIPT = ROOT / "products" / "resume_taylor" / "static" / "app.js"


class JobDiscoveryResultIndexPrebuildContractTests(unittest.TestCase):
    def test_page_read_does_not_rebuild_a_missing_or_stale_index(self) -> None:
        source = APP.read_text(encoding="utf-8")
        function_start = source.index("    def _discovery_result_cards(")
        function_end = source.index("    def _prebuild_discovery_result_index(", function_start)
        function_source = source[function_start:function_end]

        self.assertIn("rebuild_if_needed: bool = False", function_source)
        self.assertIn("if not rebuild_if_needed:", function_source)
        self.assertIn('"index_stale": True', function_source)
        self.assertIn("applications = application_store.list_for_owner(owner_id)", function_source)
        self.assertLess(
            function_source.index("if not rebuild_if_needed:"),
            function_source.index("applications = application_store.list_for_owner(owner_id)"),
        )

        route_start = source.index("    def job_discovery_workspace():")
        route_end = source.index("    @application_builder_bp.get(\n        \"/discovery/jobs/", route_start)
        route_source = source[route_start:route_end]
        self.assertNotIn("rebuild_if_needed=True", route_source)

    def test_explicit_prebuild_endpoint_materializes_the_index(self) -> None:
        source = APP.read_text(encoding="utf-8")
        self.assertIn('@application_builder_bp.post("/discovery/result-index/prebuild")', source)
        self.assertIn("def prebuild_discovery_result_index():", source)
        self.assertIn("rebuild_if_needed=True", source)
        self.assertIn("_prebuild_discovery_result_index(owner_id)", source)

    def test_browser_prebuilds_once_after_bulk_mutations(self) -> None:
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )
        javascript = JAVASCRIPT.read_text(encoding="utf-8")

        self.assertIn("data-discovery-result-index-url", template)
        self.assertIn("data-discovery-result-index-stale", template)
        self.assertIn("Updating job results", template)
        self.assertIn("const prebuildDiscoveryResultIndex", javascript)
        self.assertIn("discoveryResultIndexPrebuildPromise", javascript)
        self.assertGreaterEqual(
            javascript.count("await prebuildDiscoveryResultIndex();"),
            3,
        )


if __name__ == "__main__":
    unittest.main()
