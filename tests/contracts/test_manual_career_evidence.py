"""Contracts for adding confirmed evidence directly in Career Evidence Library."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "products" / "reunia", ROOT / "products" / "resume_taylor"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from career_bridge.reusable_evidence import find_best_evidence_match  # noqa: E402

try:
    import flask  # noqa: F401, E402
    import werkzeug  # noqa: F401, E402
except ModuleNotFoundError:
    SERVICE_DEPENDENCIES_AVAILABLE = False
else:
    SERVICE_DEPENDENCIES_AVAILABLE = True
    from meeting_assistant.repositories.knowledge_repository import (  # noqa: E402
        InMemoryKnowledgeRepository,
    )
    from meeting_assistant.services.knowledge_service import KnowledgeService  # noqa: E402


class _UnusedFileStore:
    pass


class _UnusedUserService:
    pass


class ManualEvidenceProductContractTests(unittest.TestCase):
    def test_library_exposes_manual_add_and_edit_controls(self) -> None:
        template = (
            ROOT / "products" / "reunia" / "templates" / "knowledge.html"
        ).read_text(encoding="utf-8")
        for token in (
            'id="openEvidenceModal"',
            "Add confirmed evidence",
            'id="manualEvidenceForm"',
            'name="evidence_title"',
            'name="confirmed_statement"',
            'name="experience_employer"',
            'name="experience_title"',
            'name="experience_dates"',
            'name="supported_skills"',
            'name="source_note"',
            'name="evidence_limitations"',
            'name="confirmation_status"',
            "data-edit-evidence",
        ):
            self.assertIn(token, template)

    def test_manual_create_uses_owner_scoped_api(self) -> None:
        routes = (
            ROOT
            / "products"
            / "reunia"
            / "meeting_assistant"
            / "blueprints"
            / "knowledge"
            / "routes.py"
        ).read_text(encoding="utf-8")
        self.assertIn('@knowledge_bp.post("/api/career/evidence/answers")', routes)
        self.assertIn("KnowledgeService().create_manual_evidence_answer", routes)
        self.assertIn("g.current_user_id", routes)

    def test_edit_details_control_is_available_for_all_evidence_rows(self) -> None:
        template = (
            ROOT / "products" / "reunia" / "templates" / "knowledge.html"
        ).read_text(encoding="utf-8")
        self.assertIn('data-edit-evidence', template)
        self.assertNotIn('data-edit-manual-evidence', template)

    def test_browser_posts_and_updates_manual_records(self) -> None:
        javascript = (
            ROOT
            / "products"
            / "reunia"
            / "static"
            / "js"
            / "pages"
            / "knowledge-evidence.js"
        ).read_text(encoding="utf-8")
        self.assertIn("form.addEventListener('submit'", javascript)
        self.assertIn("method: evidenceId ? 'PUT' : 'POST'", javascript)
        self.assertIn("Confirmed evidence added.", javascript)
        self.assertIn("Evidence details updated.", javascript)
        self.assertIn("statusSelect.disabled = !isManual", javascript)
        self.assertIn("editingEntryMethod === 'manual'", javascript)

    def test_confirmed_manual_evidence_can_match_future_questions(self) -> None:
        stored = {
            "question": "SEO and paid-search acquisition strategy",
            "requirement": "SEO, SEA, paid search",
            "answer_type": "long_text",
            "answer_text": "Developed and implemented SEO and paid-search acquisition activities.",
            "confirmation_status": "confirmed",
        }
        match, score = find_best_evidence_match(
            "Describe your SEO and paid-search acquisition strategy",
            "SEO, SEA, paid search",
            [stored],
            answer_type="long_text",
            threshold=0.75,
        )
        self.assertIs(match, stored)
        self.assertGreaterEqual(score, 0.75)

    def test_needs_review_manual_evidence_is_not_automatically_reused(self) -> None:
        stored = {
            "question_key": "",
            "question": "SEO and paid-search acquisition strategy",
            "requirement": "SEO, SEA, paid search",
            "answer_type": "long_text",
            "answer_text": "Developed and implemented SEO and paid-search acquisition activities.",
            "confirmation_status": "needs_review",
        }
        match, score = find_best_evidence_match(
            "Describe your SEO and paid-search acquisition strategy",
            "SEO and paid search",
            [stored],
            answer_type="long_text",
        )
        self.assertIsNone(match)
        self.assertEqual(score, 0.0)


@unittest.skipUnless(
    SERVICE_DEPENDENCIES_AVAILABLE,
    "Flask/Werkzeug dependencies are not installed in this validation environment.",
)
class ManualEvidenceStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryKnowledgeRepository()
        self.service = KnowledgeService(
            repository=self.repository,
            file_store=_UnusedFileStore(),
            user_service=_UnusedUserService(),
        )

    def test_manual_evidence_uses_existing_durable_record_type(self) -> None:
        saved = self.service.create_manual_evidence_answer(
            "user-1",
            {
                "evidence_title": "Digital acquisition strategy using SEO and paid search",
                "confirmed_statement": (
                    "Since September 2018, Thomas developed and implemented the "
                    "digital customer-acquisition strategy using SEO and paid search."
                ),
                "experience_employer": "Fausse-Boîte Inc.",
                "experience_title": "Responsable Marketing Digital",
                "experience_dates": "September 2018–Present",
                "supported_skills": "SEO, SEA, paid search, digital acquisition strategy",
                "source_note": "Imported French résumé and candidate confirmation",
                "evidence_limitations": "No verified budgets or conversion percentages.",
                "confirmation_status": "confirmed",
            },
        )

        self.assertEqual(saved["entry_method"], "manual")
        self.assertEqual(saved["confirmation_status"], "confirmed")
        self.assertEqual(saved["experience_employer"], "Fausse-Boîte Inc.")
        self.assertIn("SEO", saved["supported_skills"])
        self.assertTrue(saved["confirmed_at"])
        self.assertEqual(len(self.service.list_evidence_answers("user-1")), 1)
        self.assertEqual(self.service.list_evidence_answers("user-2"), [])

    def test_manual_evidence_details_can_be_edited(self) -> None:
        saved = self.service.create_manual_evidence_answer(
            "user-1",
            {
                "evidence_title": "Social media and KPI analysis",
                "confirmed_statement": "Managed LinkedIn and Instagram and analyzed KPIs.",
                "confirmation_status": "confirmed",
            },
        )
        updated = self.service.update_evidence_answer(
            "user-1",
            saved["evidence_id"],
            {
                "evidence_title": "Social-media management and KPI-based optimization",
                "confirmed_statement": (
                    "Managed LinkedIn and Instagram and analyzed campaign KPIs "
                    "to identify optimization opportunities."
                ),
                "experience_employer": "Fausse-Boîte Inc.",
                "experience_title": "Responsable Marketing Digital",
                "experience_dates": "September 2018–Present",
                "supported_skills": "LinkedIn, Instagram, KPI analysis, campaign optimization",
                "source_note": "Imported French résumé",
                "evidence_limitations": "No verified follower growth or lead volume.",
                "confirmation_status": "needs_review",
            },
        )
        self.assertEqual(updated["confirmation_status"], "needs_review")
        self.assertIsNone(updated["yes_no"])
        self.assertIn("KPI-based", updated["question"])
        self.assertEqual(updated["experience_dates"], "September 2018–Present")
        self.assertIn("lead volume", updated["evidence_limitations"])

    def test_workflow_evidence_details_can_be_edited_without_changing_answer_status(self) -> None:
        saved = self.service.save_evidence_answers(
            "user-1",
            [
                {
                    "question": "Have you managed paid-search acquisition?",
                    "requirement": "SEO and paid search",
                    "answer_type": "yes_no",
                    "yes_no": False,
                    "answer_text": "",
                    "experience_employer": "Fausse-Boîte Inc.",
                    "experience_title": "Responsable Marketing Digital",
                    "experience_label": "Fausse-Boîte Inc. — Responsable Marketing Digital",
                }
            ],
        )[0]
        self.assertEqual(saved["entry_method"], "workflow")
        self.assertIs(saved["yes_no"], False)

        updated = self.service.update_evidence_answer(
            "user-1",
            saved["evidence_id"],
            {
                "evidence_title": "Paid-search acquisition experience",
                "confirmed_statement": "No additional paid-search evidence was confirmed for this requirement.",
                "experience_dates": "September 2018–Present",
                "supported_skills": "SEO, SEA, paid search",
                "source_note": "Additional experience confirmation",
                "evidence_limitations": "No verified budget or conversion metrics.",
            },
        )

        self.assertEqual(updated["entry_method"], "workflow")
        self.assertIs(updated["yes_no"], False)
        self.assertEqual(updated["confirmation_status"], "confirmed")
        self.assertEqual(updated["experience_dates"], "September 2018–Present")
        self.assertIn("conversion metrics", updated["evidence_limitations"])


if __name__ == "__main__":
    unittest.main()
