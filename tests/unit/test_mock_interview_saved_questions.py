from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

try:
    from flask import Flask
    from werkzeug.datastructures import FileStorage
except ModuleNotFoundError:  # pragma: no cover - dependency-light validation images
    Flask = None
    FileStorage = None

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "products" / "reunia", ROOT / "products" / "resume_taylor"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

if Flask is not None:
    from meeting_assistant.services.mock_interview_service import MockInterviewService
    from meeting_assistant.services.user_service import UserService
else:  # pragma: no cover - classes below are skipped
    MockInterviewService = None
    UserService = None


class FakeUserRepository:
    def __init__(self) -> None:
        self.user = {"user_id": "user-1"}

    def get_by_id(self, user_id: str):
        return dict(self.user) if user_id == self.user["user_id"] else None

    def update_fields(self, user_id: str, fields: dict):
        if user_id != self.user["user_id"]:
            raise AssertionError("unexpected user")
        self.user.update(fields)
        return dict(fields)


class FakeMockInterviewSessionStore:
    def __init__(self) -> None:
        self.jobs = {}

    def get_mock_interview_session(self, owner_id: str, session_id: str):
        payload = self.jobs.get((owner_id, session_id))
        if payload is None:
            return None
        return {"owner_id": owner_id, "session_id": session_id, "payload": dict(payload)}

    def save_mock_interview_session(
        self, owner_id: str, session_id: str, *, application_id: str, payload_json: str
    ):
        import json

        payload = json.loads(payload_json)
        self.jobs[(owner_id, session_id)] = dict(payload)
        return {
            "owner_id": owner_id,
            "session_id": session_id,
            "application_id": application_id,
            "payload": dict(payload),
        }

    def delete_mock_interview_session(self, owner_id: str, session_id: str) -> bool:
        return self.jobs.pop((owner_id, session_id), None) is not None


class FakeTranscriptionService:
    def transcribe_upload(self, *_args, **_kwargs):
        return {"text": "I used a confirmed example and explained the result.", "quality": {}}


class FakeTranscriptService:
    pass


class FakeMaterialsService:
    def list_meetings(self, *_args, **_kwargs):
        return []


class FakeMockUserService:
    def __init__(self, question_set: dict) -> None:
        self.question_set = question_set

    def get_mock_interview_question_set(self, user_id: str, question_set_id: str):
        self.last_lookup = (user_id, question_set_id)
        return dict(self.question_set)

    def get_settings(self, _user_id: str):
        return {"language": "en", "aiModel": "test-model"}

    def get_assistant_context(self, _user_id: str):
        return {}


@unittest.skipIf(Flask is None, "Flask runtime dependencies are not installed")
class SavedQuestionSetPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.config.update(DEFAULT_AI_MODEL="test-model")

    def test_user_can_create_update_list_and_load_it_later(self):
        repository = FakeUserRepository()
        service = UserService(repository=repository)

        with self.app.app_context():
            created = service.save_mock_interview_question_set(
                "user-1",
                {
                    "name": "Bank interview",
                    "questions": [
                        "Tell me about yourself.",
                        "Why are you interested in this role?",
                    ],
                },
            )
            self.assertTrue(created["id"].startswith("questions-"))
            self.assertEqual(2, len(created["questions"]))

            reloaded_service = UserService(repository=repository)
            loaded = reloaded_service.get_mock_interview_question_set(
                "user-1", created["id"]
            )
            self.assertEqual("Bank interview", loaded["name"])

            updated = reloaded_service.save_mock_interview_question_set(
                "user-1",
                {
                    "id": created["id"],
                    "name": "Bank panel interview",
                    "questions": ["What is your strongest relevant example?"],
                },
            )
            self.assertEqual(created["id"], updated["id"])
            self.assertEqual(["What is your strongest relevant example?"], updated["questions"])
            self.assertEqual(1, len(reloaded_service.list_mock_interview_question_sets("user-1")))

    def test_question_list_validation_limits_size(self):
        service = UserService(repository=FakeUserRepository())
        with self.app.app_context():
            with self.assertRaisesRegex(Exception, "at most 20"):
                service.save_mock_interview_question_set(
                    "user-1",
                    {
                        "name": "Too many",
                        "questions": [f"Question {index}?" for index in range(21)],
                    },
                )


@unittest.skipIf(Flask is None, "Flask runtime dependencies are not installed")
class SavedQuestionPracticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.config.update(DEFAULT_AI_MODEL="test-model")
        self.store = FakeMockInterviewSessionStore()

    def _service(self) -> MockInterviewService:
        user_service = FakeMockUserService(
            {
                "id": "questions-123",
                "name": "Technical panel",
                "questions": [
                    "Explain your most relevant project.",
                    "Describe a technical tradeoff you made.",
                    "What would you improve next?",
                ],
            }
        )
        return MockInterviewService(
            transcription_service=FakeTranscriptionService(),
            transcript_service=FakeTranscriptService(),
            user_service=user_service,
            materials_service=FakeMaterialsService(),
            session_store=self.store,
        )

    def test_saved_questions_are_copied_into_session_and_asked_in_order(self):
        with self.app.app_context():
            service = self._service()
            session = service.create_session(
                "user-1",
                {
                    "interview_type": "saved_questions",
                    "question_set_id": "questions-123",
                    "language": "en",
                },
            )
            self.assertEqual("saved_question_set", session["question_mode"])
            self.assertEqual(3, session["question_count"])
            self.assertEqual("Explain your most relevant project.", session["current_question"])
            self.assertEqual("My questions — Technical panel", session["interview_type_label"])

            service._evaluate_and_follow_up = lambda **_kwargs: {
                "evaluation": {
                    "score": 80,
                    "summary": "Relevant answer.",
                    "strengths": ["Specific example."],
                    "improvements": [],
                    "evidence_status": "supported",
                    "challenge_needed": False,
                    "metrics": {},
                },
                "observable_delivery": {},
                "next_question": "AI-generated question that must not be used.",
                "next_question_type": "follow_up",
                "rationale": "AI rationale",
            }
            result = service.submit_answer(
                "user-1",
                session["session_id"],
                FileStorage(stream=io.BytesIO(b"audio"), filename="answer.webm"),
                language="en",
                duration_seconds=45,
            )
            self.assertEqual("Describe a technical tradeoff you made.", result["current_question"])
            self.assertEqual("saved_question", result["current_question_type"])
            self.assertEqual("questions-123", result["question_set_id"])

    def test_skipping_saved_question_moves_to_next_question_and_tracks_progress(self):
        with self.app.app_context():
            service = self._service()
            session = service.create_session(
                "user-1",
                {
                    "interview_type": "saved_questions",
                    "question_set_id": "questions-123",
                    "language": "en",
                },
            )
            result = service.skip_question("user-1", session["session_id"])
            self.assertEqual("Describe a technical tradeoff you made.", result["current_question"])
            self.assertEqual(1, result["skipped_count"])
            self.assertEqual(1, result["completed_question_count"])
            self.assertEqual(2, result["current_question_number"])
            self.assertFalse(result["complete"])

    def test_last_question_can_be_skipped_after_at_least_one_answer(self):
        with self.app.app_context():
            service = self._service()
            session = service.create_session(
                "user-1",
                {
                    "interview_type": "saved_questions",
                    "question_set_id": "questions-123",
                    "language": "en",
                },
            )
            service._evaluate_and_follow_up = lambda **_kwargs: {
                "evaluation": {
                    "score": 80,
                    "summary": "Relevant answer.",
                    "strengths": ["Specific example."],
                    "improvements": [],
                    "evidence_status": "supported",
                    "challenge_needed": False,
                    "metrics": {},
                },
                "observable_delivery": {},
                "next_question": "Unused adaptive question.",
                "next_question_type": "follow_up",
                "rationale": "Unused rationale.",
            }
            service.submit_answer(
                "user-1",
                session["session_id"],
                FileStorage(stream=io.BytesIO(b"audio"), filename="answer.webm"),
                language="en",
                duration_seconds=45,
            )
            service.skip_question("user-1", session["session_id"])
            result = service.skip_question("user-1", session["session_id"])
            self.assertTrue(result["complete"])
            self.assertEqual("ready_for_review", result["status"])
            self.assertEqual(1, result["answered_count"])
            self.assertEqual(2, result["skipped_count"])
            self.assertEqual(3, result["completed_question_count"])

    def test_cannot_skip_every_question_without_answering(self):
        with self.app.app_context():
            service = self._service()
            session = service.create_session(
                "user-1",
                {
                    "interview_type": "saved_questions",
                    "question_set_id": "questions-123",
                    "language": "en",
                },
            )
            service.skip_question("user-1", session["session_id"])
            service.skip_question("user-1", session["session_id"])
            with self.assertRaisesRegex(Exception, "Answer at least one question"):
                service.skip_question("user-1", session["session_id"])


if __name__ == "__main__":
    unittest.main()
