"""Technology-neutral interfaces for adapting the two existing products."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from career_bridge.domain.enums import DocumentKind, ScoreKind
from career_bridge.domain.models import (
    AuthSession,
    CareerAction,
    CareerDocument,
    CareerJourney,
    InterviewSession,
    ResumeArtifact,
    Score,
    SupportCase,
    Transcript,
    UserProfile,
)


@dataclass(frozen=True, slots=True)
class AIRequest:
    operation: str
    system_prompt: str
    user_prompt: str
    schema_name: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AIResponse:
    text: str = ""
    structured: Mapping[str, Any] = field(default_factory=dict)
    model: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResumeGenerationRequest:
    journey: CareerJourney
    profile: UserProfile
    source_resume: CareerDocument
    job_description: CareerDocument
    evidence_ids: tuple[str, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class AuthenticationPort(Protocol):
    def authenticate(self, email: str, password: str) -> AuthSession: ...
    def end_session(self, session_id: str) -> None: ...


@runtime_checkable
class UserProfilePort(Protocol):
    def get(self, user_id: str) -> UserProfile | None: ...
    def save(self, profile: UserProfile) -> UserProfile: ...


@runtime_checkable
class DocumentStoragePort(Protocol):
    def put(self, document: CareerDocument, content: bytes) -> CareerDocument: ...
    def get(self, storage_key: str) -> bytes: ...
    def delete(self, storage_key: str) -> None: ...


@runtime_checkable
class OpenAIIntegrationPort(Protocol):
    def complete(self, request: AIRequest) -> AIResponse: ...


@runtime_checkable
class AudioRecordingPort(Protocol):
    def accept_recording(
        self,
        session: InterviewSession,
        *,
        source: str,
        content: bytes,
        content_type: str,
    ) -> CareerDocument: ...


@runtime_checkable
class TranscriptionPort(Protocol):
    def transcribe(
        self,
        session: InterviewSession,
        recordings: Sequence[CareerDocument],
        *,
        language: str,
    ) -> Transcript: ...


@runtime_checkable
class ScoringPort(Protocol):
    def score(
        self,
        journey: CareerJourney,
        kind: ScoreKind,
        inputs: Mapping[str, Any],
    ) -> Score: ...


@runtime_checkable
class ActionTrackingPort(Protocol):
    def list_for_journey(self, journey_id: str) -> list[CareerAction]: ...
    def save(self, action: CareerAction) -> CareerAction: ...


@runtime_checkable
class AdminSupportPort(Protocol):
    def create_case(self, case: SupportCase) -> SupportCase: ...
    def update_case(self, case: SupportCase) -> SupportCase: ...


@runtime_checkable
class ResumeEnginePort(Protocol):
    def parse_resume(
        self,
        document: CareerDocument,
        content: bytes,
        *,
        user_id: str,
    ) -> Mapping[str, Any]: ...

    def generate_resume(self, request: ResumeGenerationRequest) -> ResumeArtifact: ...


@runtime_checkable
class CareerBridgeRepository(Protocol):
    def get_journey(self, journey_id: str) -> CareerJourney | None: ...
    def save_journey(self, journey: CareerJourney) -> CareerJourney: ...
    def save_document(self, document: CareerDocument) -> CareerDocument: ...
    def list_documents(self, journey_id: str, kind: DocumentKind | None = None) -> list[CareerDocument]: ...
