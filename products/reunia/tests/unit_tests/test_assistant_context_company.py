from __future__ import annotations

import pytest
from flask import Flask

from meeting_assistant.services.live_qa_service import LiveQAService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.exceptions import ValidationError


class _UserRepository:
    def __init__(self, user: dict | None = None) -> None:
        self.user = user or {
            "user_id": "user-1",
            "settings": {},
            "assistant_context": {},
        }
        self.saved_settings: dict | None = None
        self.saved_fields: dict | None = None

    def get_by_id(self, user_id: str):
        return self.user

    def update_settings(self, user_id: str, settings: dict):
        self.saved_settings = dict(settings)
        self.user["settings"] = dict(settings)
        return {"settings": settings}

    def update_fields(self, user_id: str, fields: dict):
        self.saved_fields = dict(fields)
        self.user.update(fields)
        return fields


def _app() -> Flask:
    app = Flask(__name__)
    app.config["DEFAULT_AI_MODEL"] = "test-model"
    return app


def test_settings_remove_legacy_assistant_context_values() -> None:
    repository = _UserRepository(
        {
            "user_id": "user-1",
            "settings": {
                "chatGPTRole": "Legacy Role",
                "chatGPT_role": "Legacy Role Alias",
                "chatGPTCompany": "Legacy Company",
                "chatGPT_company": "Legacy Alias",
                "chatGPTLink": "https://legacy.example.com",
                "chatGPT_link": "https://legacy-alias.example.com",
                "chatGPTPromptAudio": "Legacy audio prompt",
                "chatGPT_prompt_audio": "Legacy audio prompt alias",
                "chatGPTPromptClipboard": "Legacy clipboard prompt",
                "chatGPT_prompt_clipboard": "Legacy clipboard prompt alias",
            },
            "assistant_context": {"company": "Shared Company"},
        }
    )

    with _app().app_context():
        settings = UserService(repository=repository).get_settings("user-1")

    assert "chatGPTRole" not in settings
    assert "chatGPT_role" not in settings
    assert "chatGPTCompany" not in settings
    assert "chatGPT_company" not in settings
    assert "chatGPTLink" not in settings
    assert "chatGPT_link" not in settings
    assert "chatGPTPromptAudio" not in settings
    assert "chatGPT_prompt_audio" not in settings
    assert "chatGPTPromptClipboard" not in settings
    assert "chatGPT_prompt_clipboard" not in settings


def test_settings_update_ignores_assistant_context_payload() -> None:
    repository = _UserRepository()

    with _app().app_context():
        result = UserService(repository=repository).update_settings(
            "user-1",
            {
                "chatGPTRole": "Should Not Be Saved",
                "chatGPT_role": "Should Not Be Saved",
                "chatGPTCompany": "Should Not Be Saved",
                "chatGPT_company": "Should Not Be Saved",
                "chatGPTLink": "https://should-not-save.example.com",
                "chatGPT_link": "https://should-not-save-alias.example.com",
                "chatGPTPromptAudio": "Should Not Be Saved",
                "chatGPT_prompt_audio": "Should Not Be Saved",
                "chatGPTPromptClipboard": "Should Not Be Saved",
                "chatGPT_prompt_clipboard": "Should Not Be Saved",
            },
        )

    assert "chatGPTRole" not in result
    assert "chatGPT_role" not in result
    assert "chatGPTCompany" not in result
    assert "chatGPT_company" not in result
    assert "chatGPTLink" not in result
    assert "chatGPT_link" not in result
    assert "chatGPTPromptAudio" not in result
    assert "chatGPT_prompt_audio" not in result
    assert "chatGPTPromptClipboard" not in result
    assert "chatGPT_prompt_clipboard" not in result
    assert "chatGPTRole" not in repository.saved_settings
    assert "chatGPT_role" not in repository.saved_settings
    assert "chatGPTCompany" not in repository.saved_settings
    assert "chatGPT_company" not in repository.saved_settings
    assert "chatGPTLink" not in repository.saved_settings
    assert "chatGPT_link" not in repository.saved_settings
    assert "chatGPTPromptAudio" not in repository.saved_settings
    assert "chatGPT_prompt_audio" not in repository.saved_settings
    assert "chatGPTPromptClipboard" not in repository.saved_settings
    assert "chatGPT_prompt_clipboard" not in repository.saved_settings


def test_ai_context_company_and_reference_link_are_saved_in_shared_user_context() -> None:
    repository = _UserRepository()

    with _app().app_context():
        context = UserService(repository=repository).update_assistant_context(
            "user-1",
            {
                "use_context": True,
                "context_company": "Shared Company",
                "context_reference_link": "https://example.com/reference",
                "context_response_mode": "coaching",
                "context_audio_response_instructions": (
                    "Keep spoken answers under one minute."
                ),
                "context_clipboard_response_instructions": (
                    "Return code without Markdown fences when code is requested."
                ),
            },
        )

    assert context["enabled"] is True
    assert context["company"] == "Shared Company"
    assert context["reference_link"] == "https://example.com/reference"
    assert context["response_mode"] == "coaching"
    assert context["audio_response_instructions"] == (
        "Keep spoken answers under one minute."
    )
    assert context["clipboard_response_instructions"] == (
        "Return code without Markdown fences when code is requested."
    )
    assert repository.saved_fields == {
        "assistant_context": context,
    }


def test_live_qa_audio_prompt_is_generated_from_shared_ai_context() -> None:
    prompt = LiveQAService._build_prompt(
        "microphone",
        {
            "chatGPTPromptAudio": "Legacy prompt that must be ignored.",
            "chatGPTRole": "Legacy Engineer",
            "chatGPTCompany": "Legacy Company",
        },
        "Client prompt that must also be ignored.",
        {
            "enabled": True,
            "company": "Toyota",
            "role": "Quality engineer",
            "reference_link": "https://careers.toyota.com/us/en/job/10330125/Quality-Engineer",
            "type": "interview",
            "domain": "Automotive quality",
            "audience": "Hiring manager",
            "answer_style": "concise",
            "response_mode": "ready_to_say",
            "audio_response_instructions": (
                "Keep the response conversational and under 60 seconds."
            ),
            "objective": "Prepare strong interview answers",
            "free_text": "Emphasize continuous improvement experience.",
        },
    )

    assert "Act as an expert career coach and recruiter." in prompt
    assert "I am preparing for a job interview for the role described in the AI Context section." in prompt
    assert "Use the company, role, objective, reference link" in prompt
    assert "Keep the answer concise." in prompt
    assert "Respond with a complete answer that I can use directly." in prompt
    assert "write in the first person as if you are me" in prompt
    assert "Additional audio response instructions:" in prompt
    assert "Keep the response conversational and under 60 seconds." in prompt
    assert "Response mode: Ready-to-say answer" in prompt
    assert "Company or organization: Toyota" in prompt
    assert "Role or position: Quality engineer" in prompt
    assert "Reference link: https://careers.toyota.com/us/en/job/10330125/Quality-Engineer" in prompt
    assert "Meeting or task type: interview" in prompt
    assert "Domain or subject area: Automotive quality" in prompt
    assert "Audience: Hiring manager" in prompt
    assert "Primary objective: Prepare strong interview answers" in prompt
    assert "Additional notes: Emphasize continuous improvement experience." in prompt
    assert "Legacy prompt that must be ignored" not in prompt
    assert "Client prompt that must also be ignored" not in prompt
    assert "Legacy Engineer" not in prompt
    assert "Legacy Company" not in prompt


def test_live_qa_audio_prompt_respects_paused_ai_context() -> None:
    prompt = LiveQAService._build_prompt(
        "microphone",
        {"chatGPTPromptAudio": "Legacy prompt that must be ignored."},
        "Client prompt that must be ignored.",
        {
            "enabled": False,
            "role": "Paused Role",
            "objective": "Paused Objective",
            "company": "Paused Company",
            "reference_link": "https://paused.example.com",
            "type": "interview",
        },
    )

    assert "Act as an expert meeting assistant and subject-matter advisor." in prompt
    assert "I need help with a live meeting question or task." in prompt
    assert "Paused Role" not in prompt
    assert "Paused Objective" not in prompt
    assert "Paused Company" not in prompt
    assert "https://paused.example.com" not in prompt
    assert "Legacy prompt that must be ignored" not in prompt
    assert "Client prompt that must be ignored" not in prompt


def test_live_qa_clipboard_prompt_is_generated_from_shared_ai_context() -> None:
    prompt = LiveQAService._build_prompt(
        "clipboard",
        {"chatGPTPromptClipboard": "Legacy prompt that must be ignored."},
        "Client prompt that must also be ignored.",
        {
            "enabled": True,
            "company": "Toyota",
            "role": "Quality engineer",
            "reference_link": "https://careers.toyota.com/us/en/job/10330125/Quality-Engineer",
            "type": "interview",
            "answer_style": "concise",
            "response_mode": "ready_to_say",
            "audio_response_instructions": "Keep spoken answers short.",
            "clipboard_response_instructions": (
                "Respond only using plain text, Python code, or SQL code. "
                "Do not include introductory text."
            ),
        },
    )

    assert prompt.startswith(
        "I am doing a technical interview for the role described in the AI Context section."
    )
    assert "Use the company, role, objective, reference link" in prompt
    assert "Keep the answer concise." in prompt
    assert "Respond only using plain text, Python code, or SQL code" in prompt
    assert "Additional clipboard response instructions:" in prompt
    assert "Do not include introductory text." in prompt
    assert "Respond with a complete answer that I can use directly." in prompt
    assert "Company or organization: Toyota" in prompt
    assert "Role or position: Quality engineer" in prompt
    assert "Reference link: https://careers.toyota.com/us/en/job/10330125/Quality-Engineer" in prompt
    assert "Legacy prompt that must be ignored" not in prompt
    assert "Client prompt that must also be ignored" not in prompt
    assert "Keep spoken answers short." not in prompt
    assert "Additional audio response instructions" not in prompt


def test_clipboard_response_instructions_are_not_added_to_audio_prompts() -> None:
    prompt = LiveQAService._build_prompt(
        "microphone",
        {},
        None,
        {
            "enabled": True,
            "type": "interview",
            "clipboard_response_instructions": "Return SQL only.",
        },
    )

    assert "Return SQL only." not in prompt
    assert "Additional clipboard response instructions" not in prompt


def test_audio_response_instructions_are_not_added_to_clipboard_prompts() -> None:
    prompt = LiveQAService._build_prompt(
        "clipboard",
        {},
        None,
        {
            "enabled": True,
            "type": "interview",
            "audio_response_instructions": "Keep the spoken response under one minute.",
        },
    )

    assert "Keep the spoken response under one minute." not in prompt
    assert "Additional audio response instructions" not in prompt


def test_ai_context_defaults_audio_response_instructions_to_empty() -> None:
    repository = _UserRepository()

    with _app().app_context():
        context = UserService(repository=repository).get_assistant_context("user-1")

    assert context["audio_response_instructions"] == ""


def test_ai_context_defaults_clipboard_response_instructions_to_empty() -> None:
    repository = _UserRepository()

    with _app().app_context():
        context = UserService(repository=repository).get_assistant_context("user-1")

    assert context["clipboard_response_instructions"] == ""


def test_live_qa_coaching_response_mode_allows_guidance() -> None:
    prompt = LiveQAService._build_prompt(
        "speaker",
        {},
        None,
        {
            "enabled": True,
            "type": "interview",
            "response_mode": "coaching",
        },
    )

    assert "Provide coaching guidance, recommended talking points, and strategy." in prompt
    assert "write in the first person as if you are me" not in prompt
    assert "Response mode: Coaching guidance" in prompt


def test_ai_context_defaults_to_ready_to_say_response_mode() -> None:
    repository = _UserRepository()

    with _app().app_context():
        context = UserService(repository=repository).get_assistant_context("user-1")

    assert context["response_mode"] == "ready_to_say"


def test_ai_context_rejects_invalid_reference_link() -> None:
    repository = _UserRepository()

    with _app().app_context(), pytest.raises(ValidationError, match="http:// or https://"):
        UserService(repository=repository).update_assistant_context(
            "user-1",
            {"context_reference_link": "example.com/reference"},
        )
