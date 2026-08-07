"""Contracts ensuring the retired Live Q&A feature cannot reappear."""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.source_aggregates import MOCK_INTERVIEW_SOURCE

ROOT = Path(__file__).resolve().parents[2]


class LiveQARetirementContractTests(unittest.TestCase):
    def test_dedicated_runtime_files_are_removed(self) -> None:
        retired = [
            "products/reunia/meeting_assistant/blueprints/live_qa",
            "products/reunia/meeting_assistant/repositories/live_qa_repository.py",
            "products/reunia/meeting_assistant/services/live_qa_service.py",
            "products/reunia/meeting_assistant/services/browser_recorder_live_service.py",
            "products/reunia/meeting_assistant/services/recorder_live_state_store.py",
            "products/reunia/meeting_assistant/utils/feature_access.py",
            "products/reunia/templates/live-qa.html",
            "products/reunia/static/js/pages/live-qa.js",
            "products/reunia/static/css/pages/live-qa.css",
        ]
        for relative in retired:
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_app_factory_and_extensions_do_not_register_live_qa(self) -> None:
        app_factory = (ROOT / "products/reunia/meeting_assistant/__init__.py").read_text(encoding="utf-8")
        extensions = (ROOT / "products/reunia/meeting_assistant/extensions.py").read_text(encoding="utf-8")
        recorder_routes = (ROOT / "products/reunia/meeting_assistant/blueprints/recorder/routes.py").read_text(encoding="utf-8")
        combined = "\n".join((app_factory, extensions, recorder_routes))
        for retired_token in (
            "live_qa_bp",
            "LIVE_QA_STORAGE_BACKEND",
            "RECORDER_LIVE_STATE_BACKEND",
            "recorder_live_state_store",
            "/api/meeting-recorder/live-chunk",
            "/api/meeting-recorder/live-session",
        ):
            self.assertNotIn(retired_token, combined)

    def test_user_surfaces_do_not_link_or_configure_live_qa(self) -> None:
        files = [
            "products/reunia/templates/navbar.html",
            "products/reunia/templates/settings.html",
            "products/reunia/templates/admin-analytics.html",
            "products/reunia/static/js/pages/settings.js",
            "products/reunia/static/js/pages/admin-analytics.js",
        ]
        combined = "\n".join((ROOT / relative).read_text(encoding="utf-8") for relative in files)
        for retired_token in (
            "Live Interview Assistance",
            "Live Assistance answers",
            "/live-qa.html",
            "live-qa-settings",
            "data-live-assistance-user",
        ):
            self.assertNotIn(retired_token, combined)

    def test_mock_interview_keeps_short_audio_transcription(self) -> None:
        recorder = (ROOT / "products/reunia/meeting_assistant/services/audio_transcription_service.py").read_text(encoding="utf-8")
        mock = MOCK_INTERVIEW_SOURCE.read_text(encoding="utf-8")
        self.assertIn("def transcribe_upload", recorder)
        self.assertIn("transcribe_upload(", mock)
        self.assertNotIn("transcribe_live_upload", recorder + mock)


if __name__ == "__main__":
    unittest.main(verbosity=2)
