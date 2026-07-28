from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest

from meeting_assistant.repositories.knowledge_file_store import LocalKnowledgeFileStore
from meeting_assistant.services.knowledge_search_service import (
    KnowledgeSearchService,
    _unique_sources,
)
from meeting_assistant.utils.exceptions import ValidationError


def _authenticate(client, user_id: str = "user-1") -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id


class _FakeChatCompletions:
    def __init__(self, answer: str = "Project Aurora uses blue branding.") -> None:
        self.answer = answer
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer))]
        )


class _FakeOpenAIClient:
    def __init__(self, answer: str = "Project Aurora uses blue branding.") -> None:
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(answer))


class _FakeUserService:
    def get_settings(self, user_id: str):
        return {"aiModel": "test-model"}


class _EmptyTranscriptService:
    def list_for_user(self, user_id: str):
        return []


def test_knowledge_search_route_is_registered(app):
    rules = {
        (rule.rule, method)
        for rule in app.url_map.iter_rules()
        for method in rule.methods
    }
    assert ("/api/knowledge/ask", "POST") in rules


def test_knowledge_search_route_calls_backend(app, monkeypatch):
    class _FakeSearchService:
        def answer(self, user_id, payload):
            assert user_id == "user-1"
            assert payload["question"] == "What changed?"
            return {"answer": "The plan changed.", "sources": []}

    monkeypatch.setattr(
        "meeting_assistant.blueprints.knowledge.routes.KnowledgeSearchService",
        _FakeSearchService,
    )
    client = app.test_client()
    _authenticate(client)

    response = client.post(
        "/api/knowledge/ask",
        json={"question": "What changed?", "source_scope": "library"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"answer": "The plan changed.", "sources": []}


def test_knowledge_search_answers_from_document_library(app, tmp_path):
    app.extensions["knowledge_file_store"] = LocalKnowledgeFileStore(tmp_path / "files")
    client = app.test_client()
    _authenticate(client)

    uploaded = client.post(
        "/api/knowledge/files",
        data={
            "files": (
                BytesIO(b"Project Aurora uses blue branding for the July launch."),
                "aurora.txt",
            )
        },
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 201

    fake_client = _FakeOpenAIClient()
    with app.app_context():
        service = KnowledgeSearchService(
            client=fake_client,
            transcript_service=_EmptyTranscriptService(),
            user_service=_FakeUserService(),
        )
        result = service.answer(
            "user-1",
            {
                "question": "What branding color does Project Aurora use?",
                "source_scope": "library",
                "collection_ids": [],
            },
        )

    assert result["answer"] == "Project Aurora uses blue branding."
    assert result["sources"][0]["filename"] == "aurora.txt"
    request = fake_client.chat.completions.requests[0]
    assert request["model"] == "test-model"
    assert "Project Aurora uses blue branding" in request["messages"][1]["content"]


def test_knowledge_search_returns_helpful_message_without_sources(app):
    fake_client = _FakeOpenAIClient()
    with app.app_context():
        service = KnowledgeSearchService(
            client=fake_client,
            transcript_service=_EmptyTranscriptService(),
            user_service=_FakeUserService(),
        )
        result = service.answer(
            "user-1",
            {"question": "What is the contract value?", "source_scope": "library"},
        )

    assert "couldn't find relevant information" in result["answer"]
    assert result["sources"] == []
    assert fake_client.chat.completions.requests == []


def test_knowledge_search_lists_each_document_source_once():
    selected = [
        {
            "text": "First matching excerpt.",
            "source": {
                "source_type": "Document",
                "filename": "Interview preparation document",
                "file_id": "file-123",
                "section": "Page 1",
            },
        },
        {
            "text": "Second matching excerpt.",
            "source": {
                "source_type": "Document",
                "filename": "Interview preparation document",
                "file_id": "file-123",
                "section": "Page 2",
            },
        },
        {
            "text": "Third matching excerpt.",
            "source": {
                "source_type": "Document",
                "filename": "Interview preparation document",
                "file_id": "file-123",
                "section": "Page 3",
            },
        },
    ]

    sources = _unique_sources(selected)

    assert len(sources) == 1
    assert sources[0]["filename"] == "Interview preparation document"
    assert sources[0]["excerpt_count"] == 3


def test_knowledge_search_keeps_different_files_with_same_name():
    selected = [
        {
            "text": "Excerpt from the first file.",
            "source": {
                "source_type": "Document",
                "filename": "Notes.txt",
                "file_id": "file-1",
            },
        },
        {
            "text": "Excerpt from the second file.",
            "source": {
                "source_type": "Document",
                "filename": "Notes.txt",
                "file_id": "file-2",
            },
        },
    ]

    sources = _unique_sources(selected)

    assert len(sources) == 2


class _ScopedEvidenceSearchService(KnowledgeSearchService):
    def _meeting_evidence(self, user_id, question, payload):
        has_selected_meeting = bool(payload.get("meeting_id") or payload.get("meeting_ids"))
        if has_selected_meeting:
            return [
                {
                    "text": "The selected meeting approved the launch budget.",
                    "score": 0.8,
                    "source": {
                        "source_type": "Previous Meeting",
                        "meeting_name": "Selected launch review",
                        "meeting_id": "meeting-1",
                        "section": "Summary",
                    },
                }
            ]
        return [
            {
                "text": "An earlier planning meeting proposed a smaller launch budget.",
                "score": 0.7,
                "source": {
                    "source_type": "Previous Meeting",
                    "meeting_name": "Earlier planning meeting",
                    "meeting_id": "meeting-2",
                    "section": "Summary",
                },
            }
        ]

    def _library_evidence(self, user_id, question, payload):
        return [
            {
                "text": "The uploaded launch policy requires finance approval for budget changes.",
                "score": 0.75,
                "source": {
                    "source_type": "Document",
                    "filename": "Launch policy.txt",
                    "file_id": "file-1",
                    "section": "Budget approvals",
                },
            }
        ]


class _MeetingAnalysisTranscriptService:
    def list_for_user(self, user_id):
        return [
            {
                "meeting_id": "meeting-1",
                "meeting_name": "Customer interview",
                "timestamp": "2026-07-16T10:00:00Z",
                "summary": "The customer approved the proposed workflow.",
                "action_items": [{"task": "Send the revised plan", "owner": "Vincent"}],
                "open_questions": ["When should implementation begin?"],
                "key_wins": ["Clear explanation of the workflow"],
                "improvement_areas": ["Answer pricing questions more directly"],
                "final_grade": 88.5,
                "content_average_score": 90.0,
                "form_average_score": 85.7,
                "scorecard_source": "microphone",
                "form_metrics": {
                    "pace_wpm": 132,
                    "overall_assessment": "Clear and concise delivery.",
                },
                "content_grades": [
                    {
                        "question": "Why this workflow?",
                        "answer": "It reduces manual work.",
                        "grade": "A",
                        "analysis": "The answer was relevant and concise.",
                    }
                ],
                "transcript": "The customer asked about the workflow and approved it.",
            }
        ]


def test_meeting_review_scope_requires_a_selected_meeting(app):
    with app.app_context():
        service = KnowledgeSearchService(
            client=_FakeOpenAIClient(),
            transcript_service=_EmptyTranscriptService(),
            user_service=_FakeUserService(),
        )
        with pytest.raises(ValidationError, match="Select a meeting"):
            service.answer(
                "user-1",
                {"question": "What was decided?", "source_scope": "this_meeting"},
            )


def test_meeting_review_default_uses_only_selected_meeting(app):
    fake_client = _FakeOpenAIClient()
    with app.app_context():
        service = _ScopedEvidenceSearchService(
            client=fake_client,
            transcript_service=_EmptyTranscriptService(),
            user_service=_FakeUserService(),
        )
        result = service.answer(
            "user-1",
            {
                "question": "What launch budget was approved?",
                "source_scope": "this_meeting",
                "meeting_id": "meeting-1",
                "meeting_ids": ["meeting-1"],
            },
        )

    prompt = fake_client.chat.completions.requests[0]["messages"][1]["content"]
    assert "selected meeting approved the launch budget" in prompt
    assert "earlier planning meeting" not in prompt
    assert "uploaded launch policy" not in prompt
    assert [source["meeting_id"] for source in result["sources"]] == ["meeting-1"]


def test_meeting_review_related_scope_supplements_selected_meeting(app):
    fake_client = _FakeOpenAIClient()
    with app.app_context():
        service = _ScopedEvidenceSearchService(
            client=fake_client,
            transcript_service=_EmptyTranscriptService(),
            user_service=_FakeUserService(),
        )
        result = service.answer(
            "user-1",
            {
                "question": "What launch budget approval applies?",
                "source_scope": "meeting_review_related",
                "meeting_id": "meeting-1",
                "meeting_ids": ["meeting-1"],
                "include_related_knowledge": True,
            },
        )

    request = fake_client.chat.completions.requests[0]
    system_prompt = request["messages"][0]["content"]
    user_prompt = request["messages"][1]["content"]
    assert "selected meeting as the primary source" in system_prompt
    assert "selected meeting approved the launch budget" in user_prompt
    assert "earlier planning meeting proposed" in user_prompt
    assert "uploaded launch policy" in user_prompt
    assert {source.get("meeting_id") or source.get("file_id") for source in result["sources"]} == {
        "meeting-1",
        "meeting-2",
        "file-1",
    }


def test_completed_meeting_evidence_includes_scorecard_and_analysis(app):
    with app.app_context():
        service = KnowledgeSearchService(
            client=_FakeOpenAIClient(),
            transcript_service=_MeetingAnalysisTranscriptService(),
            user_service=_FakeUserService(),
        )

        evidence = service._meeting_evidence(
            "user-1",
            "What should I improve based on the scorecard?",
            {"meeting_id": "meeting-1", "meeting_ids": ["meeting-1"]},
        )
    sections = {item["source"]["section"] for item in evidence}
    combined_text = "\n".join(item["text"] for item in evidence)

    assert "Scorecard and communication analysis" in sections
    assert "Answer analysis" in sections
    assert "Key wins" in sections
    assert "Improvement areas" in sections
    assert "Clear and concise delivery" in combined_text
    assert "Answer pricing questions more directly" in combined_text
