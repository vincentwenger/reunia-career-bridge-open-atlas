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
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_results.html"
)
JAVASCRIPT = ROOT / "products" / "resume_taylor" / "static" / "app-job-discovery.js"


class JobDiscoveryResultIndexPrebuildContractTests(unittest.TestCase):
    def test_page_read_does_not_rebuild_a_missing_or_stale_index(self) -> None:
        cards_source = function_source(RESULT_QUERY, "_discovery_result_cards")

        self.assertIn("rebuild_if_needed: bool = False", cards_source)
        self.assertIn("if not rebuild_if_needed:", cards_source)
        self.assertIn('"index_stale": True', cards_source)
        self.assertIn("applications = application_store.list_for_owner(owner_id)", cards_source)
        self.assertLess(
            cards_source.index("if not rebuild_if_needed:"),
            cards_source.index("applications = application_store.list_for_owner(owner_id)"),
        )

        route_source = function_source(
            WORKSPACE_VIEW, "render_job_discovery_workspace"
        )
        self.assertNotIn("rebuild_if_needed=True", route_source)

    def test_explicit_prebuild_endpoint_materializes_the_index(self) -> None:
        source = OPERATION_ROUTES.read_text(encoding="utf-8")
        self.assertIn("@_routes.post('/discovery/result-index/prebuild')", source)
        self.assertIn("def prebuild_discovery_result_index():", source)
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
