"""Architecture contracts for the canonical Job Application aggregate."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "products" / "reunia" / "meeting_assistant" / "services"
APPLICATION_MATERIALS = SERVICES / "application_materials_service.py"
DYNAMODB_STORAGE = (
    ROOT / "products" / "resume_taylor" / "resume_tailor" / "dynamodb_storage.py"
)


class CanonicalApplicationMaterialsContracts(unittest.TestCase):
    def test_legacy_meeting_materials_runtime_service_is_removed(self) -> None:
        self.assertFalse((SERVICES / "meeting_materials_service.py").exists())
        for path in ROOT.glob("products/**/*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("MeetingMaterialsService", source, str(path))
            self.assertNotIn("meeting_materials_service", source, str(path))

    def test_application_materials_are_linked_to_application_records(self) -> None:
        service = APPLICATION_MATERIALS.read_text(encoding="utf-8")
        storage = DYNAMODB_STORAGE.read_text(encoding="utf-8")
        self.assertIn("class ApplicationMaterialsService", service)
        self.assertIn('"career_bridge_application_store"', service)
        self.assertIn("get_application_materials", service)
        self.assertIn("save_application_materials", service)
        self.assertIn('APPLICATION_MATERIALS#<application_id>', service)
        self.assertIn('_APPLICATION_MATERIALS_PREFIX = "APPLICATION_MATERIALS#"', storage)
        self.assertIn('_ACTIVE_APPLICATION_KEY = "STATE#ACTIVE_APPLICATION"', storage)

    def test_legacy_metadata_migration_runtime_is_removed(self) -> None:
        service = APPLICATION_MATERIALS.read_text(encoding="utf-8")
        self.assertNotIn("_migrate_legacy_materials", service)
        self.assertNotIn("_migrate_legacy_active_application", service)
        self.assertNotIn("_legacy_materials_migration_enabled", service)
        self.assertNotIn("list_meetings(", service)
        self.assertNotIn("upsert_meeting(", service)

    def test_ui_aliases_normalize_to_the_same_application_id(self) -> None:
        service = APPLICATION_MATERIALS.read_text(encoding="utf-8")
        self.assertIn("def _normalize_application_id", service)
        self.assertIn('{"builder", "application"}', service)


if __name__ == "__main__":
    unittest.main()
