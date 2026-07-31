"""Runtime regression tests for the unified Réunia/Application Builder app.

Run from the repository root with:

    python -m unittest -v tests.integration.test_application_builder
"""

from __future__ import annotations

import importlib.util
import os
import re
import unittest
from unittest.mock import patch

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
    from flask import url_for

    from app import create_application
    from products.resume_taylor.app import init_application_builder
else:
    url_for = None
    create_application = None
    init_application_builder = None


@unittest.skipUnless(_RUNTIME_AVAILABLE, _SKIP_REASON)
class ApplicationBuilderIntegrationTests(unittest.TestCase):
    """Validate that the Builder behaves as a Blueprint inside Réunia."""

    def setUp(self) -> None:
        self.app = create_application("testing")
        self.app.config.update(
            CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            SERVER_NAME="localhost",
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "architecture-validation-user"
            session["email"] = "validation@example.test"
            session["full_name"] = "Architecture Validation"

    def test_builder_exception_uses_reunia_recovery_page(self) -> None:
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]
        with patch.object(
            application_store,
            "list_for_owner",
            side_effect=RuntimeError("forced validation failure"),
        ):
            response = self.client.get("/applications/")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 500)
        self.assertIn("System Error", body)
        self.assertIn("Réunia is temporarily unavailable", body)
        self.assertIn("Open Help &amp; Support", body)
        self.assertRegex(body, r"REQ-[A-F0-9]{12}")

    def test_missing_builder_url_uses_reunia_404_page(self) -> None:
        response = self.client.get("/applications/route-that-does-not-exist")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 404)
        self.assertIn("Page Not Found", body)
        self.assertIn("The page may have moved", body)
        self.assertIn("Open Help &amp; Support", body)

    def test_builder_forms_use_shared_reunia_csrf_token(self) -> None:
        page = self.client.get("/applications/")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)

        meta_match = re.search(
            r'<meta name="csrf-token" content="([^"]+)">', body
        )
        self.assertIsNotNone(meta_match)
        token = meta_match.group(1)
        self.assertIn(
            f'name="csrf_token" value="{token}"',
            body,
        )

        with self.client.session_transaction() as session:
            self.assertEqual(session.get("_csrf_token"), token)
            self.assertNotIn("csrf_token", session)

        rejected = self.client.post("/applications/reset")
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("Request Expired", rejected.get_data(as_text=True))

        accepted = self.client.post(
            "/applications/reset",
            data={"csrf_token": token},
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertIn("/applications/", accepted.headers["Location"])

    def test_authentication_session_is_shared_across_shell_and_builder(self) -> None:
        shell_response = self.client.get("/app")
        builder_response = self.client.get("/applications/")

        self.assertEqual(shell_response.status_code, 200)
        self.assertEqual(builder_response.status_code, 200)
        self.assertNotIn("/login.html", shell_response.headers.get("Location", ""))
        self.assertNotIn("/login.html", builder_response.headers.get("Location", ""))

        with self.client.session_transaction() as session:
            self.assertEqual(
                session.get("application_owner_id"),
                "architecture-validation-user",
            )
            self.assertEqual(
                session.get("workflow_sid"),
                "architecture-validation-user",
            )

    def test_builder_and_shared_static_assets_resolve(self) -> None:
        builder_css = self.client.get("/applications/static/styles.css")
        builder_js = self.client.get("/applications/static/app.js")
        shared_css = self.client.get("/static/css/base.css")

        self.assertEqual(builder_css.status_code, 200)
        self.assertEqual(builder_js.status_code, 200)
        self.assertEqual(shared_css.status_code, 200)
        self.assertIn("text/css", builder_css.content_type)
        self.assertIn("javascript", builder_js.content_type)

        with self.app.test_request_context():
            self.assertEqual(
                url_for("application_builder.static", filename="styles.css"),
                "/applications/static/styles.css",
            )

    def test_health_exposes_application_builder_storage_limitations(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "status": "ok",
                "services": ["reunia", "application-builder"],
                "application_builder": {
                    "workflow_storage": "memory",
                    "application_storage": "dynamodb",
                    "document_storage": "local",
                    "durability": "mixed",
                    "multi_worker_safe": False,
                    "multi_node_safe": False,
                },
            },
        )

    def test_startup_logs_application_builder_persistence_warning(self) -> None:
        with self.assertLogs(level="WARNING") as captured:
            create_application("testing")

        warning = "\n".join(captured.output)
        self.assertIn("Application Builder storage configured", warning)
        self.assertIn("workflow=memory", warning)
        self.assertIn("applications=dynamodb", warning)
        self.assertIn("documents=local", warning)
        self.assertIn("not fully durable", warning)

    def test_builder_stores_are_initialized_once_per_app(self) -> None:
        workflow_store = self.app.extensions["career_bridge_workflow_store"]
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]

        init_application_builder(self.app)
        self.assertIs(
            workflow_store,
            self.app.extensions["career_bridge_workflow_store"],
        )
        self.assertIs(
            application_store,
            self.app.extensions["career_bridge_application_store"],
        )

        second_app = create_application("testing")
        self.assertIsNot(
            workflow_store,
            second_app.extensions["career_bridge_workflow_store"],
        )
        self.assertIsNot(
            application_store,
            second_app.extensions["career_bridge_application_store"],
        )

    def test_builder_routes_and_urls_are_namespaced(self) -> None:
        builder_rules = [
            rule
            for rule in self.app.url_map.iter_rules()
            if rule.rule.startswith("/applications")
        ]
        self.assertGreater(len(builder_rules), 1)
        self.assertTrue(
            all(
                rule.endpoint.startswith("application_builder.")
                for rule in builder_rules
            )
        )

        with self.app.test_request_context():
            self.assertEqual(
                url_for("application_builder.index"),
                "/applications/",
            )
            self.assertEqual(
                url_for("application_builder.interview_preparation_workspace"),
                "/applications/interview-preparation",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
