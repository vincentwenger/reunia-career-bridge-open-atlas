from __future__ import annotations

import unittest
from pathlib import Path

from tests.application_builder_source import application_builder_source


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"


class JobDiscoveryServerTimingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = application_builder_source()

    def test_page_timing_is_scoped_to_the_job_discovery_get(self) -> None:
        self.assertIn("def _job_discovery_page_timing_active()", self.source)
        self.assertIn('request.method == "GET"', self.source)
        self.assertIn(
            '== "application_builder.job_discovery_workspace"',
            self.source,
        )

    def test_server_timing_header_contains_total_and_phase_metrics(self) -> None:
        self.assertIn('response.headers["Server-Timing"]', self.source)
        self.assertIn("jd_total;dur=", self.source)
        for metric in (
            "jd_context",
            "jd_workflow",
            "jd_profile",
            "jd_sources",
            "jd_preferences",
            "jd_result_profile",
            "jd_result_index",
            "jd_template",
            "jd_persist",
        ):
            self.assertIn(f'"{metric}"', self.source)

    def test_phase_log_is_structured_and_has_a_slow_request_threshold(self) -> None:
        self.assertIn("Job Discovery timing request_id=%s", self.source)
        self.assertIn("owner_scope=%s", self.source)
        self.assertIn("index_state=%s", self.source)
        self.assertIn(
            "CAREER_BRIDGE_JOB_DISCOVERY_SLOW_REQUEST_MS",
            self.source,
        )
        self.assertIn("current_app.logger.warning", self.source)
        self.assertIn("current_app.logger.info", self.source)

    def test_timing_finalizer_runs_after_workflow_persistence(self) -> None:
        timing_registration = self.source.index(
            "def add_job_discovery_server_timing(response: Response)"
        )
        persistence_registration = self.source.index(
            "def persist_workflow_state(response: Response)"
        )
        self.assertLess(timing_registration, persistence_registration)
        self.assertIn(
            '"jd_persist", persist_started_at, "Workflow persistence"',
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
