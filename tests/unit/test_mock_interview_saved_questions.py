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


class FakeRecorderJobStore:
    def __init__(self) -> None:
        self.jobs = {}

    def create(self, job_id: str) -> None:
        if job_id in self.jobs:
            raise FileExistsError(job_id)
        self.jobs[job_id] = {}

    def write(self, job: dict) -> None:
        self.jobs[job["job_id"]] = dict(job)

    def read(self, job_id: str) -> dict:
        return dict(self.jobs[job_id])

    def remove(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


class FakeRecorderService:
    def transcribe_live_upload(self, *_args, **_kwargs):
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
        self.store = FakeRecorderJobStore()
        self.app.extensions["recorder_job_store"] = self.store

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
            recorder_service=FakeRecorderService(),
            transcript_service=FakeTranscriptService(),
            user_service=user_service,
            materials_service=FakeMaterialsService(),
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


if __name__ == "__main__":
    unittest.main()
