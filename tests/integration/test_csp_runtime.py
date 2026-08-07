"""Runtime CSP/header compatibility checks for key Career Bridge pages."""

from __future__ import annotations

import importlib.util
import os
import re
import unittest

from tests.csp_helpers import inspect_rendered_html

os.environ.setdefault("APP_ENV", "testing")

_REQUIRED_MODULES = (
    "flask",
    "dotenv",
    "redis",
    "openai",
    "docx",
    "reportlab",
    "openpyxl",
    "pypdf",
    "xlrd",
)
_MISSING_MODULES = tuple(
    name for name in _REQUIRED_MODULES if importlib.util.find_spec(name) is None
)
_RUNTIME_AVAILABLE = not _MISSING_MODULES
_SKIP_REASON = "Missing runtime dependencies: " + ", ".join(_MISSING_MODULES)

if _RUNTIME_AVAILABLE:
    from app import create_application
else:
    create_application = None


@unittest.skipUnless(_RUNTIME_AVAILABLE, _SKIP_REASON)
class CSPRuntimeCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_application("testing")
        self.app.config.update(
            TESTING=True,
            CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=True,
            SERVER_NAME="localhost",
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "csp-runtime-user"
            session["email"] = "csp.runtime@example.test"
            session["full_name"] = "CSP Runtime"
            session["is_admin"] = True

    def test_key_pages_render_under_nonce_based_script_policy(self) -> None:
        routes = (
            "/app",
            "/career-profile",
            "/applications/career-translation",
            "/applications/job-discovery",
            "/applications/?tab=tailoring&stage=setup",
            "/mock-interview",
            "/interview-review",
            "/career-action-plan",
            "/progress",
        )
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(200, response.status_code)
                policy = response.headers.get("Content-Security-Policy", "")
                nonce_match = re.search(
                    r"(?:^|;\s*)script-src 'self' 'nonce-([^']+)'(?:;|$)",
                    policy,
                )
                self.assertIsNotNone(nonce_match, policy)
                self.assertNotIn("'unsafe-inline'", nonce_match.group(0))

                parsed = inspect_rendered_html(response.get_data(as_text=True))
                self.assertEqual([], parsed.inline_event_handlers)
                self.assertTrue(
                    all(
                        nonce == nonce_match.group(1)
                        for nonce in parsed.inline_executable_script_nonces
                    ),
                    parsed.inline_executable_script_nonces,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
