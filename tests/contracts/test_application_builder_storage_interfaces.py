"""Contracts for storage protocols and configuration-driven adapter selection."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"
STORAGE_MODULE = RESUME_TAYLOR_ROOT / "resume_tailor" / "storage.py"
WEB_STATE = RESUME_TAYLOR_ROOT / "resume_tailor" / "web_state.py"
APPLICATION_TRACKER = RESUME_TAYLOR_ROOT / "resume_tailor" / "application_tracker.py"
BUILDER_APP = RESUME_TAYLOR_ROOT / "app.py"
CONFIG = ROOT / "products" / "reunia" / "meeting_assistant" / "config.py"


class ApplicationBuilderStorageInterfaceTests(unittest.TestCase):
    def test_workflow_protocol_has_the_required_public_api(self) -> None:
        tree = ast.parse(STORAGE_MODULE.read_text(encoding="utf-8"))
        protocol = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "WorkflowStore"
        )
        methods = {
            node.name
            for node in protocol.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(
            methods,
            {"new_id", "load", "get", "save", "reset", "peek", "delete"},
        )

    def test_application_protocol_matches_sqlite_public_methods(self) -> None:
        def public_methods(path: Path, class_name: str) -> set[str]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            cls = next(
                node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            return {
                node.name
                for node in cls.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
            }

        self.assertEqual(
            public_methods(STORAGE_MODULE, "ApplicationStore"),
            public_methods(APPLICATION_TRACKER, "SQLiteApplicationStore"),
        )

    def test_in_memory_store_implements_explicit_save(self) -> None:
        web_state = WEB_STATE.read_text(encoding="utf-8")
        self.assertIn("def load(self, workflow_key: str)", web_state)
        self.assertIn("expected_version: int", web_state)
        self.assertIn("updated_by_request: str", web_state)
        self.assertIn("workflow_state_json_bytes(state)", web_state)
        self.assertNotIn("state: WorkflowState\n    touched_at", web_state)

    def test_builder_depends_on_protocols_and_factories(self) -> None:
        builder = BUILDER_APP.read_text(encoding="utf-8")
        self.assertIn("store: WorkflowStore = LocalProxy(", builder)
        self.assertIn("application_store: ApplicationStore = LocalProxy(", builder)
        self.assertIn("create_workflow_store(", builder)
        self.assertIn("create_application_store(", builder)
        self.assertIn("document_store=app.extensions", builder)
        self.assertNotIn("store: InMemoryWorkflowStore", builder)
        self.assertNotIn("application_store: SQLiteApplicationStore", builder)

    def test_blueprint_commits_mutable_workflow_state_through_save(self) -> None:
        builder = BUILDER_APP.read_text(encoding="utf-8")
        self.assertIn("@application_builder_bp.after_request", builder)
        self.assertIn("loaded_workflow = store.load(workflow_key)", builder)
        self.assertIn("g.workflow_initial_version = loaded_workflow.version", builder)
        self.assertIn("workflow_state_fingerprint(workflow_state)", builder)
        self.assertIn("expected_version=int(", builder)
        self.assertIn("updated_by_request=str(", builder)
        self.assertIn("g.workflow_request_id = normalize_workflow_request_id(", builder)
        self.assertIn("except WorkflowConflictError as exc:", builder)
        self.assertIn("status=409", builder)
        self.assertIn('g.workflow_state_deleted = True', builder)

    def test_configuration_exposes_both_backend_environment_variables(self) -> None:
        config = CONFIG.read_text(encoding="utf-8")
        self.assertIn("CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND", config)
        self.assertIn("CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND", config)
        self.assertIn("CAREER_BRIDGE_APPLICATIONS_TABLE_NAME", config)
        self.assertIn("CAREER_BRIDGE_WORKFLOWS_TABLE_NAME", config)
        self.assertIn("CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS", config)
        self.assertIn("CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS", config)
        self.assertIn("CAREER_BRIDGE_WORKFLOW_TTL_SECONDS", config)
        self.assertIn("CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND", config)
        self.assertIn("CAREER_BRIDGE_DOCUMENTS_BUCKET", config)
        self.assertIn("CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION", config)
        self.assertIn('"memory"', config)
        self.assertIn('"sqlite"', config)

    def test_default_factories_return_protocol_compatible_adapters(self) -> None:
        sys.path.insert(0, str(RESUME_TAYLOR_ROOT))
        try:
            from resume_tailor.storage import (
                ApplicationStore,
                WorkflowStore,
                create_application_store,
                create_workflow_store,
            )

            workflow = create_workflow_store(
                {"CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "memory"},
                lambda: object(),  # construction does not inspect the state yet
            )
            applications = create_application_store(
                {
                    "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "sqlite",
                    "APPLICATIONS_DB_PATH": ":memory:",
                }
            )
            self.assertIsInstance(workflow, WorkflowStore)
            self.assertIsInstance(applications, ApplicationStore)
            created = applications.create(
                "owner-local", company="Example", role="Engineer"
            )
            progressed = applications.update_builder_progress(
                "owner-local",
                created.id,
                workflow_step="setup",
                original_resume_key="career-bridge/users/hash/original.pdf",
            )
            self.assertEqual(
                progressed.original_resume_key,
                "career-bridge/users/hash/original.pdf",
            )
            applications._connection.close()
        finally:
            if sys.path and sys.path[0] == str(RESUME_TAYLOR_ROOT):
                sys.path.pop(0)

    def test_invalid_backend_names_are_rejected(self) -> None:
        sys.path.insert(0, str(RESUME_TAYLOR_ROOT))
        try:
            from resume_tailor.storage import (
                StorageBackendConfigurationError,
                configured_application_backend,
                configured_workflow_backend,
            )

            with self.assertRaises(StorageBackendConfigurationError):
                configured_workflow_backend(
                    {"CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "redis"}
                )
            with self.assertRaises(StorageBackendConfigurationError):
                configured_application_backend(
                    {"CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "postgres"}
                )
        finally:
            if sys.path and sys.path[0] == str(RESUME_TAYLOR_ROOT):
                sys.path.pop(0)

    def test_dynamodb_workflow_factory_is_available_and_validates_table_name(self) -> None:
        sys.path.insert(0, str(RESUME_TAYLOR_ROOT))
        try:
            from resume_tailor.storage import (
                StorageBackendConfigurationError,
                WorkflowStore,
                create_workflow_store,
            )

            class FakeObjectStore:
                def put(self, object_key, content, content_type, *, metadata=None):
                    pass

                def get(self, object_key):
                    return b""

                def delete(self, object_key):
                    pass

            with self.assertRaisesRegex(StorageBackendConfigurationError, "WORKFLOWS_TABLE"):
                create_workflow_store(
                    {"CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "dynamodb"},
                    lambda: object(),
                    document_store=FakeObjectStore(),
                )
            workflow = create_workflow_store(
                {
                    "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "dynamodb",
                    "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                    "AWS_REGION": "us-west-2",
                },
                lambda: object(),
                document_store=FakeObjectStore(),
            )
            self.assertIsInstance(workflow, WorkflowStore)
        finally:
            if sys.path and sys.path[0] == str(RESUME_TAYLOR_ROOT):
                sys.path.pop(0)

    def test_dynamodb_application_factory_returns_protocol_adapter(self) -> None:
        sys.path.insert(0, str(RESUME_TAYLOR_ROOT))
        try:
            from resume_tailor.storage import ApplicationStore, create_application_store

            class FakeObjectStore:
                def put(self, object_key, content, content_type, *, metadata=None):
                    pass

                def get(self, object_key):
                    return b""

                def delete(self, object_key):
                    pass

            applications = create_application_store(
                {
                    "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "dynamodb",
                    "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "career-bridge-applications",
                    "AWS_REGION": "us-west-2",
                },
                document_store=FakeObjectStore(),
            )
            self.assertIsInstance(applications, ApplicationStore)
        finally:
            if sys.path and sys.path[0] == str(RESUME_TAYLOR_ROOT):
                sys.path.pop(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
