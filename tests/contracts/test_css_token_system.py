from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts.check_css_token_policy import audit


ROOT = Path(__file__).resolve().parents[2]
TOKENS = ROOT / "products/reunia/static/css/design-tokens.css"
STYLELINT = ROOT / "config" / "quality" / "stylelint.config.mjs"
PACKAGE = ROOT / "package.json"
WORKFLOW = ROOT / ".github/workflows/asset-budget.yml"


class CssTokenSystemContractTests(unittest.TestCase):
    def test_css_policy_has_no_violations(self) -> None:
        self.assertEqual([], audit())

    def test_canonical_semantic_tokens_are_present(self) -> None:
        source = TOKENS.read_text(encoding="utf-8")
        for token in (
            "--cb-color-primary",
            "--cb-color-teal",
            "--cb-color-emerald",
            "--cb-color-cta",
            "--cb-color-border",
            "--cb-space-4",
            "--cb-radius-md",
            "--cb-shadow-md",
        ):
            with self.subTest(token=token):
                self.assertRegex(source, rf"(?m)^\s*{re.escape(token)}\s*:")

    def test_stylelint_rejects_raw_hex_and_noncanonical_properties(self) -> None:
        config = STYLELINT.read_text(encoding="utf-8")
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        self.assertIn("declaration-property-value-disallowed-list", config)
        self.assertIn("custom-property-pattern", config)
        self.assertIn("declaration-no-important", config)
        self.assertIn("lint:css", package["scripts"])
        self.assertIn("stylelint", package["devDependencies"])

    def test_ci_runs_css_policy_and_stylelint(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python scripts/check_css_token_policy.py", workflow)
        self.assertIn("npm run lint:css", workflow)


if __name__ == "__main__":
    unittest.main()
