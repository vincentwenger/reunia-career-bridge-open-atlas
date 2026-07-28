from __future__ import annotations

from dataclasses import dataclass

from career_bridge.ports import (
    ActionTrackingPort,
    AdminSupportPort,
    AudioRecordingPort,
    AuthenticationPort,
    DocumentStoragePort,
    OpenAIIntegrationPort,
    ResumeEnginePort,
    ScoringPort,
    TranscriptionPort,
    UserProfilePort,
)


@dataclass(slots=True)
class CareerBridgeModules:
    """Composition root for adapters, populated incrementally during migration."""

    authentication: AuthenticationPort | None = None
    user_profiles: UserProfilePort | None = None
    document_storage: DocumentStoragePort | None = None
    openai: OpenAIIntegrationPort | None = None
    audio_recording: AudioRecordingPort | None = None
    transcription: TranscriptionPort | None = None
    scoring: ScoringPort | None = None
    action_tracking: ActionTrackingPort | None = None
    admin_support: AdminSupportPort | None = None
    resume_engine: ResumeEnginePort | None = None

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self.__dataclass_fields__
            if getattr(self, name) is None
        )

    def ready_for_resume_tailoring(self) -> bool:
        return all(
            (
                self.user_profiles,
                self.document_storage,
                self.openai,
                self.scoring,
                self.resume_engine,
            )
        )

    def ready_for_interview_practice(self) -> bool:
        return all(
            (
                self.document_storage,
                self.openai,
                self.audio_recording,
                self.transcription,
                self.scoring,
                self.action_tracking,
            )
        )
