"""Contract tests for the Application Builder startup persistence warning."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_APP = ROOT / "products" / "resume_taylor" / "app.py"
REUNIA_FACTORY = ROOT / "products" / "reunia" / "meeting_assistant" / "__init__.py"
DOCKERFILE = ROOT / "Dockerfile"


class ApplicationBuilderPersistenceWarningContractTests(unittest.TestCase):
    """Keep process-local and local-file persistence risks visible at startup."""

    def setUp(self) -> None:
        self.builder_text = BUILDER_APP.read_text(encoding="utf-8")

    def test_startup_warning_describes_each_persistence_risk(self) -> None:
        required_lines = (
            "Application Builder persistence:\\n",
            "- workflow backend: process memory\\n",
            "- application backend: SQLite\\n",
            "- database path: %s\\n",
            "- safe only with one Gunicorn worker and Lightsail scale 1\\n",
            "- records may be lost during container replacement",
        )
        for line in required_lines:
            with self.subTest(line=line):
                self.assertIn(line, self.builder_text)

    def test_warning_uses_the_resolved_database_path(self) -> None:
        self.assertIn(
            'application_database_path = app.config.get("APPLICATIONS_DB_PATH")',
            self.builder_text,
        )
        self.assertIn(
            'app.logger.warning(',
            self.builder_text,
        )
        self.assertIn(
            '            application_database_path,\n        )',
            self.builder_text,
        )

    def test_warning_is_logged_only_once_per_application(self) -> None:
        self.assertIn(
            'warning_key = "career_bridge_application_builder_persistence_warning_logged"',
            self.builder_text,
        )
        self.assertIn('if not app.extensions.get(warning_key):', self.builder_text)
        self.assertIn('app.extensions[warning_key] = True', self.builder_text)

    def test_default_database_filename_is_consistent(self) -> None:
        factory_text = REUNIA_FACTORY.read_text(encoding="utf-8")
        expected = 'career_bridge_applications.sqlite3'
        self.assertIn(expected, self.builder_text)
        self.assertIn(expected, factory_text)

    def test_container_layout_resolves_to_documented_production_path(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("WORKDIR /app", dockerfile)
        factory_text = REUNIA_FACTORY.read_text(encoding="utf-8")
        self.assertIn('repository_root\n                / "instance"', factory_text)
        self.assertIn('/ "career_bridge_applications.sqlite3"', factory_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
