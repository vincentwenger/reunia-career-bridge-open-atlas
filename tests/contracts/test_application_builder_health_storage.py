"""Contract tests for Application Builder storage visibility in /health."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_APP = ROOT / "products" / "resume_taylor" / "app.py"
REUNIA_FACTORY = ROOT / "products" / "reunia" / "meeting_assistant" / "__init__.py"

EXPECTED_STORAGE_KEYS = {
    "workflow_storage",
    "application_storage",
    "document_storage",
    "durability",
    "multi_worker_safe",
    "multi_node_safe",
}


class ApplicationBuilderHealthStorageContractTests(unittest.TestCase):
    """Keep storage capability metadata visible, dynamic, and non-secret."""

    def test_storage_status_helper_uses_configured_backends(self) -> None:
        tree = ast.parse(BUILDER_APP.read_text(encoding="utf-8"))
        helper = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "application_builder_storage_status"
        )
        helper_text = ast.unparse(helper)
        self.assertIn("configured_workflow_backend(current_app.config)", helper_text)
        self.assertIn("configured_application_backend(current_app.config)", helper_text)
        self.assertIn("configured_document_backend(current_app.config)", helper_text)
        for key in EXPECTED_STORAGE_KEYS:
            with self.subTest(key=key):
                self.assertIn(repr(key), helper_text)

    def test_health_endpoint_includes_the_canonical_storage_status(self) -> None:
        factory_text = REUNIA_FACTORY.read_text(encoding="utf-8")
        self.assertIn("application_builder_storage_status,", factory_text)
        self.assertIn(
            '"application_builder": application_builder_storage_status(),',
            factory_text,
        )

    def test_storage_contract_contains_no_path_or_secret_material(self) -> None:
        for key in EXPECTED_STORAGE_KEYS:
            with self.subTest(key=key):
                normalized = key.casefold()
                self.assertNotIn("path", normalized)
                self.assertNotIn("secret", normalized)
                self.assertNotIn("key", normalized)
                self.assertNotIn("token", normalized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
