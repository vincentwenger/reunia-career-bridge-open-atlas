"""Contract tests for Application Builder storage startup logging."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_APP = ROOT / "products" / "resume_taylor" / "app.py"
REUNIA_FACTORY = ROOT / "products" / "reunia" / "meeting_assistant" / "__init__.py"


class ApplicationBuilderPersistenceWarningContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder_text = BUILDER_APP.read_text(encoding="utf-8")

    def test_startup_log_reports_all_storage_backends(self) -> None:
        self.assertIn("Application Builder storage configured", self.builder_text)
        self.assertIn("workflow=%s", self.builder_text)
        self.assertIn("applications=%s", self.builder_text)
        self.assertIn("job_discovery=%s", self.builder_text)
        self.assertIn("job_discovery_table=%s", self.builder_text)
        self.assertIn("documents=%s%s", self.builder_text)
        self.assertIn("not fully durable", self.builder_text)

    def test_application_store_has_no_local_database_path_setup(self) -> None:
        combined = self.builder_text + REUNIA_FACTORY.read_text(encoding="utf-8")
        self.assertNotIn("application_database_path", combined)

    def test_storage_log_is_emitted_once_per_application(self) -> None:
        self.assertIn(
            'warning_key = "career_bridge_application_builder_persistence_warning_logged"',
            self.builder_text,
        )
        self.assertIn('if not app.extensions.get(warning_key):', self.builder_text)
        self.assertIn('app.extensions[warning_key] = True', self.builder_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
