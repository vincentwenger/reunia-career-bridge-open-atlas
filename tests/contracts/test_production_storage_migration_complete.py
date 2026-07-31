"""Final production-persistence migration contract.

This suite prevents the application from drifting back to process-local workflow
state or non-DynamoDB application records in a normal production deployment.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "products" / "reunia" / "meeting_assistant" / "config.py"
FACTORY = ROOT / "products" / "reunia" / "meeting_assistant" / "__init__.py"
BUILDER = ROOT / "products" / "resume_taylor" / "app.py"
DYNAMODB = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "dynamodb_storage.py"
)
SERIALIZATION = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "workflow_serialization.py"
)


def _production_backend_defaults() -> dict[str, str]:
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    production = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProductionConfig"
    )
    defaults: dict[str, str] = {}
    for statement in production.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name):
            continue
        for call in ast.walk(statement.value):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "getenv"
                and len(call.args) >= 2
                and isinstance(call.args[1], ast.Constant)
                and isinstance(call.args[1].value, str)
            ):
                defaults[target.id] = call.args[1].value
                break
    return defaults


class ProductionStorageMigrationCompleteTests(unittest.TestCase):
    def test_normal_production_defaults_are_durable(self) -> None:
        defaults = _production_backend_defaults()
        self.assertEqual(
            defaults["CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND"], "dynamodb"
        )
        self.assertEqual(
            defaults["CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND"], "dynamodb"
        )
        self.assertEqual(defaults["CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND"], "s3")

    def test_builder_runtime_uses_interfaces_and_factories_not_concrete_local_stores(self) -> None:
        source = BUILDER.read_text(encoding="utf-8")
        self.assertIn("create_workflow_store(", source)
        self.assertIn("create_application_store(", source)
        self.assertIn("store: WorkflowStore", source)
        self.assertIn("application_store: ApplicationStore", source)
        self.assertNotIn("from resume_tailor.web_state import InMemoryWorkflowStore", source)
        self.assertNotIn("application_database_path", source)

    def test_application_records_use_dynamodb_metadata_and_s3_document_keys(self) -> None:
        source = DYNAMODB.read_text(encoding="utf-8")
        self.assertIn("class DynamoDBApplicationStore", source)
        self.assertIn("resume_docx_key", source)
        self.assertIn("resume_pdf_key", source)
        self.assertIn("original_resume_key", source)
        self.assertIn("Deliberately exclude ``record.resume_bytes``", source)
        self.assertIn("legacy_resume_bytes", source)

    def test_workflow_store_is_serializable_ttl_aware_and_optimistically_locked(self) -> None:
        repository = DYNAMODB.read_text(encoding="utf-8")
        serializer = SERIALIZATION.read_text(encoding="utf-8")
        self.assertIn("class DynamoDBWorkflowStore", repository)
        self.assertIn("state_json_key", repository)
        self.assertIn("expires_at", repository)
        self.assertIn("expected_version", repository)
        self.assertIn("updated_by_request", repository)
        self.assertIn("ConditionExpression=condition", repository)
        self.assertIn("workflow_state_json_bytes", serializer)
        self.assertIn("WorkflowSerializationError", serializer)

    def test_production_gate_rejects_local_backends_without_demo_override(self) -> None:
        source = FACTORY.read_text(encoding="utf-8")
        self.assertIn('"CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "dynamodb"', source)
        self.assertIn('"CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "dynamodb"', source)
        self.assertIn('"CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "s3"', source)
        self.assertIn("CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION", source)
        self.assertIn("Unsafe Career Bridge production persistence configuration", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
