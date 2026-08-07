"""Playwright coverage proving frontend behavior remains compatible with CSP.

Run from the repository root after installing ``requirements-dev.txt`` and
Playwright Chromium:

    python -m unittest -v tests.browser.test_csp_compatibility
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.runtime_dependencies import (
    missing_runtime_dependencies,
    playwright_chromium_executable,
)

_MISSING_RUNTIME_DEPENDENCIES = tuple(missing_runtime_dependencies())
_CHROMIUM_EXECUTABLE = playwright_chromium_executable()
_RUNTIME_AVAILABLE = not _MISSING_RUNTIME_DEPENDENCIES
_SKIP_REASON = (
    "Missing runtime dependencies: " + ", ".join(_MISSING_RUNTIME_DEPENDENCIES)
    if _MISSING_RUNTIME_DEPENDENCIES
    else ""
)


@unittest.skipUnless(_RUNTIME_AVAILABLE, _SKIP_REASON)
class CSPBrowserCompatibilityTests(unittest.TestCase):
    """Exercise real CSP-protected pages and Job Discovery interactions."""

    def test_key_pages_filters_and_confirmation_work_under_csp(self) -> None:
        os.environ["APP_ENV"] = "testing"
        os.environ.setdefault("OPENAI_API_KEY", "csp-browser-test-key")

        from job_discovery.models import CompanySource, JobSourceType
        from job_discovery.public_catalog import SHARED_CATALOG_SOURCE_OWNER_ID
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server

        app_entry = importlib.import_module("app")
        app = app_entry.create_application("testing")
        app.config.update(
            TESTING=True,
            CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=True,
            SERVER_NAME=None,
        )
        owner_id = "csp-browser-user"
        source_id = "csp-source"
        discovery_store = app.extensions["career_bridge_job_discovery_store"]
        discovery_store.put_company_source(
            CompanySource(
                id=source_id,
                owner_id=SHARED_CATALOG_SOURCE_OWNER_ID,
                company_name="CSP Test Company",
                careers_url="https://boards.greenhouse.io/csptest",
                source_type=JobSourceType.GREENHOUSE,
                source_identifier="csptest",
            )
        )

        server = make_server("127.0.0.1", 0, app, threaded=True)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        try:
            client = app.test_client()
            with client.session_transaction() as session:
                session["user_id"] = owner_id
                session["email"] = "csp.browser@example.test"
                session["full_name"] = "CSP Browser"
                session["is_admin"] = True
            response = client.get("/applications/job-discovery?view=settings")
            self.assertEqual(200, response.status_code)
            session_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
            session_cookie = client.get_cookie(session_cookie_name)
            self.assertIsNotNone(session_cookie)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=_CHROMIUM_EXECUTABLE,
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context()
                context.add_cookies(
                    [
                        {
                            "name": session_cookie_name,
                            "value": session_cookie.value,
                            "url": base_url,
                        }
                    ]
                )
                page = context.new_page()
                csp_console_errors: list[str] = []
                page.on(
                    "console",
                    lambda message: csp_console_errors.append(message.text)
                    if message.type == "error"
                    and (
                        "content security policy" in message.text.lower()
                        or "refused to execute inline" in message.text.lower()
                        or "refused to load" in message.text.lower()
                    )
                    else None,
                )

                key_pages = (
                    "/app",
                    "/career-profile",
                    "/applications/career-translation",
                    "/applications/job-discovery?render_results=1",
                    "/mock-interview",
                    "/career-action-plan",
                    "/progress",
                )
                for route in key_pages:
                    with self.subTest(route=route):
                        navigation = page.goto(
                            base_url + route,
                            wait_until="domcontentloaded",
                        )
                        self.assertIsNotNone(navigation)
                        self.assertEqual(200, navigation.status)
                        policy = navigation.headers.get("content-security-policy", "")
                        self.assertRegex(
                            policy,
                            r"(?:^|;\s*)script-src 'self' 'nonce-[^']+'(?:;|$)",
                        )
                        self.assertNotIn(
                            "script-src 'self' 'unsafe-inline'", policy
                        )
                        bad_scripts = page.eval_on_selector_all(
                            "script:not([src])",
                            """scripts => scripts.filter(script => {
                                const type = (script.type || '').trim().toLowerCase().split(';', 1)[0];
                                const executable = ['', 'module', 'text/javascript', 'application/javascript', 'text/ecmascript', 'application/ecmascript'].includes(type);
                                return executable && !script.nonce;
                            }).map(script => script.outerHTML.slice(0, 160))""",
                        )
                        self.assertEqual([], bad_scripts)

                page.goto(
                    base_url + "/applications/job-discovery?render_results=1",
                    wait_until="domcontentloaded",
                )
                page.wait_for_selector(
                    '[data-discovery-filter-form] select[name="min_fit"]'
                )
                page.select_option(
                    '[data-discovery-filter-form] select[name="min_fit"]', "70"
                )
                page.wait_for_function(
                    "new URL(window.location.href).searchParams.get('min_fit') === '70'"
                )
                self.assertEqual(
                    "70",
                    page.locator(
                        '[data-discovery-filter-form] select[name="min_fit"]'
                    ).input_value(),
                )

                page.goto(
                    base_url + "/applications/job-discovery?view=settings",
                    wait_until="domcontentloaded",
                )
                source = page.locator(
                    f'[data-discovery-source-id="{source_id}"]'
                )
                source.wait_for()
                source.locator("summary").click()

                dismissed_messages: list[str] = []

                def dismiss_dialog(dialog) -> None:
                    dismissed_messages.append(dialog.message)
                    dialog.dismiss()

                page.once("dialog", dismiss_dialog)
                source.get_by_role("button", name="Remove", exact=True).click()
                page.wait_for_timeout(100)
                self.assertEqual(1, len(dismissed_messages))
                self.assertIn("Remove this company source?", dismissed_messages[0])
                self.assertIsNotNone(
                    discovery_store.get_company_source(
                        SHARED_CATALOG_SOURCE_OWNER_ID, source_id
                    )
                )

                accepted_messages: list[str] = []

                def accept_dialog(dialog) -> None:
                    accepted_messages.append(dialog.message)
                    dialog.accept()

                page.once("dialog", accept_dialog)
                with page.expect_navigation(wait_until="domcontentloaded"):
                    source.get_by_role("button", name="Remove", exact=True).click()
                self.assertEqual(1, len(accepted_messages))
                self.assertIsNone(
                    discovery_store.get_company_source(
                        SHARED_CATALOG_SOURCE_OWNER_ID, source_id
                    )
                )
                self.assertEqual(0, page.locator(
                    f'[data-discovery-source-id="{source_id}"]'
                ).count())

                self.assertEqual([], csp_console_errors, "\n".join(csp_console_errors))
                context.close()
                browser.close()
        finally:
            server.shutdown()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
