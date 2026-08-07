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
SCHEDULING = ROOT / "job_discovery" / "scheduling.py"


class JobDiscoverySchedulingContractTests(unittest.TestCase):
    def test_catalog_manager_has_explicit_refresh_button_and_post_route(self) -> None:
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )
        app = OPERATION_ROUTES.read_text(encoding="utf-8")
        operations = OPERATIONS.read_text(encoding="utf-8")
        self.assertIn("Refresh jobs", template)
        self.assertIn("refresh_discovered_jobs", template)
        self.assertIn("post('/discovery/refresh')", app)
        self.assertIn("JobDiscoveryService(store=discovery_store)", operations)
        self.assertIn("csrf_token()", template)


    def test_shared_catalog_schedule_is_persisted_and_external_runner_honors_it(self) -> None:
        settings = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        app = SOURCE_ROUTES.read_text(encoding="utf-8")
        scheduling = SCHEDULING.read_text(encoding="utf-8")
        for label in ("Frequency", "Weekly day", "Local hour", "Time zone", "Save refresh schedule"):
            self.assertIn(label, settings)
        self.assertIn("post('/discovery/schedule')", app)
        self.assertIn("put_scan_schedule", app)
        self.assertIn("run_scheduled_owners", scheduling)
        self.assertIn("--scheduled", scheduling)

    def test_no_scheduler_is_started_inside_flask_or_gunicorn(self) -> None:
        combined = application_builder_source().casefold()
        for forbidden in (
            "apscheduler",
            "backgroundscheduler",
            "schedule.every",
            "threading.timer",
        ):
            self.assertNotIn(forbidden, combined)

    def test_external_entry_points_support_lambda_container_or_cron(self) -> None:
        source = SCHEDULING.read_text(encoding="utf-8")
        self.assertIn("def lambda_handler", source)
        self.assertIn("def main", source)
        self.assertIn("ExternalJobDiscoveryRunner", source)
        self.assertNotIn("from flask", source)
        self.assertIn("in-memory store would discard results", source.casefold())


if __name__ == "__main__":
    unittest.main()
