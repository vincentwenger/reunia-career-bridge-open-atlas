from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from .models import (
    CandidateAnswer,
    CandidateProfile,
    JobAnalysis,
    NewcomerCareerProfile,
    TailoringProposal,
)
from .resume_report import ResumeReport

if TYPE_CHECKING:
    from .storage import LoadedWorkflowState


def normalize_job_description(value: str) -> str:
    """Return a stable representation of job-description text.

    Browsers submit textarea line endings as CRLF even when the server originally rendered LF.
    Treat those transport-only differences, plus trailing spaces, as the same input.
    """
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def normalize_target_title(value: str) -> str:
    """Normalize harmless spacing differences in a target title."""
    return " ".join(value.split())


def initial_report_fingerprint(state: "WorkflowState") -> str:
    """Fingerprint only inputs that define the immutable Initial report baseline.

    Draft generation, candidate confirmations, proposal edits, stage transitions, audits,
    exports, and browser-only newline conversion intentionally do not participate.
    """
    payload = {
        "source_profile": state.source_profile.model_dump(mode="json"),
        "job_description": normalize_job_description(state.job_description),
        "target_title": normalize_target_title(state.target_title),
        "career_background": state.career_background.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class WorkflowStepSnapshot:
    """Immutable display state captured when a guided workflow step is completed."""

    stage: str
    captured_at: str
    job_description: str = ""
    target_title: str = ""
    career_background: NewcomerCareerProfile = field(
        default_factory=NewcomerCareerProfile
    )
    proposal: TailoringProposal | None = None
    profile: CandidateProfile | None = None
    candidate_answers: list[CandidateAnswer] = field(default_factory=list)
    draft_revision: int = 0
    previous_draft_revision: int | None = None
    previous_draft_proposal: TailoringProposal | None = None
    change_label: str = ""
    changed_at: str = ""


@dataclass
class WorkflowState:
    source_profile: CandidateProfile
    original_source_profile: CandidateProfile | None = None
    # How the reusable Baseline Resume was first created. Older stored states
    # intentionally default to an empty value and are inferred at render time.
    baseline_creation_method: str = ""
    # User-entered facts retained when an imported resume is merged for review.
    manual_source_profile: CandidateProfile | None = None
    # Language detected from the originally imported file. This is distinct
    # from source_profile_language, which is the generated Baseline Resume language.
    source_resume_language: str = ""
    source_profile_language: str = ""
    source_profile_translation_fingerprint: str = ""
    career_background: NewcomerCareerProfile = field(
        default_factory=NewcomerCareerProfile
    )
    job_description: str = ""
    target_title: str = ""
    profile_upload_name: str = ""
    source_resume_key: str = ""
    source_resume_fingerprint: str = ""
    source_resume_contact_links_fingerprint: str = ""
    # Fingerprint of the reusable Foundation Baseline Resume version from which
    # this application was copied. It remains stable when the application is
    # intentionally translated into a different target language.
    foundation_baseline_fingerprint: str = ""

    processing_mode: str = "testing"
    custom_analysis_tailoring_model: str = "gpt-5.6-terra"
    custom_evidence_review_model: str = "gpt-5.6-sol"
    custom_analysis_tailoring_reasoning_effort: str | None = "low"
    custom_evidence_review_reasoning_effort: str | None = "medium"

    analysis: JobAnalysis | None = None
    analysis_input_fingerprint: str | None = None
    initial_report: ResumeReport | None = None
    initial_report_input_fingerprint: str | None = None
    initial_report_analysis: JobAnalysis | None = None
    initial_report_proposal: TailoringProposal | None = None
    initial_report_created_at: str = ""
    initial_report_error: str = ""
    initial_evidence_proposal: TailoringProposal | None = None
    initial_evidence_input_fingerprint: str | None = None

    # Resume version lifecycle. The Initial version is derived from source_profile.
    # The provisional proposal feeds confirmation, the draft is the job-aligned resume,
    # and the final proposal is the optimized export version.
    workflow_stage: str = "initial"
    # True after the candidate approves the Step 3 Job-Aligned Resume and
    # starts the quality/finalization pass.  The presentation layer already
    # uses this flag to distinguish the editable review from its completed
    # snapshot, so it must be part of every workflow state (including fresh
    # sessions opened directly from the global navbar).
    quality_review_started: bool = False
    draft_proposal: TailoringProposal | None = None
    previous_draft_proposal: TailoringProposal | None = None
    draft_revision: int = 0
    previous_draft_revision: int | None = None
    draft_last_change_label: str = ""
    draft_last_changed_at: str = ""
    final_proposal: TailoringProposal | None = None
    provisional_proposal: TailoringProposal | None = None
    analyzed_input_fingerprint: str | None = None
    confirmation_complete: bool = False
    candidate_answers: list[CandidateAnswer] = field(default_factory=list)
    confirmed_profile: CandidateProfile | None = None
    save_confirmed_profile: bool = False
    save_confirmation_to_library: bool = False
    saved_library_evidence_count: int = 0
    reused_library_evidence_count: int = 0
    confirmation_draft: dict[str, str] = field(default_factory=dict)
    confirmation_follow_up_round: int = 0
    confirmation_follow_up_count: int = 0
    workflow_step_snapshots: dict[str, WorkflowStepSnapshot] = field(default_factory=dict)

    updated_report: ResumeReport | None = None
    updated_report_input_fingerprint: str | None = None
    updated_report_proposal_fingerprint: str | None = None
    updated_report_created_at: str = ""
    updated_report_error: str = ""

    # Step 4 optimization keeps the report before and after automatic improvements
    # so the UI can show whether the consolidated stage added measurable value.
    optimization_report_before: ResumeReport | None = None
    optimization_report_after: ResumeReport | None = None
    optimization_started_at: str = ""
    optimization_applied_issue_count: int = 0
    optimization_accepted_batch_count: int = 0
    optimization_rejected_batch_count: int = 0
    optimization_rejected_issue_count: int = 0
    optimization_unchanged_batch_count: int = 0
    optimization_baseline_rolled_back: bool = False
    # User-facing outcome for the optional Step 4 AI quality pass. Keep the
    # provider exception out of persisted workflow state and expose only a
    # stable status that the template can explain without technical details.
    optimization_status: str = "not_started"
    optimization_notice: str = ""

    final_report: ResumeReport | None = None
    final_report_input_fingerprint: str | None = None
    final_report_proposal_fingerprint: str | None = None
    final_report_proposal: TailoringProposal | None = None
    final_report_profile: CandidateProfile | None = None
    final_report_candidate_answers: list[CandidateAnswer] = field(default_factory=list)
    final_report_created_at: str = ""
    final_report_filename: str = ""
    final_report_error: str = ""
    final_report_exact: bool = False
    final_resume_title: str = ""
    final_resume_bytes: bytes | None = None
    final_resume_pdf_bytes: bytes | None = None
    final_resume_docx_key: str = ""
    final_resume_pdf_key: str = ""
    final_resume_docx_fingerprint: str = ""
    final_resume_pdf_fingerprint: str = ""
    final_resume_pdf_error: str = ""
    # Legacy template key retained for saved sessions and application records.
    resume_style: str = "professional"
    resume_style_explicit: bool = False
    resume_career_stage: str = "mid_career"
    resume_career_stage_explicit: bool = False
    resume_format: str = "standard"
    resume_format_explicit: bool = False
    resume_visual_design: str = "corporate"
    resume_visual_design_explicit: bool = False

    def clear_tailoring_results(self) -> None:
        self.workflow_stage = "initial"
        self.quality_review_started = False
        self.draft_proposal = None
        self.previous_draft_proposal = None
        self.draft_revision = 0
        self.previous_draft_revision = None
        self.draft_last_change_label = ""
        self.draft_last_changed_at = ""
        self.final_proposal = None
        self.final_resume_title = ""
        self.provisional_proposal = None
        self.analyzed_input_fingerprint = None
        self.confirmation_complete = False
        self.candidate_answers = []
        self.confirmed_profile = None
        self.save_confirmed_profile = False
        self.save_confirmation_to_library = False
        self.saved_library_evidence_count = 0
        self.reused_library_evidence_count = 0
        self.confirmation_draft = {}
        self.confirmation_follow_up_round = 0
        self.confirmation_follow_up_count = 0
        self.workflow_step_snapshots = {}
        self.clear_draft_report()
        self.clear_final_report()

    def clear_draft_report(self) -> None:
        self.updated_report = None
        self.updated_report_input_fingerprint = None
        self.updated_report_proposal_fingerprint = None
        self.updated_report_created_at = ""
        self.updated_report_error = ""

    def clear_final_report(self) -> None:
        self.optimization_report_before = None
        self.optimization_report_after = None
        self.optimization_started_at = ""
        self.optimization_applied_issue_count = 0
        self.optimization_accepted_batch_count = 0
        self.optimization_rejected_batch_count = 0
        self.optimization_rejected_issue_count = 0
        self.optimization_unchanged_batch_count = 0
        self.optimization_baseline_rolled_back = False
        self.optimization_status = "not_started"
        self.optimization_notice = ""
        self.final_report = None
        self.final_report_input_fingerprint = None
        self.final_report_proposal_fingerprint = None
        self.final_report_proposal = None
        self.final_report_profile = None
        self.final_report_candidate_answers = []
        self.final_report_created_at = ""
        self.final_report_filename = ""
        self.final_report_error = ""
        self.final_report_exact = False
        self.final_resume_bytes = None
        self.final_resume_pdf_bytes = None
        self.final_resume_docx_key = ""
        self.final_resume_pdf_key = ""
        self.final_resume_docx_fingerprint = ""
        self.final_resume_pdf_fingerprint = ""
        self.final_resume_pdf_error = ""

    def clear_results(self) -> None:
        self.clear_tailoring_results()
        self.analysis = None
        self.analysis_input_fingerprint = None
        self.initial_report = None
        self.initial_report_input_fingerprint = None
        self.initial_report_analysis = None
        self.initial_report_proposal = None
        self.initial_report_created_at = ""
        self.initial_report_error = ""
        self.initial_evidence_proposal = None
        self.initial_evidence_input_fingerprint = None


@dataclass
class _StoredState:
    serialized: bytes
    version: int
    fingerprint: str
    touched_at: float
    updated_at: str
    updated_by_request: str


class InMemoryWorkflowStore:
    """Thread-safe workflow storage that still enforces remote-store semantics.

    Each load returns a newly deserialized state object. Routes therefore cannot
    persist changes merely by mutating a shared Python reference; they must call
    ``save`` with the version observed at load time, exactly as they do with the
    DynamoDB adapter.
    """

    def __init__(
        self,
        state_factory: Callable[[], WorkflowState],
        *,
        scratch_ttl_seconds: int = 8 * 60 * 60,
        application_ttl_seconds: int = 0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._state_factory = state_factory
        self._scratch_ttl_seconds = max(300, int(scratch_ttl_seconds))
        self._application_ttl_seconds = (
            max(300, int(application_ttl_seconds))
            if int(application_ttl_seconds) > 0
            else 0
        )
        self._clock = clock or time.time
        self._items: dict[str, _StoredState] = {}
        self._lock = threading.RLock()

    def new_id(self) -> str:
        return secrets.token_urlsafe(24)

    def load(self, workflow_key: str) -> "LoadedWorkflowState":
        from .storage import LoadedWorkflowState
        from .workflow_serialization import (
            workflow_state_from_json_bytes,
            workflow_state_json_bytes,
        )

        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            stored = self._items.get(workflow_key)
            if stored is None:
                state = self._state_factory()
                serialized = workflow_state_json_bytes(state)
                stored = _StoredState(
                    serialized=serialized,
                    version=0,
                    fingerprint=hashlib.sha256(serialized).hexdigest(),
                    touched_at=now,
                    updated_at="",
                    updated_by_request="",
                )
                self._items[workflow_key] = stored
            else:
                stored.touched_at = now
            return LoadedWorkflowState(
                state=workflow_state_from_json_bytes(stored.serialized),
                version=stored.version,
                fingerprint=stored.fingerprint,
                updated_at=stored.updated_at,
                updated_by_request=stored.updated_by_request,
            )

    def get(self, workflow_key: str) -> WorkflowState:
        return self.load(workflow_key).state

    def save(
        self,
        workflow_key: str,
        state: WorkflowState,
        *,
        expected_version: int,
        updated_by_request: str,
    ) -> "LoadedWorkflowState":
        """Persist a detached snapshot using optimistic version checking."""

        from .storage import (
            LoadedWorkflowState,
            WorkflowConflictError,
            normalize_workflow_request_id,
        )
        from .workflow_serialization import (
            workflow_state_from_json_bytes,
            workflow_state_json_bytes,
        )

        serialized = workflow_state_json_bytes(state)
        fingerprint = hashlib.sha256(serialized).hexdigest()
        request_id = normalize_workflow_request_id(updated_by_request)
        now = self._clock()
        updated_at = datetime.fromtimestamp(now, timezone.utc).isoformat(
            timespec="seconds"
        )
        with self._lock:
            self._prune_locked(now)
            current = self._items.get(workflow_key)
            actual_version = current.version if current is not None else 0
            if int(expected_version) != actual_version:
                raise WorkflowConflictError(
                    workflow_key,
                    expected_version=int(expected_version),
                    actual_version=actual_version,
                    actual_updated_by_request=(
                        current.updated_by_request if current is not None else ""
                    ),
                )
            if current is not None and current.fingerprint == fingerprint:
                current.touched_at = now
                return LoadedWorkflowState(
                    state=workflow_state_from_json_bytes(current.serialized),
                    version=current.version,
                    fingerprint=current.fingerprint,
                    updated_at=current.updated_at,
                    updated_by_request=current.updated_by_request,
                )
            version = actual_version + 1
            stored = _StoredState(
                serialized=serialized,
                version=version,
                fingerprint=fingerprint,
                touched_at=now,
                updated_at=updated_at,
                updated_by_request=request_id,
            )
            self._items[workflow_key] = stored
            return LoadedWorkflowState(
                state=workflow_state_from_json_bytes(serialized),
                version=version,
                fingerprint=fingerprint,
                updated_at=updated_at,
                updated_by_request=request_id,
            )

    def reset(self, workflow_key: str) -> WorkflowState:
        loaded = self.load(workflow_key)
        state = self._state_factory()
        return self.save(
            workflow_key,
            state,
            expected_version=loaded.version,
            updated_by_request="SYSTEM-RESET",
        ).state

    def peek(self, workflow_key: str) -> WorkflowState | None:
        from .workflow_serialization import workflow_state_from_json_bytes

        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            stored = self._items.get(workflow_key)
            if stored is None:
                return None
            stored.touched_at = now
            return workflow_state_from_json_bytes(stored.serialized)

    def delete(self, workflow_key: str) -> None:
        with self._lock:
            self._items.pop(workflow_key, None)

    def _prune_locked(self, now: float) -> None:
        from .storage import workflow_retention_class

        expired: list[str] = []
        for workflow_key, stored in self._items.items():
            ttl_seconds = (
                self._scratch_ttl_seconds
                if workflow_retention_class(workflow_key) == "scratch"
                else self._application_ttl_seconds
            )
            if ttl_seconds > 0 and now - stored.touched_at > ttl_seconds:
                expired.append(workflow_key)
        for workflow_key in expired:
            self._items.pop(workflow_key, None)
