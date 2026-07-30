"""Isolated dependency-light runner for the scorecard-to-action integration.

The production ActionService imports Flask's ``current_app`` only for extension
lookup and error logging. This runner supplies a minimal current-app stand-in so
that the real service, repository, application store, grounding validator, and
action derivation logic can be exercised even in an offline validation image
where Flask itself is unavailable.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REUNIA_ROOT = ROOT / "products" / "reunia"
RESUME_ROOT = ROOT / "products" / "resume_taylor"
for path in (str(RESUME_ROOT), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_runtime_modules():
    """Load ActionService without executing the full Flask application package."""

    meeting_assistant_root = REUNIA_ROOT / "meeting_assistant"

    package = types.ModuleType("meeting_assistant")
    package.__path__ = [str(meeting_assistant_root)]
    sys.modules["meeting_assistant"] = package

    services_package = types.ModuleType("meeting_assistant.services")
    services_package.__path__ = [str(meeting_assistant_root / "services")]
    sys.modules["meeting_assistant.services"] = services_package

    # ActionService only needs the class for its optional constructor default.
    # The test supplies a concrete transcript-service double.
    transcript_module = types.ModuleType(
        "meeting_assistant.services.transcript_service"
    )
    transcript_module.TranscriptService = type("TranscriptService", (), {})
    sys.modules[transcript_module.__name__] = transcript_module

    class _Logger:
        def exception(self, *_args, **_kwargs) -> None:
            return None

    class _CurrentApp:
        logger = _Logger()
        extensions: dict[str, object] = {}

    flask_module = types.ModuleType("flask")
    flask_module.current_app = _CurrentApp()
    sys.modules["flask"] = flask_module

    from meeting_assistant.repositories.action_repository import (  # noqa: PLC0415
        InMemoryActionRepository,
    )
    from meeting_assistant.services.action_service import ActionService  # noqa: PLC0415
    from products.resume_taylor.resume_tailor.application_tracker import (  # noqa: PLC0415
        SQLiteApplicationStore,
    )

    return ActionService, InMemoryActionRepository, SQLiteApplicationStore


class _TranscriptService:
    def __init__(self, reviews: list[dict[str, object]]) -> None:
        self._reviews = reviews

    def list_for_user(self, user_id: str) -> list[dict[str, object]]:
        del user_id
        return self._reviews


class _EmptyWorkflowStore:
    def get(self, _key: str) -> object:
        return types.SimpleNamespace()


def main() -> int:
    ActionService, InMemoryActionRepository, SQLiteApplicationStore = (
        _load_runtime_modules()
    )

    owner_id = "scorecard-action-integration-user"
    application_store = SQLiteApplicationStore(":memory:")
    application = application_store.create(
        owner_id,
        company="Northwest Systems",
        role="Senior Data Engineer",
        status="interviewing",
        job_description="Build Python data services backed by PostgreSQL.",
        workflow_step="evidence_export",
    )
    reviews: list[dict[str, object]] = [
        {
            "meeting_id": "mock-scorecard-001",
            "timestamp": "2026-07-29T16:00:00+00:00",
            "scorecard_type": "interview",
            "career_application_id": application.id,
            "overall_score": 52,
            "interview_type": "Technical interview",
            "interview_answer_reviews": [
                {
                    "question_number": "1",
                    "question": "Describe a data service you built.",
                    "answer": (
                        "I built Python REST APIs and stored payment data in PostgreSQL."
                    ),
                    "score": 42,
                    "recommended_practice_action": (
                        "Highlight your 12 years leading SAP S/4HANA transformations "
                        "at Google."
                    ),
                },
                {
                    "question_number": "2",
                    "question": "How do you validate data quality?",
                    "answer": "I use automated checks and reconcile unexpected results.",
                    "score": 82,
                    "recommended_practice_action": "Keep this concise answer structure.",
                },
            ],
        }
    ]

    service = ActionService(
        repository=InMemoryActionRepository(),
        transcript_service=_TranscriptService(reviews),
        application_store=application_store,
        workflow_store=_EmptyWorkflowStore(),
    )
    interview_actions = [
        action
        for action in service.list_for_user(owner_id)
        if action.get("source") == "interview_scorecard"
    ]

    assert len(interview_actions) == 2, interview_actions
    assert all(
        action.get("application_id") == application.id
        for action in interview_actions
    )
    assert all(
        action.get("application_company") == "Northwest Systems"
        for action in interview_actions
    )
    assert all(
        action.get("application_role") == "Senior Data Engineer"
        for action in interview_actions
    )

    weak_answer_action = next(
        action
        for action in interview_actions
        if action.get("link_url") == "/interview-review?meeting=mock-scorecard-001"
    )
    assert weak_answer_action["priority"] == "high"
    assert weak_answer_action["description"] == (
        "Review weak interview answer 1 using one confirmed example and a clear result"
    )
    assert "score of 42" in weak_answer_action["source_detail"]

    repeat_action = next(
        action
        for action in interview_actions
        if action.get("link_url")
        == f"/mock-interview?application_id={application.id}"
    )
    assert repeat_action["priority"] == "high"
    assert "latest interview scorecard was 52" in repeat_action["source_detail"]

    serialized = json.dumps(interview_actions, ensure_ascii=False)
    for forbidden in ("SAP S/4HANA", "Google", "12 years"):
        assert forbidden not in serialized
    assert "question 2" not in serialized.casefold()

    print(
        json.dumps(
            {
                "status": "passed",
                "application_id": application.id,
                "generated_action_count": len(interview_actions),
                "weak_answer_action": weak_answer_action["description"],
                "repeat_action": repeat_action["description"],
                "invented_claims_removed": [
                    "SAP S/4HANA",
                    "Google",
                    "12 years",
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
