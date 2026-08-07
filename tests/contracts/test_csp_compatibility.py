"""Static contracts keeping templates compatible with the production CSP."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.csp_helpers import template_csp_violations

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOTS = (
    ROOT / "products" / "reunia" / "templates",
    ROOT / "products" / "resume_taylor" / "templates",
)
APP_FACTORY = ROOT / "products" / "reunia" / "meeting_assistant" / "__init__.py"


class CSPCompatibilityContractTests(unittest.TestCase):
    def test_templates_reject_inline_handlers_and_nonce_inline_scripts(self) -> None:
        violations: list[str] = []
        for root in TEMPLATE_ROOTS:
            for path in sorted(root.rglob("*.html")):
                violations.extend(template_csp_violations(path))
        self.assertEqual([], violations, "\n".join(violations))

    def test_production_script_policy_requires_request_nonce(self) -> None:
        content = APP_FACTORY.read_text(encoding="utf-8")
        self.assertIn("g.csp_nonce = secrets.token_urlsafe", content)
        self.assertIn("f\"script-src 'self' 'nonce-{nonce}'\"", content)
        script_policy = re.search(
            r'f"script-src \'self\' \'nonce-\{nonce\}\'"', content
        )
        self.assertIsNotNone(script_policy)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
