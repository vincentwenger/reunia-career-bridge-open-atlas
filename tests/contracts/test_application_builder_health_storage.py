"""Contract tests for Application Builder storage visibility in /health."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_APP = ROOT / "products" / "resume_taylor" / "app.py"
REUNIA_FACTORY = ROOT / "products" / "reunia" / "meeting_assistant" / "__init__.py"

EXPECTED_STORAGE_STATUS = {
    "workflow_storage": "memory",
    "application_storage": "sqlite",
    "durability": "demo-only",
    "multi_worker_safe": False,
    "multi_node_safe": False,
}


class ApplicationBuilderHealthStorageContractTests(unittest.TestCase):
    """Keep deployment-limitation metadata visible and non-secret."""

    def test_storage_status_helper_returns_the_required_contract(self) -> None:
        tree = ast.parse(BUILDER_APP.read_text(encoding="utf-8"))
        helper = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "application_builder_storage_status"
        )
        return_node = next(
            node for node in ast.walk(helper) if isinstance(node, ast.Return)
        )
        self.assertEqual(ast.literal_eval(return_node.value), EXPECTED_STORAGE_STATUS)

    def test_health_endpoint_includes_the_canonical_storage_status(self) -> None:
        factory_text = REUNIA_FACTORY.read_text(encoding="utf-8")
        self.assertIn("application_builder_storage_status,", factory_text)
        self.assertIn(
            '"application_builder": application_builder_storage_status(),',
            factory_text,
        )

    def test_storage_contract_contains_no_path_or_secret_material(self) -> None:
        for key in EXPECTED_STORAGE_STATUS:
            with self.subTest(key=key):
                normalized = key.casefold()
                self.assertNotIn("path", normalized)
                self.assertNotIn("secret", normalized)
                self.assertNotIn("key", normalized)
                self.assertNotIn("token", normalized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
