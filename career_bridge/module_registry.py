"""Inventory of reusable capabilities discovered in the imported products.

The registry is descriptive. It deliberately does not import either legacy app,
which avoids Flask application-context coupling and the current OpenAI SDK major-
version conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    AUTHENTICATION = "authentication"
    USER_PROFILES = "user_profiles"
    DOCUMENT_STORAGE = "document_storage"
    OPENAI_INTEGRATION = "openai_integration"
    AUDIO_RECORDING = "audio_recording"
    TRANSCRIPTION = "transcription"
    SCORING = "scoring"
    ACTION_TRACKING = "action_tracking"
    ADMIN_SUPPORT = "admin_support"
    RESUME_PARSING_GENERATION = "resume_parsing_generation"


class ReuseStrategy(str, Enum):
    ADAPT = "adapt"
    EXTRACT = "extract"
    WRAP = "wrap"
    CONSOLIDATE_LATER = "consolidate_later"


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    capability: Capability
    source_product: str
    primary_paths: tuple[str, ...]
    strategy: ReuseStrategy
    notes: str


MODULES: tuple[ModuleDescriptor, ...] = (
    ModuleDescriptor(
        Capability.AUTHENTICATION,
        "Réunia",
        (
            "products/reunia/meeting_assistant/services/authentication_service.py",
            "products/reunia/meeting_assistant/repositories/user_repository.py",
            "products/reunia/meeting_assistant/blueprints/auth/",
        ),
        ReuseStrategy.WRAP,
        "Use Réunia authentication behind AuthenticationPort; do not copy auth routes into the resume app.",
    ),
    ModuleDescriptor(
        Capability.USER_PROFILES,
        "Réunia + Resume Taylor",
        (
            "products/reunia/meeting_assistant/services/user_service.py",
            "products/reunia/meeting_assistant/repositories/user_repository.py",
            "products/resume_taylor/resume_tailor/models.py",
            "products/resume_taylor/resume_tailor/profile_io.py",
        ),
        ReuseStrategy.CONSOLIDATE_LATER,
        "Keep account preferences and verified candidate evidence distinct, joined by the Career Bridge user ID.",
    ),
    ModuleDescriptor(
        Capability.DOCUMENT_STORAGE,
        "Réunia",
        (
            "products/reunia/meeting_assistant/repositories/knowledge_file_store.py",
            "products/reunia/meeting_assistant/repositories/recorder_job_store.py",
            "products/resume_taylor/resume_tailor/docx_export.py",
            "products/resume_taylor/resume_tailor/pdf_export.py",
        ),
        ReuseStrategy.ADAPT,
        "Adapt Local/S3 object storage to DocumentStoragePort; keep generation separate from storage.",
    ),
    ModuleDescriptor(
        Capability.OPENAI_INTEGRATION,
        "Réunia + Resume Taylor",
        (
            "products/reunia/meeting_assistant/services/ai_cost_control_service.py",
            "products/reunia/meeting_assistant/services/transcript_analysis_service.py",
            "products/reunia/meeting_assistant/services/knowledge_search_service.py",
            "products/resume_taylor/resume_tailor/ai.py",
            "products/resume_taylor/resume_tailor/model_config.py",
        ),
        ReuseStrategy.EXTRACT,
        "Create one provider adapter after resolving the OpenAI SDK 1.x versus 2.x dependency conflict.",
    ),
    ModuleDescriptor(
        Capability.AUDIO_RECORDING,
        "Réunia",
        (
            "products/reunia/meeting_assistant/services/browser_recorder_service.py",
            "products/reunia/meeting_assistant/services/browser_recorder_job_service.py",
            "products/reunia/meeting_assistant/blueprints/recorder/",
            "products/reunia/meeting_assistant/recorder_worker.py",
        ),
        ReuseStrategy.WRAP,
        "Reuse upload validation, queuing, and recording processing behind AudioRecordingPort.",
    ),
    ModuleDescriptor(
        Capability.TRANSCRIPTION,
        "Réunia",
        (
            "products/reunia/meeting_assistant/services/browser_recorder_service.py",
            "products/reunia/meeting_assistant/services/transcript_service.py",
            "products/reunia/meeting_assistant/services/transcript_analysis_service.py",
            "products/reunia/meeting_assistant/repositories/transcript_repository.py",
        ),
        ReuseStrategy.ADAPT,
        "Map Réunia transcript segments and persisted meeting transcripts to the shared Transcript model.",
    ),
    ModuleDescriptor(
        Capability.SCORING,
        "Réunia + Resume Taylor",
        (
            "products/reunia/meeting_assistant/services/scoring_service.py",
            "products/resume_taylor/resume_tailor/application_fit.py",
            "products/resume_taylor/resume_tailor/resume_report.py",
            "products/resume_taylor/resume_tailor/optimization.py",
        ),
        ReuseStrategy.CONSOLIDATE_LATER,
        "Preserve each rubric; normalize outputs to ScoreKind and a 0-100 Score without collapsing algorithms.",
    ),
    ModuleDescriptor(
        Capability.ACTION_TRACKING,
        "Réunia + Resume Taylor",
        (
            "products/reunia/meeting_assistant/services/action_service.py",
            "products/reunia/meeting_assistant/repositories/action_repository.py",
            "products/resume_taylor/resume_tailor/application_tracker.py",
        ),
        ReuseStrategy.ADAPT,
        "Use Réunia actions for tasks and Resume Taylor records for application outcomes; join them through CareerJourney.",
    ),
    ModuleDescriptor(
        Capability.ADMIN_SUPPORT,
        "Réunia",
        (
            "products/reunia/meeting_assistant/services/admin_support_service.py",
            "products/reunia/meeting_assistant/services/support_service.py",
            "products/reunia/meeting_assistant/services/admin_analytics_service.py",
            "products/reunia/meeting_assistant/repositories/support_repository.py",
        ),
        ReuseStrategy.WRAP,
        "Reuse support cases, incident aggregation, and analytics through AdminSupportPort.",
    ),
    ModuleDescriptor(
        Capability.RESUME_PARSING_GENERATION,
        "Resume Taylor",
        (
            "products/resume_taylor/resume_tailor/models.py",
            "products/resume_taylor/resume_tailor/profile_io.py",
            "products/resume_taylor/resume_tailor/ai.py",
            "products/resume_taylor/resume_tailor/docx_export.py",
            "products/resume_taylor/resume_tailor/pdf_export.py",
            "products/resume_taylor/resume_tailor/validation.py",
        ),
        ReuseStrategy.WRAP,
        "Expose verified-profile parsing, tailoring, validation, and export behind ResumeEnginePort.",
    ),
)


def module_inventory() -> tuple[ModuleDescriptor, ...]:
    return MODULES


def module_for(capability: Capability) -> ModuleDescriptor:
    for descriptor in MODULES:
        if descriptor.capability is capability:
            return descriptor
    raise KeyError(capability)
