"""Unit tests for Career Evidence Library dashboard readiness."""

from __future__ import annotations

import unittest
from career_bridge.application.evidence_readiness import (
    build_evidence_library_readiness,
)


class HomeEvidenceReadinessTests(unittest.TestCase):
    def readiness(self, library: dict) -> dict:
        return build_evidence_library_readiness(library)

    def test_empty_library_needs_setup(self) -> None:
        result = self.readiness({"files": [], "evidence_answers": [], "career_roles": []})
        self.assertFalse(result["ready"])
        self.assertEqual(result["item_count"], 0)

    def test_ready_document_makes_library_ready(self) -> None:
        result = self.readiness({
            "files": [{"status": "ready"}, {"status": "failed"}],
            "evidence_answers": [],
            "career_roles": [],
        })
        self.assertTrue(result["ready"])
        self.assertEqual(result["document_count"], 1)
        self.assertEqual(result["item_count"], 1)

    def test_only_supportive_confirmation_answers_count(self) -> None:
        result = self.readiness({
            "files": [],
            "evidence_answers": [
                {"yes_no": True, "answer_text": "Led the migration."},
                {"yes_no": None, "answer_text": "Built the pipeline."},
                {"yes_no": False, "answer_text": "No direct experience."},
                {"yes_no": True, "answer_text": ""},
                {"yes_no": None, "answer_text": "Pending manual evidence.", "confirmation_status": "needs_review"},
            ],
            "career_roles": [],
        })
        self.assertEqual(result["answer_count"], 2)
        self.assertEqual(result["item_count"], 2)

    def test_only_active_confirmed_roles_count(self) -> None:
        result = self.readiness({
            "files": [],
            "evidence_answers": [],
            "career_roles": [
                {"status": "confirmed", "source_active": True},
                {"status": "needs_review", "source_active": True},
                {"status": "confirmed", "source_active": False},
            ],
        })
        self.assertTrue(result["ready"])
        self.assertEqual(result["confirmed_role_count"], 1)
        self.assertEqual(result["item_count"], 1)


if __name__ == "__main__":
    unittest.main()
