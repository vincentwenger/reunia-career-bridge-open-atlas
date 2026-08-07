"""Regression contract for degraded Admin Analytics source handling.

The Admin Analytics page must remain usable when one DynamoDB-backed source is
briefly unavailable or its IAM permissions are incomplete. The service should
return a partial dashboard with explicit source availability instead of letting
one repository exception become a page-wide HTTP 500.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.source_aggregates import ADMIN_ANALYTICS_SOURCE

ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = ADMIN_ANALYTICS_SOURCE
SCRIPT_PATH = ROOT / "products" / "reunia" / "static" / "js" / "pages" / "admin-analytics.js"


class AdminAnalyticsResilienceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = SERVICE_PATH.read_text(encoding="utf-8")
        cls.script = SCRIPT_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.service)

    def test_dashboard_catches_core_repository_failures(self) -> None:
        self.assertIn('core_sources["activity"] = False', self.service)
        self.assertIn('core_sources["users"] = False', self.service)
        self.assertIn('usage["sources"].update(core_sources)', self.service)
        self.assertIn(
            "Could not load visitor and session activity for Admin Analytics",
            self.service,
        )
        self.assertIn("Could not load registered users for Admin Analytics", self.service)

    def test_degraded_core_dashboard_is_not_cached(self) -> None:
        self.assertIn("core_sources_available = all(core_sources.values())", self.service)
        self.assertIn(
            "if self._cacheable and cache is not None and core_sources_available:",
            self.service,
        )

    def test_cache_failure_cannot_break_dashboard(self) -> None:
        self.assertIn(
            "Could not read the Admin Analytics cache; loading live data",
            self.service,
        )
        self.assertIn("Could not write the Admin Analytics cache", self.service)

    def test_incident_endpoints_report_missing_user_source(self) -> None:
        self.assertGreaterEqual(self.service.count('"users_available": users_available'), 2)
        self.assertIn("users: data.users_available !== false", self.script)
        self.assertIn("registered-user details", self.script)

    def test_user_rows_do_not_depend_on_retired_live_assistance_access(self) -> None:
        self.assertNotIn("live_interview_assistance_access", self.service)
        self.assertIn('"groups": list(user.get("groups")', self.service)

    def test_dashboard_warning_names_core_sources(self) -> None:
        self.assertIn("activity: 'visitor and session activity'", self.script)
        self.assertIn("users: 'registered users'", self.script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
