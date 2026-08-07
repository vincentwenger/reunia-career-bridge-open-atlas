from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_browser_storage_policy.py"


class NoSilentBrowserStorageFallbackContractTests(unittest.TestCase):
    def test_browser_storage_policy_has_no_violations(self) -> None:
        spec = importlib.util.spec_from_file_location("check_browser_storage_policy", SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual([], module.audit())

    def test_action_plan_disables_changes_when_service_is_unavailable(self) -> None:
        script = (
            ROOT / "products/reunia/static/js/pages/action-center.js"
        ).read_text(encoding="utf-8")
        self.assertIn("Changes are disabled until the connection is restored", script)
        self.assertIn("requireActionService", script)
        self.assertNotIn("writeLocalActions", script)
        self.assertNotIn("readLocalActions", script)

    def test_materials_and_profile_require_server_success(self) -> None:
        script = (
            ROOT / "products/reunia/static/js/pages/knowledge.js"
        ).read_text(encoding="utf-8")
        self.assertNotIn("localStorage", script)
        self.assertIn("No cached copy was used", script)
        self.assertIn("Your unsaved selections remain on this page", script)
        self.assertNotIn("[404, 405].includes(response.status)", script)


if __name__ == "__main__":
    unittest.main()
