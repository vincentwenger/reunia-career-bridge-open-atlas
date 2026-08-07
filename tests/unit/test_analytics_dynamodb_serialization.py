from __future__ import annotations

import ast
import math
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any



ROOT = Path(__file__).resolve().parents[2]
ANALYTICS_REPOSITORY = (
    ROOT
    / "products"
    / "reunia"
    / "meeting_assistant"
    / "repositories"
    / "analytics_repository.py"
)


def _load_safe_converter():
    source = ANALYTICS_REPOSITORY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_dynamodb_safe"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"Any": Any, "Decimal": Decimal, "math": math}
    exec(compile(module, str(ANALYTICS_REPOSITORY), "exec"), namespace)
    return namespace["_dynamodb_safe"]


def _load_record_activity_method():
    source = ANALYTICS_REPOSITORY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    repository_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DynamoAnalyticsRepository"
    )
    method = next(
        node
        for node in repository_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "record_activity"
    )
    test_class = ast.ClassDef(
        name="TestRepository",
        bases=[],
        keywords=[],
        body=[method],
        decorator_list=[],
    )
    module = ast.Module(body=[test_class], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Any": Any}
    exec(compile(module, str(ANALYTICS_REPOSITORY), "exec"), namespace)
    return namespace["TestRepository"]


class AnalyticsDynamoSerializationTests(unittest.TestCase):
    def test_nested_floats_are_converted_to_decimal(self) -> None:
        converter = _load_safe_converter()
        converted = converter(
            {
                "cost": 0.00125,
                "details": {"latency": 12.5},
                "samples": [1.0, {"ratio": 0.25}],
            }
        )

        self.assertEqual(Decimal("0.00125"), converted["cost"])
        self.assertEqual(Decimal("12.5"), converted["details"]["latency"])
        self.assertEqual(Decimal("1.0"), converted["samples"][0])
        self.assertEqual(Decimal("0.25"), converted["samples"][1]["ratio"])

    def test_record_usage_event_converts_before_put_item(self) -> None:
        source_text = ANALYTICS_REPOSITORY.read_text(encoding="utf-8")
        tree = ast.parse(source_text)
        repository_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "DynamoAnalyticsRepository"
        )
        method = next(
            node
            for node in repository_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "record_usage_event"
        )
        source = ast.get_source_segment(source_text, method) or ""
        self.assertIn("item = _dynamodb_safe(", source)
        self.assertIn("Item=item", source)
        self.assertLess(source.index("_dynamodb_safe"), source.index("put_item"))

    def test_optional_country_alias_is_only_added_when_used(self) -> None:
        source_text = ANALYTICS_REPOSITORY.read_text(encoding="utf-8")
        tree = ast.parse(source_text)
        repository_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "DynamoAnalyticsRepository"
        )
        method = next(
            node
            for node in repository_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "record_activity"
        )
        source = ast.get_source_segment(source_text, method) or ""

        names_initialization, optional_country_block = source.split(
            'if event.get("country_code"):', 1
        )
        self.assertNotIn('"#country_code": "country_code"', names_initialization)
        self.assertIn(
            'names["#country_code"] = "country_code"',
            optional_country_block,
        )
        self.assertIn(
            'set_parts.append("#country_code = :country_code")',
            optional_country_block,
        )

    def test_record_activity_omits_unused_country_alias(self) -> None:
        repository_type = _load_record_activity_method()
        captured = {}

        class FakeTable:
            def update_item(self, **kwargs):
                captured.update(kwargs)

        repository = repository_type()
        repository._table = lambda: FakeTable()
        repository.record_activity(
            {
                "session_key": "session#1",
                "visitor_id": "visitor-1",
                "session_id": "session-1",
                "activity_date": "2026-08-05",
                "identity_type": "anonymous",
                "observed_at": 1_786_000_000,
                "page_path": "/applications/career-translation",
                "active_seconds": 5,
                "page_views": 1,
            }
        )

        self.assertNotIn(
            "#country_code", captured["ExpressionAttributeNames"]
        )
        self.assertNotIn(
            ":country_code", captured["ExpressionAttributeValues"]
        )
        self.assertNotIn(
            "#country_code", captured["UpdateExpression"]
        )

    def test_record_activity_includes_country_alias_when_used(self) -> None:
        repository_type = _load_record_activity_method()
        captured = {}

        class FakeTable:
            def update_item(self, **kwargs):
                captured.update(kwargs)

        repository = repository_type()
        repository._table = lambda: FakeTable()
        repository.record_activity(
            {
                "session_key": "session#2",
                "visitor_id": "visitor-2",
                "session_id": "session-2",
                "activity_date": "2026-08-05",
                "identity_type": "anonymous",
                "observed_at": 1_786_000_000,
                "page_path": "/applications/career-translation",
                "active_seconds": 5,
                "page_views": 1,
                "country_code": "US",
            }
        )

        self.assertEqual(
            "country_code",
            captured["ExpressionAttributeNames"]["#country_code"],
        )
        self.assertEqual(
            "US", captured["ExpressionAttributeValues"][":country_code"]
        )
        self.assertIn(
            "#country_code = :country_code", captured["UpdateExpression"]
        )

    def test_nan_and_infinity_are_rejected(self) -> None:
        converter = _load_safe_converter()
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    converter(value)


if __name__ == "__main__":
    unittest.main()
