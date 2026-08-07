"""Behavior tests for application-owned materials and legacy migration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REUNIA_ROOT = ROOT / "products" / "reunia"
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"
for path in (ROOT, REUNIA_ROOT, RESUME_TAYLOR_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

try:
    from flask import Flask

    from meeting_assistant.repositories.knowledge_repository import (
        InMemoryKnowledgeRepository,
    )
    from meeting_assistant.services.application_materials_service import (
        ApplicationMaterialsService,
    )
    from tests.helpers.dynamodb_application_store import make_application_store
except ModuleNotFoundError as exc:  # pragma: no cover - dependency-light CI
    Flask = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class InMemoryKnowledgeFileStore:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, object_key: str, content: bytes, content_type: str) -> None:
        del content_type
        self.items[object_key] = bytes(content)

    def get(self, object_key: str) -> bytes:
        return self.items[object_key]

    def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)
        self.items.pop(object_key, None)


@unittest.skipIf(Flask is None, f"Runtime dependencies unavailable: {IMPORT_ERROR}")
class CanonicalApplicationMaterialsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application_store = make_application_store()
        self.knowledge_repository = InMemoryKnowledgeRepository()
        self.knowledge_file_store = InMemoryKnowledgeFileStore()
        self.app = Flask(__name__)
        self.app.extensions["career_bridge_application_store"] = self.application_store
        self.app.extensions["knowledge_repository"] = self.knowledge_repository
        self.app.extensions["knowledge_file_store"] = self.knowledge_file_store
        self.owner_id = "owner-1"
        self.application = self.application_store.create(
            self.owner_id,
            company="Example Bank",
            role="Senior Engineer",
        )

    def test_builder_alias_resolves_to_the_canonical_application(self) -> None:
        with self.app.app_context():
            service = ApplicationMaterialsService()
            materials = service.save_materials(
                self.owner_id,
                {
                    "meeting_id": f"builder:{self.application.id}",
                    "application_context": {"objective": "Prepare for interview"},
                    "recruiter_contacts": ["Recruiter"],
                    "recruiter_messages": [
                        {
                            "subject": "Interview invitation",
                            "body": "Please confirm your availability.",
                            "direction": "inbound",
                        }
                    ],
                    "interview_scheduled_at": "2026-08-10T09:00:00-07:00",
                },
            )
            self.assertEqual(materials["application_id"], self.application.id)
            self.assertEqual(
                service.set_active_application(
                    self.owner_id, f"builder:{self.application.id}"
                ),
                self.application.id,
            )
            service.complete_interview(
                self.owner_id,
                f"builder:{self.application.id}",
                "interview-1",
            )

        record = self.application_store.get_application_materials(
            self.owner_id, self.application.id
        )
        self.assertIsNotNone(record)
        payload = record.payload()
        self.assertEqual(payload["application_id"], self.application.id)
        self.assertEqual(payload["last_completed_interview_id"], "interview-1")
        self.assertEqual(payload["recruiter_contacts"], ["Recruiter"])
        self.assertEqual(
            payload["recruiter_messages"][0]["body"],
            "Please confirm your availability.",
        )
        self.assertEqual(
            payload["interview_scheduled_at"],
            "2026-08-10T09:00:00-07:00",
        )
        self.assertEqual(
            payload["application_context"]["objective"], "Prepare for interview"
        )
        self.assertIsNone(
            self.knowledge_repository.get_meeting(
                self.owner_id, self.application.id
            )
        )
        self.assertEqual(
            self.application_store.get_active_application_id(self.owner_id), ""
        )

    def test_matching_legacy_package_is_migrated_once(self) -> None:
        self.knowledge_repository.upsert_meeting(
            {
                "user_id": self.owner_id,
                "meeting_id": self.application.id,
                "library_file_ids": [],
                "temporary_files": [],
                "meeting_context": {"objective": "Legacy context"},
                "scheduled_at": "2026-08-10T09:00:00-07:00",
                "participants": ["Recruiter"],
                "material_index": [],
                "material_index_version": 2,
                "material_indexed_at": "",
                "created_at": "2026-08-01T00:00:00+00:00",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        )

        with self.app.app_context():
            materials = ApplicationMaterialsService().get_materials(
                self.owner_id, self.application.id
            )

        self.assertEqual(
            materials["application_context"]["objective"], "Legacy context"
        )
        self.assertIsNone(
            self.knowledge_repository.get_meeting(
                self.owner_id, self.application.id
            )
        )
        record = self.application_store.get_application_materials(
            self.owner_id, self.application.id
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.payload()["recruiter_contacts"], ["Recruiter"])


if __name__ == "__main__":
    unittest.main()
