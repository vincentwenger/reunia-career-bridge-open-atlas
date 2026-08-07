from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_common_page_assets.py"


class CommonPageAssetContractTests(unittest.TestCase):
    def test_all_user_facing_pages_resolve_common_css_and_javascript(self) -> None:
        spec = importlib.util.spec_from_file_location("check_common_page_assets", SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual([], module.audit())

    def test_common_assets_are_centralized_in_shared_fragments(self) -> None:
        styles = (ROOT / "products/reunia/templates/components/common_page_styles.html").read_text(encoding="utf-8")
        scripts = (ROOT / "products/reunia/templates/components/common_page_scripts.html").read_text(encoding="utf-8")
        self.assertIn("css/design-tokens.css", styles)
        self.assertIn("css/base.css", styles)
        self.assertIn("js/common.js", scripts)


if __name__ == "__main__":
    unittest.main()
