"""Production startup contracts for Career Bridge persistence."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
APP_FACTORY = ROOT / "products" / "reunia" / "meeting_assistant" / "__init__.py"
CONFIG = ROOT / "products" / "reunia" / "meeting_assistant" / "config.py"


def _load_validation_functions():
    tree = ast.parse(APP_FACTORY.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name
        in {
            "_configuration_flag",
            "_validate_career_bridge_production_storage",
        }
    ]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"Flask": object}
    exec(compile(module, str(APP_FACTORY), "exec"), namespace)
    return namespace["_validate_career_bridge_production_storage"]


class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message % args if args else message)


def _app(config: dict[str, object]):
    return SimpleNamespace(config=config, logger=_RecordingLogger(), testing=False)


class CareerBridgeProductionStorageValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validate = staticmethod(_load_validation_functions())

    def test_durable_backends_and_explicit_resource_names_pass(self) -> None:
        app = _app(
            {
                "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "dynamodb",
                "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "dynamodb",
                "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "s3",
                "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "career-bridge-applications",
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_BUCKET": "career-bridge-documents",
            }
        )

        self.validate(app)
        self.assertEqual(app.logger.warnings, [])

    def test_memory_sqlite_and_local_are_rejected_in_production(self) -> None:
        app = _app(
            {
                "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "sqlite",
                "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "memory",
                "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "local",
            }
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Unsafe Career Bridge production persistence configuration",
        ) as raised:
            self.validate(app)

        message = str(raised.exception)
        self.assertIn("CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND", message)
        self.assertIn("CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND", message)
        self.assertIn("CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND", message)
        self.assertIn("CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION=true", message)

    def test_missing_table_and_bucket_names_are_rejected(self) -> None:
        app = _app(
            {
                "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "dynamodb",
                "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "dynamodb",
                "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "s3",
                "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "",
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "",
                "CAREER_BRIDGE_DOCUMENTS_BUCKET": "",
            }
        )

        with self.assertRaises(RuntimeError) as raised:
            self.validate(app)

        message = str(raised.exception)
        self.assertIn("CAREER_BRIDGE_APPLICATIONS_TABLE_NAME", message)
        self.assertIn("CAREER_BRIDGE_WORKFLOWS_TABLE_NAME", message)
        self.assertIn("CAREER_BRIDGE_DOCUMENTS_BUCKET", message)

    def test_explicit_demo_override_allows_ephemeral_backends_and_warns(self) -> None:
        app = _app(
            {
                "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "sqlite",
                "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "memory",
                "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "local",
                "CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION": "true",
            }
        )

        self.validate(app)

        self.assertEqual(len(app.logger.warnings), 1)
        self.assertIn("DEMO STORAGE OVERRIDE ENABLED", app.logger.warnings[0])
        self.assertIn("one worker and one node", app.logger.warnings[0])

    def test_false_override_does_not_bypass_validation(self) -> None:
        app = _app(
            {
                "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "sqlite",
                "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "memory",
                "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "local",
                "CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION": "false",
            }
        )

        with self.assertRaises(RuntimeError):
            self.validate(app)

    def test_central_production_validator_invokes_career_bridge_gate_first(self) -> None:
        tree = ast.parse(APP_FACTORY.read_text(encoding="utf-8"))
        validator = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_validate_production_configuration"
        )
        calls = [
            node.value.func.id
            for node in validator.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
        ]
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0], "_validate_career_bridge_production_storage")
        self.assertEqual(calls[1], "_validate_dynamodb_table_configuration")

    def test_generic_dynamodb_table_validation_includes_workflows(self) -> None:
        tree = ast.parse(APP_FACTORY.read_text(encoding="utf-8"))
        validator = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_validate_dynamodb_table_configuration"
        )
        string_pairs = {
            tuple(
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
            )
            for node in ast.walk(validator)
            if isinstance(node, ast.Tuple)
        }
        self.assertIn(
            (
                "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND",
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME",
            ),
            string_pairs,
        )

    def test_configuration_exposes_the_narrow_demo_override(self) -> None:
        source = CONFIG.read_text(encoding="utf-8")
        self.assertIn("CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION", source)
        self.assertIn('"false"', source)

    def test_production_defaults_select_durable_career_bridge_adapters(self) -> None:
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
            for node in ast.walk(statement.value):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getenv"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                ):
                    defaults[target.id] = node.args[1].value
                    break

        self.assertEqual(
            defaults.get("CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND"),
            "dynamodb",
        )
        self.assertEqual(
            defaults.get("CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND"),
            "dynamodb",
        )
        self.assertEqual(
            defaults.get("CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND"),
            "s3",
        )

    def test_local_adapters_are_confined_to_testing_or_explicit_demo_mode(self) -> None:
        tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
        testing = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "TestingConfig"
        )
        assignments = {
            statement.targets[0].id: statement.value.value
            for statement in testing.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        }
        self.assertEqual(assignments["CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND"], "memory")
        self.assertEqual(assignments["CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND"], "sqlite")
        self.assertEqual(assignments["CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND"], "local")

        source = APP_FACTORY.read_text(encoding="utf-8")
        self.assertIn("CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION", source)
        self.assertIn("Production requires DynamoDB application/workflow storage", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
