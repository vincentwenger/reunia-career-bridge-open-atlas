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


class JobDiscoveryPendingAssessmentButtonContractTests(unittest.TestCase):
    def test_user_specific_pending_assessment_route_is_bounded(self) -> None:
        source = APP.read_text(encoding="utf-8")
        self.assertIn('post("/discovery/assess/pending")', source)
        self.assertIn("def assess_pending_discovered_jobs", source)
        self.assertIn("_discovery_assessment_batch_size", source)
        self.assertIn("assess_existing_jobs", source)
        self.assertIn("skip_job_keys", source)
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

    def test_browser_runs_multiple_bounded_assessment_requests(self) -> None:
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertIn("data-discovery-assessment-run", javascript)
        self.assertIn("while (!stopRequested && !completed && attemptedTotal < runLimit)", javascript)
        self.assertIn("assessAllRemaining", javascript)
        self.assertIn("Math.max(initialPendingCount, assessmentRunLimit)", javascript)
        self.assertIn("skip_job_keys: skippedJobKeys", javascript)
        self.assertIn("assessmentRunLimit", javascript)
        self.assertIn("batch_size: 1", javascript)
        self.assertNotIn("Math.min(3, runLimit - attemptedTotal)", javascript)
        self.assertIn("Stop after current batch", javascript)
        self.assertIn("transientAssessmentStatuses = new Set([502, 503, 504])", javascript)
        self.assertIn("maxTransientAssessmentRetries = 2", javascript)
        self.assertIn("requestAssessmentBatch", javascript)
        self.assertIn("Pending-job assessment paused", javascript)
        self.assertIn("completed assessments were preserved", javascript)
        self.assertIn("durableAttemptedCount", javascript)
        self.assertIn("window.location.reload()", javascript)


if __name__ == "__main__":
    unittest.main()
