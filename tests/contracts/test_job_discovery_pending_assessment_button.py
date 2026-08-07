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


class JobDiscoveryPendingAssessmentButtonContractTests(unittest.TestCase):
    def test_user_specific_pending_assessment_route_is_bounded(self) -> None:
        source = OPERATION_ROUTES.read_text(encoding="utf-8")
        operations = OPERATIONS.read_text(encoding="utf-8")
        combined = source + operations
        self.assertIn("post('/discovery/assess/pending')", source)
        self.assertIn("def assess_pending_discovered_jobs", source)
        self.assertIn("_discovery_assessment_run_limit", combined)
        self.assertIn("assess_existing_jobs", operations)
        self.assertIn("skip_job_keys", operations)
        self.assertIn("_restore_discovery_fit_snapshots_from_cached_analysis", operations)
        self.assertIn("analyze_new_jobs=False", operations)
        self.assertIn("durable cached analyses", operations)
        self.assertNotIn(
            "_require_job_catalog_manager()\n        owner_id = _application_owner_id()\n        payload, wants_json = _assessment_request_payload()",
            source,
        )

    def test_results_panel_separates_refresh_from_assessment(self) -> None:
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )
        self.assertIn("Refresh jobs for everyone", template)
        self.assertIn("Assess next {{ discovery_assessment_run_limit }} jobs", template)
        self.assertIn("Assess all remaining", template)
        self.assertIn('data-assessment-scope="limited"', template)
        self.assertIn('data-assessment-scope="all"', template)
        self.assertIn("data-discovery-assessment-run", template)
        self.assertIn("data-discovery-assessment-progress", template)
        self.assertIn("discovery_result_summary.pending_count", template)

    def test_browser_queues_one_durable_background_job(self) -> None:
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertIn("data-discovery-assessment-run", javascript)
        self.assertIn("Queue AI-backed Job Discovery assessment in durable storage", javascript)
        self.assertIn("status_url", javascript)
        self.assertIn("cancel_url", javascript)
        self.assertIn("retry_url", javascript)
        self.assertIn("pollUntilTerminal", javascript)
        self.assertIn("You may leave this page", javascript)
        self.assertIn("Stop after current job", javascript)
        self.assertNotIn("batch_size: 1", javascript)
        self.assertNotIn("while (!stopRequested", javascript)

    def test_route_only_enqueues_and_returns_accepted(self) -> None:
        route = function_source(OPERATION_ROUTES, "assess_pending_discovered_jobs")
        self.assertIn("AsyncJob.queued", route)
        self.assertIn("async_job_store.create(job)", route)
        self.assertIn("return jsonify(response), 202", route)
        self.assertNotIn("assess_existing_jobs", route)
        self.assertNotIn("ResumeAI(", route)


if __name__ == "__main__":
    unittest.main()
