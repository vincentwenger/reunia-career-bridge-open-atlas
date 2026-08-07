from __future__ import annotations

import unittest

from tests.source_aggregates import MOCK_INTERVIEW_SOURCE
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SavedMockInterviewQuestionListContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_ui_exposes_saved_question_mode_and_library(self):
        template = self.read("products/reunia/templates/meeting-recorder.html")
        for marker in (
            'value="saved_questions"',
            'id="savedQuestionSetPanel"',
            'id="savedQuestionSetSelect"',
            'id="savedQuestionSetQuestions"',
            'id="saveQuestionSetButton"',
        ):
            self.assertIn(marker, template)

    def test_authenticated_crud_routes_are_registered(self):
        routes = self.read(
            "products/reunia/meeting_assistant/blueprints/recorder/routes.py"
        )
        self.assertIn('@recorder_bp.get("/api/career/mock-interviews/question-sets")', routes)
        self.assertIn('@recorder_bp.post("/api/career/mock-interviews/question-sets")', routes)
        self.assertIn(
            '@recorder_bp.delete("/api/career/mock-interviews/question-sets/<question_set_id>")',
            routes,
        )
        self.assertGreaterEqual(routes.count("@api_auth_required"), 3)

    def test_lists_are_stored_on_user_record_and_fixed_in_session(self):
        user_service = self.read(
            "products/reunia/meeting_assistant/services/user_service.py"
        )
        mock_service = MOCK_INTERVIEW_SOURCE.read_text()
        self.assertIn('"mock_interview_question_sets"', user_service)
        self.assertIn('"question_mode": "saved_question_set"', mock_service)
        self.assertIn(
            'session["current_question"] = str(saved_questions[question_number])',
            mock_service,
        )
        self.assertIn('"saved_questions": saved_questions', mock_service)

    def test_session_controls_support_skip_and_discard(self):
        template = self.read("products/reunia/templates/meeting-recorder.html")
        javascript = self.read("products/reunia/static/js/pages/meeting-recorder.js")
        routes = self.read("products/reunia/meeting_assistant/blueprints/recorder/routes.py")
        self.assertIn('id="skipQuestionButton"', template)
        self.assertIn('id="endInterviewButton"', template)
        self.assertIn('Skip question', template)
        self.assertIn('Discard interview', template)
        self.assertIn('skipCurrentQuestion', javascript)
        self.assertIn('/adaptive/sessions/<session_id>/skip', routes)

    def test_browser_flow_auto_saves_before_starting(self):
        javascript = self.read(
            "products/reunia/static/js/pages/meeting-recorder.js"
        )
        self.assertIn("savedQuestionSet = await saveQuestionSet({silent: true});", javascript)
        self.assertIn("question_set_id: savedQuestionSet?.id || ''", javascript)
        self.assertIn("session.question_mode === 'saved_question_set'", javascript)


if __name__ == "__main__":
    unittest.main()
