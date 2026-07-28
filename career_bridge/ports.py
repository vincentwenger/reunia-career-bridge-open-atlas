"""Technology-neutral interfaces for adapting the two imported products."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from career_bridge.domain.enums import DocumentKind, ScoreKind
from career_bridge.domain.models import (
    AuthSession,
    CandidateProfile,
    CareerBackground,
    CareerDocument,
    EvidenceItem,
    EvidenceLibrary,
    ImprovementAction,
    InterviewPreparation,
    InterviewQuestion,
    JobApplication,
    JobApplicationBundle,
    MockInterviewSession,
    Resume,
    Score,
    SupportCase,
    TailoredResumeVersion,
    TargetJobDescription,
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
    application: JobApplication
    candidate_profile: CandidateProfile
    career_background: CareerBackground
    source_resume: Resume
    source_document: CareerDocument
    target_job: TargetJobDescription
    evidence_library: EvidenceLibrary
    evidence_items: tuple[EvidenceItem, ...] = ()
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
class CandidateProfilePort(Protocol):
    def get(self, candidate_profile_id: str) -> CandidateProfile | None: ...
    def save(self, profile: CandidateProfile) -> CandidateProfile: ...


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
        session: MockInterviewSession,
        *,
        source: str,
        content: bytes,
        content_type: str,
    ) -> CareerDocument: ...


@runtime_checkable
class TranscriptionPort(Protocol):
    def transcribe(
        self,
        session: MockInterviewSession,
        recordings: Sequence[CareerDocument],
        *,
        language: str,
    ) -> Transcript: ...


@runtime_checkable
class ScoringPort(Protocol):
    def score(
        self,
        application: JobApplication,
        kind: ScoreKind,
        inputs: Mapping[str, Any],
    ) -> Score: ...


@runtime_checkable
class ActionTrackingPort(Protocol):
    def list_for_application(self, application_id: str) -> list[ImprovementAction]: ...
    def save(self, action: ImprovementAction) -> ImprovementAction: ...


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
        candidate_profile_id: str,
    ) -> CareerBackground: ...

    def generate_resume(
        self,
        request: ResumeGenerationRequest,
    ) -> TailoredResumeVersion: ...


@runtime_checkable
class CareerBridgeRepository(Protocol):
    """Persistence boundary for the shared application aggregate.

    Existing Réunia and Resume Taylor records remain in their current stores.
    Implementations may persist only shared IDs and relationships initially.
    """

    def get_application(self, application_id: str) -> JobApplication | None: ...
    def save_application(self, application: JobApplication) -> JobApplication: ...
    def get_application_bundle(self, application_id: str) -> JobApplicationBundle | None: ...

    def get_candidate_profile(self, candidate_profile_id: str) -> CandidateProfile | None: ...
    def save_candidate_profile(self, profile: CandidateProfile) -> CandidateProfile: ...
    def save_career_background(self, background: CareerBackground) -> CareerBackground: ...
    def save_resume(self, resume: Resume) -> Resume: ...
    def save_target_job(self, target_job: TargetJobDescription) -> TargetJobDescription: ...
    def save_evidence_library(self, library: EvidenceLibrary) -> EvidenceLibrary: ...
    def save_evidence_item(self, item: EvidenceItem) -> EvidenceItem: ...
    def save_tailored_resume_version(
        self,
        version: TailoredResumeVersion,
    ) -> TailoredResumeVersion: ...
    def save_interview_preparation(
        self,
        preparation: InterviewPreparation,
    ) -> InterviewPreparation: ...
    def save_interview_question(self, question: InterviewQuestion) -> InterviewQuestion: ...
    def save_mock_interview_session(
        self,
        session: MockInterviewSession,
    ) -> MockInterviewSession: ...
    def save_improvement_action(self, action: ImprovementAction) -> ImprovementAction: ...

    def save_document(self, document: CareerDocument) -> CareerDocument: ...
    def list_documents(
        self,
        application_id: str,
        kind: DocumentKind | None = None,
    ) -> list[CareerDocument]: ...
