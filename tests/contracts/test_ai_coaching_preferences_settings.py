from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETTINGS_TEMPLATE = ROOT / "products/reunia/templates/settings.html"
SETTINGS_JS = ROOT / "products/reunia/static/js/pages/settings.js"
CAREER_TEMPLATE = ROOT / "products/reunia/templates/knowledge.html"
CAREER_JS = ROOT / "products/reunia/static/js/pages/knowledge.js"
USER_SERVICE = ROOT / "products/reunia/meeting_assistant/services/user_service.py"
KNOWLEDGE_ROUTES = ROOT / "products/reunia/meeting_assistant/blueprints/knowledge/routes.py"


class AICoachingPreferencesSettingsContractTests(unittest.TestCase):
    def test_settings_has_dedicated_ai_coaching_category_and_fields(self) -> None:
        source = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('value="ai-coaching-settings"', source)
        self.assertIn('id="ai-coaching-settings"', source)
        for element_id in (
            "aiCoachingAnswerStyle",
            "aiCoachingResponseMode",
            "aiCoachingAudioInstructions",
            "aiCoachingClipboardInstructions",
        ):
            self.assertIn(f'id="{element_id}"', source)
            self.assertIn(f'name="{element_id}"', source)

    def test_settings_frontend_validates_and_saves_preferences(self) -> None:
        source = SETTINGS_JS.read_text(encoding="utf-8")
        for field in (
            "aiCoachingAnswerStyle",
            "aiCoachingResponseMode",
            "aiCoachingAudioInstructions",
            "aiCoachingClipboardInstructions",
        ):
            self.assertIn(field, source)
        self.assertIn("showSettingsScope('ai-coaching-settings')", source)

    def test_backend_defaults_migrates_validates_and_exposes_preferences(self) -> None:
        source = USER_SERVICE.read_text(encoding="utf-8")
        for field in (
            "aiCoachingAnswerStyle",
            "aiCoachingResponseMode",
            "aiCoachingAudioInstructions",
            "aiCoachingClipboardInstructions",
        ):
            self.assertIn(f'"{field}"', source)
        self.assertIn("def get_ai_coaching_preferences", source)
        self.assertIn("context.update(self.get_ai_coaching_preferences(user_id))", source)
        self.assertIn("if destination in _AI_COACHING_CONTEXT_FIELDS", source)
        self.assertIn("legacy_preference_fallbacks", source)
        default_context = source.split("def default_assistant_context", 1)[1].split("_ASSISTANT_CONTEXT_ALIASES", 1)[0]
        for legacy_field in (
            '"answer_style"',
            '"response_mode"',
            '"audio_response_instructions"',
            '"clipboard_response_instructions"',
        ):
            self.assertNotIn(legacy_field, default_context)

    def test_career_profile_no_longer_renders_or_serializes_preferences(self) -> None:
        template = CAREER_TEMPLATE.read_text(encoding="utf-8")
        javascript = CAREER_JS.read_text(encoding="utf-8")
        routes = KNOWLEDGE_ROUTES.read_text(encoding="utf-8")
        for token in (
            "data-context-answer-style",
            "data-context-response-mode",
            "data-context-audio-response-instructions",
            "data-context-clipboard-response-instructions",
        ):
            self.assertNotIn(token, template)
        for token in (
            "context_answer_style",
            "context_response_mode",
            "context_audio_response_instructions",
            "context_clipboard_response_instructions",
        ):
            self.assertNotIn(token, javascript)
        for token in (
            "assistant_context_answer_style",
            "assistant_context_response_mode",
            "assistant_context_audio_response_instructions",
            "assistant_context_clipboard_response_instructions",
        ):
            self.assertNotIn(token, routes)


if __name__ == "__main__":
    unittest.main()
