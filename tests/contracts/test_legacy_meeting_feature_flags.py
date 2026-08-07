"""Contracts ensuring predecessor meeting runtime code stays removed."""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.source_aggregates import MOCK_INTERVIEW_SOURCE

ROOT = Path(__file__).resolve().parents[2]
REUNIA = ROOT / "products/reunia"
ASSISTANT = REUNIA / "meeting_assistant"


class RetiredMeetingRuntimeContracts(unittest.TestCase):
    def test_retired_runtime_files_are_absent(self) -> None:
        retired = (
            "meeting_assistant/blueprints/meeting_shares",
            "meeting_assistant/blueprints/recorder/legacy_routes.py",
            "meeting_assistant/blueprints/transcripts/legacy_routes.py",
            "meeting_assistant/blueprints/knowledge/legacy_routes.py",
            "meeting_assistant/services/browser_recorder_service.py",
            "meeting_assistant/services/browser_recorder_job_service.py",
            "meeting_assistant/services/recorder_job_queue.py",
            "meeting_assistant/services/meeting_share_service.py",
            "meeting_assistant/repositories/recorder_job_store.py",
            "meeting_assistant/repositories/meeting_share_repository.py",
            "meeting_assistant/recorder_worker.py",
            "meeting_assistant/run_production.py",
            "templates/shared-meeting.html",
            "templates/meeting/_share_dialog.html",
            "static/js/pages/shared-meeting.js",
            "static/css/pages/shared-meeting.css",
        )
        for relative in retired:
            self.assertFalse((REUNIA / relative).exists(), relative)

    def test_configuration_and_boot_have_no_legacy_switches_or_resources(self) -> None:
        combined = "\n".join(
            (ASSISTANT / relative).read_text(encoding="utf-8")
            for relative in ("config.py", "__init__.py", "extensions.py")
        )
        for token in (
            "CAREER_BRIDGE_ENABLE_LEGACY_",
            "RECORDER_JOBS_BUCKET",
            "MEETING_SHARES_TABLE_NAME",
            "legacy_recorder_bp",
            "legacy_transcripts_bp",
            "legacy_meeting_knowledge_bp",
            "meeting_shares_bp",
            "recorder_job_queue",
            "recorder_job_store",
        ):
            self.assertNotIn(token, combined)

    def test_canonical_career_routes_remain(self) -> None:
        factory = (ASSISTANT / "__init__.py").read_text(encoding="utf-8")
        recorder = (ASSISTANT / "blueprints/recorder/routes.py").read_text(encoding="utf-8")
        transcripts = (ASSISTANT / "blueprints/transcripts/routes.py").read_text(encoding="utf-8")
        knowledge = (ASSISTANT / "blueprints/knowledge/routes.py").read_text(encoding="utf-8")
        self.assertIn("from meeting_assistant.blueprints.recorder import recorder_bp", factory)
        self.assertIn('@recorder_bp.get("/mock-interview")', recorder)
        self.assertIn('@transcript_bp.get("/interview-review")', transcripts)
        self.assertIn('@knowledge_bp.get("/career-evidence-library")', knowledge)
        self.assertIn('@knowledge_bp.get("/api/career/evidence/collections")', knowledge)

    def test_mock_interview_uses_focused_short_audio_service(self) -> None:
        service = (ASSISTANT / "services/audio_transcription_service.py").read_text(encoding="utf-8")
        mock = MOCK_INTERVIEW_SOURCE.read_text(encoding="utf-8")
        self.assertIn("class ShortAudioTranscriptionService", service)
        self.assertIn("def transcribe_upload", service)
        self.assertIn("ShortAudioTranscriptionService", mock)
        self.assertIn("transcribe_upload(", mock)
        for token in ("queue_meeting", "finalize_upload_session", "RedisRecorderJobQueue"):
            self.assertNotIn(token, service + mock)

    def test_application_materials_have_no_legacy_migration_branch(self) -> None:
        source = (ASSISTANT / "services/application_materials_service.py").read_text(encoding="utf-8")
        self.assertNotIn("_legacy_materials_migration_enabled", source)
        self.assertNotIn("_migrate_legacy_materials", source)
        self.assertNotIn("_migrate_legacy_active_application", source)
        self.assertIn("return active_id or \"\"", source)

    def test_interview_review_has_no_dead_public_share_surface(self) -> None:
        files = (
            REUNIA / "templates/meeting-review.html",
            REUNIA / "templates/settings.html",
            REUNIA / "static/js/pages/meeting-review.js",
            REUNIA / "static/js/pages/settings.js",
            ASSISTANT / "services/user_service.py",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
        for token in (
            "meeting-share",
            "Sharing Defaults",
            "shareDefaultExpirationDays",
            "shareRequirePassword",
            "shareAllowDownload",
            "shareIncludeScorecard",
        ):
            self.assertNotIn(token, combined)

    def test_health_has_only_current_service_status(self) -> None:
        source = (ASSISTANT / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("legacy_features", source)
        self.assertIn('"application_builder": application_builder_storage_status()', source)
        self.assertIn('"async_worker": async_worker_health_status()', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
