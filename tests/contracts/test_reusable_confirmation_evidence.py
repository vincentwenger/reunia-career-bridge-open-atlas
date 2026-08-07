"""Contracts for reusable Confirm Relevant Experience answers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tests.application_builder_source import application_builder_source
from tests.source_helpers import function_source

ROOT = Path(__file__).resolve().parents[2]
CONFIRMATION_ROUTES = (
    ROOT
    / "products"
    / "resume_taylor"
    / "application_builder_routes"
    / "resume_workflow_routes"
    / "confirmation_routes.py"
)
for candidate in (ROOT, ROOT / "products" / "reunia", ROOT / "products" / "resume_taylor"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from career_bridge.reusable_evidence import (  # noqa: E402
    MATCH_THRESHOLD,
    answer_has_specific_evidence,
    find_best_evidence_match,
    question_match_score,
    stored_answer_fully_satisfies,
)
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


class ReusableEvidenceMatchingTests(unittest.TestCase):
    def test_exact_question_is_reused_even_when_requirement_wording_changes(self) -> None:
        stored = {
            "evidence_id": "ev-1",
            "question": "Have you built machine learning models in Python?",
            "normalized_question": "have you built machine learning models in python",
            "requirement": "Production machine learning experience",
            "normalized_requirement": "production machine learning experience",
            "answer_type": "yes_no_with_details",
        }
        match, score = find_best_evidence_match(
            "Have you built machine learning models in Python?",
            "Develop predictive models with Python",
            [stored],
            answer_type="yes_no",
        )
        self.assertIs(match, stored)
        self.assertEqual(score, 1.0)

    def test_data_pipeline_paraphrase_matches_same_evidence_topic(self) -> None:
        stored = {
            "question": "Can you confirm any direct experience with developing data pipelines in your previous roles?",
            "requirement": "Data pipeline development",
            "normalized_requirement": "data pipeline development",
            "answer_type": "yes_no_with_details",
            "yes_no": True,
            "answer_text": "Built nightly ETL pipelines in Python and SQL to transform Oracle trade data into regulatory reports for banking clients.",
        }
        match, score = find_best_evidence_match(
            "Can you provide specific examples of your experience in data pipeline development?",
            "Data pipeline development",
            [stored],
            answer_type="long_text",
        )
        self.assertIs(match, stored)
        self.assertGreaterEqual(score, MATCH_THRESHOLD)

    def test_aml_detail_question_matches_broad_library_question(self) -> None:
        stored = {
            "question": "What experience do you have with AML processes or related knowledge?",
            "requirement": "AML processes or related knowledge",
            "normalized_requirement": "aml processes or related knowledge",
            "answer_type": "long_text",
            "yes_no": True,
            "answer_text": (
                "Managed database environments housing AML data and orchestrated "
                "the migration and backup of dedicated AML raw data files during "
                "large-scale Oracle database upgrades."
            ),
        }
        match, score = find_best_evidence_match(
            "Do you have any specific experience with AML processes that can be detailed?",
            "AML processes",
            [stored],
            answer_type="yes_no_with_details",
        )
        self.assertIs(match, stored)
        self.assertGreaterEqual(score, MATCH_THRESHOLD)
        self.assertTrue(
            stored_answer_fully_satisfies(
                "Do you have any specific experience with AML processes that can be detailed?",
                "AML processes",
                stored,
                answer_type="yes_no_with_details",
            )
        )

    def test_aml_yes_no_wording_reuses_broad_library_answer(self) -> None:
        stored = {
            "question": "What experience do you have with AML processes or related knowledge?",
            "requirement": "AML processes or related knowledge",
            "normalized_requirement": "aml processes or related knowledge",
            "answer_type": "long_text",
            "yes_no": True,
            "answer_text": (
                "Managed database environments housing AML data and orchestrated "
                "the migration and backup of dedicated AML raw data files during "
                "large-scale Oracle database upgrades."
            ),
        }
        question = (
            "Do you have experience specifically with AML processes in your previous roles?"
        )
        match, score = find_best_evidence_match(
            question,
            "AML processes",
            [stored],
            answer_type="yes_no_with_details",
        )
        self.assertIs(match, stored)
        self.assertGreaterEqual(score, MATCH_THRESHOLD)
        self.assertTrue(
            stored_answer_fully_satisfies(
                question,
                "AML processes",
                stored,
                answer_type="yes_no_with_details",
            )
        )

    def test_aml_general_answer_prefills_targeted_detail_follow_up(self) -> None:
        stored = {
            "question": "What experience do you have with AML processes or related knowledge?",
            "answer_type": "long_text",
            "yes_no": True,
            "answer_text": "I have general knowledge of AML processes.",
        }
        match, score = find_best_evidence_match(
            "Do you have any specific experience with AML processes that can be detailed?",
            "AML processes",
            [stored],
            answer_type="yes_no_with_details",
        )
        self.assertIs(match, stored)
        self.assertGreaterEqual(score, MATCH_THRESHOLD)
        self.assertFalse(
            stored_answer_fully_satisfies(
                "Do you have any specific experience with AML processes that can be detailed?",
                "AML processes",
                stored,
                answer_type="yes_no_with_details",
            )
        )

    def test_anti_money_laundering_expansion_matches_aml(self) -> None:
        stored = {
            "question": "What experience do you have with anti-money-laundering processes?",
            "answer_type": "long_text",
            "yes_no": True,
            "answer_text": "Reviewed anti-money-laundering data controls for regulated systems.",
        }
        match, score = find_best_evidence_match(
            "Can you detail your AML process experience?",
            "AML processes",
            [stored],
            answer_type="long_text",
        )
        self.assertIs(match, stored)
        self.assertGreaterEqual(score, MATCH_THRESHOLD)

    def test_specific_example_question_reuses_detailed_saved_answer(self) -> None:
        stored = {
            "question": "Can you confirm direct experience developing data pipelines?",
            "answer_type": "yes_no_with_details",
            "yes_no": True,
            "answer_text": "Built nightly ETL pipelines in Python and SQL to transform Oracle trade data into regulatory reports for banking clients.",
        }
        self.assertTrue(
            stored_answer_fully_satisfies(
                "Can you provide specific examples of your experience in data pipeline development?",
                "Data pipeline development",
                stored,
                answer_type="long_text",
            )
        )

    def test_general_confirmation_requires_targeted_detail_follow_up(self) -> None:
        stored = {
            "question": "Can you confirm direct experience developing data pipelines?",
            "answer_type": "yes_no_with_details",
            "yes_no": True,
            "answer_text": "I developed data pipelines in previous roles.",
        }
        self.assertFalse(
            answer_has_specific_evidence(
                stored["answer_text"],
                question="Can you provide specific examples of your experience in data pipeline development?",
                requirement="Data pipeline development",
            )
        )
        self.assertFalse(
            stored_answer_fully_satisfies(
                "Can you provide specific examples of your experience in data pipeline development?",
                "Data pipeline development",
                stored,
                answer_type="long_text",
            )
        )

    def test_negative_semantic_answer_is_reused_without_requesting_examples(self) -> None:
        stored = {
            "question": "Can you confirm direct experience developing data pipelines?",
            "answer_type": "yes_no_with_details",
            "yes_no": False,
            "answer_text": "",
        }
        self.assertTrue(
            stored_answer_fully_satisfies(
                "Can you provide specific examples of your experience in data pipeline development?",
                "Data pipeline development",
                stored,
                answer_type="long_text",
            )
        )

    def test_unrelated_leadership_question_is_not_reused(self) -> None:
        stored = {
            "question": "Have you led database migration projects?",
            "normalized_question": "have you led database migration projects",
            "requirement": "Lead Oracle migrations",
            "normalized_requirement": "lead oracle migrations",
            "answer_type": "yes_no_with_details",
        }
        score = question_match_score(
            "Have you led machine learning model validation projects?",
            "Lead model validation",
            stored,
            answer_type="yes_no_with_details",
        )
        self.assertLess(score, MATCH_THRESHOLD)


@unittest.skipUnless(
    SERVICE_DEPENDENCIES_AVAILABLE,
    "Flask/Werkzeug dependencies are not installed in this validation environment.",
)
class ReusableEvidenceStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryKnowledgeRepository()
        self.service = KnowledgeService(
            repository=self.repository,
            file_store=_UnusedFileStore(),
            user_service=_UnusedUserService(),
        )

    def test_answers_are_upserted_and_editable(self) -> None:
        entry = {
            "question": "Have you built machine learning models in Python?",
            "requirement": "Machine learning model development",
            "answer_type": "yes_no_with_details",
            "yes_no": True,
            "answer_text": "Built fraud-detection models in Python and deployed scoring pipelines.",
            "experience_id": "EXP-1",
            "experience_label": "Northstar Financial Systems — Lead Software Engineer",
            "experience_employer": "Northstar Financial Systems",
            "experience_title": "Lead Software Engineer",
            "placement": "update_existing",
            "source_application_id": "APP-1",
            "source_job_title": "Machine Learning Engineer",
            "source_company": "Example Corp",
        }
        first = self.service.save_evidence_answers("user-1", [entry])
        second = self.service.save_evidence_answers(
            "user-1",
            [{**entry, "answer_text": "Built and deployed Python fraud models."}],
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(self.service.list_evidence_answers("user-1")), 1)

        evidence_id = first[0]["evidence_id"]
        updated = self.service.update_evidence_answer(
            "user-1",
            evidence_id,
            {
                "yes_no": True,
                "answer_text": "Built and monitored Python fraud models in production.",
                "experience_label": "Northstar Financial Systems — Lead Software Engineer",
            },
        )
        self.assertIn("monitored", updated["answer_text"])
        self.assertEqual(
            self.service.list_evidence_answers("user-1")[0]["answer_text"],
            updated["answer_text"],
        )

    def test_negative_answer_can_be_saved_without_role_or_detail(self) -> None:
        saved = self.service.save_evidence_answers(
            "user-1",
            [
                {
                    "question": "Have you administered Kubernetes clusters?",
                    "requirement": "Kubernetes administration",
                    "answer_type": "yes_no",
                    "yes_no": False,
                    "answer_text": "",
                }
            ],
        )
        self.assertEqual(saved[0]["yes_no"], False)
        self.assertEqual(saved[0]["answer_text"], "")

    def test_question_and_saved_answer_can_be_removed(self) -> None:
        saved = self.service.save_evidence_answers(
            "user-1",
            [
                {
                    "question": "Have you optimized large SQL workloads?",
                    "requirement": "Database performance optimization",
                    "answer_type": "yes_no_with_details",
                    "yes_no": True,
                    "answer_text": "Tuned SQL, execution plans, and indexes for regulatory reporting workloads.",
                }
            ],
        )
        evidence_id = saved[0]["evidence_id"]

        deleted = self.service.delete_evidence_answer("user-1", evidence_id)

        self.assertEqual(deleted["evidence_id"], evidence_id)
        self.assertEqual(self.service.list_evidence_answers("user-1"), [])


if __name__ == "__main__":
    unittest.main()


class ConfirmationSaveActionContractTests(unittest.TestCase):
    def test_active_step_save_button_posts_to_library_route_without_advancing(self) -> None:
        template = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'formaction="{{ url_for(\'application_builder.save_confirmation_to_library\') }}"',
            template,
        )
        self.assertIn('data-skip-loading="true"', template)
        self.assertNotIn('name="confirmation_action"', template)
        self.assertIn("keeps you on this step", template)

    def test_resume_creation_no_longer_implicitly_saves_library_answers(self) -> None:
        apply_section = function_source(CONFIRMATION_ROUTES, "apply_confirmation")
        self.assertNotIn("_save_confirmation_answers_to_library(", apply_section)
        self.assertNotIn("confirmation_action", apply_section)

    def test_library_save_handler_collects_form_answers_and_stays_on_step_two(self) -> None:
        save_section = function_source(
            CONFIRMATION_ROUTES, "save_confirmation_to_library"
        )
        self.assertIn("collect_candidate_answers(questions, request.form)", save_section)
        self.assertIn("current.candidate_answers = [", save_section)
        self.assertIn("_save_confirmation_answers_to_library(", save_section)
        self.assertIn('stage="confirmation"', save_section)
        self.assertNotIn('current.workflow_stage = "draft"', save_section)
        self.assertNotIn("refine_proposal", save_section)

    def test_library_save_skips_resume_generation_loading_overlay(self) -> None:
        javascript = (
            ROOT / "products" / "resume_taylor" / "static" / "app-shell.js"
        ).read_text(encoding="utf-8")
        self.assertIn("submitter?.dataset.skipLoading === 'true'", javascript)

class CareerEvidenceLibraryRemoveQuestionContractTests(unittest.TestCase):
    def test_remove_question_action_is_next_to_save(self) -> None:
        template = (
            ROOT / "products" / "reunia" / "templates" / "knowledge.html"
        ).read_text(encoding="utf-8")
        action_section = template.split(
            '<td class="reusable-evidence-actions">', 1
        )[1].split("</td>", 1)[0]
        self.assertIn('class="reusable-evidence-action-row"', action_section)
        self.assertIn(">Save</button>", action_section)
        self.assertIn("data-delete-evidence-answer", action_section)
        self.assertIn(">Remove question</button>", action_section)
        self.assertLess(action_section.index(">Save</button>"), action_section.index(">Remove question</button>"))

    def test_remove_question_uses_dedicated_delete_request(self) -> None:
        javascript = (
            ROOT / "products" / "reunia" / "static" / "js" / "pages" / "knowledge.js"
        ).read_text(encoding="utf-8")
        delete_section = javascript.split(
            "document.querySelectorAll('[data-delete-evidence-answer]')", 1
        )[1].split("// Initialize the active workspace.", 1)[0]
        self.assertIn("method: 'DELETE'", delete_section)
        self.assertIn("Future applications may ask it again", delete_section)
        self.assertIn("Question removed from Career Evidence Library.", delete_section)
        self.assertIn("remaining === 0", delete_section)

    def test_delete_api_and_service_are_owner_scoped(self) -> None:
        routes = (
            ROOT
            / "products"
            / "reunia"
            / "meeting_assistant"
            / "blueprints"
            / "knowledge"
            / "routes.py"
        ).read_text(encoding="utf-8")
        self.assertIn('@knowledge_bp.delete("/api/career/evidence/answers/<evidence_id>")', routes)
        self.assertIn("g.current_user_id, evidence_id", routes)
