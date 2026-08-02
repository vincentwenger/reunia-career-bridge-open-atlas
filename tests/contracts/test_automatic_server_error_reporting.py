"""Static contract for automatic 5xx reporting to administrators."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ERROR_HANDLERS = ROOT / "products/reunia/meeting_assistant/utils/error_handlers.py"
REPORTER = ROOT / "products/reunia/meeting_assistant/services/server_error_reporting_service.py"
ANALYTICS = ROOT / "products/reunia/meeting_assistant/services/admin_analytics_service.py"
ADMIN_JS = ROOT / "products/reunia/static/js/pages/admin-analytics.js"
BUILDER = ROOT / "products/resume_taylor/app.py"


class AutomaticServerErrorReportingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.error_handlers = ERROR_HANDLERS.read_text(encoding="utf-8")
        cls.reporter = REPORTER.read_text(encoding="utf-8")
        cls.analytics = ANALYTICS.read_text(encoding="utf-8")
        cls.admin_js = ADMIN_JS.read_text(encoding="utf-8")
        cls.builder = BUILDER.read_text(encoding="utf-8")
        ast.parse(cls.error_handlers)
        ast.parse(cls.reporter)
        ast.parse(cls.analytics)
        ast.parse(cls.builder)

    def test_unhandled_and_explicit_5xx_responses_are_reported(self) -> None:
        self.assertIn("@app.errorhandler(Exception)", self.error_handlers)
        self.assertIn("@app.after_request", self.error_handlers)
        self.assertIn("response.status_code < 500", self.error_handlers)
        self.assertGreaterEqual(self.error_handlers.count("_report_server_error("), 5)

    def test_reports_are_written_to_both_admin_destinations(self) -> None:
        self.assertIn('current_app.extensions.get("support_repository")', self.reporter)
        self.assertIn('current_app.extensions.get("analytics_repository")', self.reporter)
        self.assertIn('"source": "automatic_server_error"', self.reporter)
        self.assertIn('"metric": "server_error"', self.reporter)
        self.assertIn('"support_request_id": support_request_id', self.reporter)

    def test_job_discovery_is_identified_in_diagnostics(self) -> None:
        self.assertIn('("/applications/job-discovery", "job_discovery", "Job Discovery")', self.reporter)
        self.assertIn('"job_discovery": "Job Discovery"', self.analytics)

    def test_admin_incidents_include_sanitized_traceback_and_support_link(self) -> None:
        self.assertIn('"technical_details": str(item.get("technical_details") or "")', self.analytics)
        self.assertIn('"automatic_server_error"', self.analytics)
        self.assertIn("Sanitized technical details", self.admin_js)
        self.assertIn("data-open-support-request", self.admin_js)

    def test_reporter_does_not_collect_request_bodies_or_auth_headers(self) -> None:
        self.assertNotIn("request.get_json", self.reporter)
        self.assertNotIn("request.form", self.reporter)
        self.assertNotIn("request.cookies", self.reporter)
        self.assertIn("[REDACTED]", self.reporter)

    def test_builder_storage_errors_preserve_original_exception(self) -> None:
        self.assertIn("ServerErrorReportingService().report_safely(", self.builder)
        self.assertIn("status_code=503", self.builder)


if __name__ == "__main__":
    unittest.main(verbosity=2)
