from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .models import (
    CandidateAnswer,
    CandidateProfile,
    JobAnalysis,
    TailoringProposal,
)
from .resume_report import ResumeReport


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
    job_description: str = ""
    target_title: str = ""
    profile_upload_name: str = ""

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
    state: WorkflowState
    touched_at: float


class InMemoryWorkflowStore:
    """Thread-safe, process-local workflow storage keyed by a signed browser session ID.

    The Flask cookie contains only an opaque ID. Resume contents, API results, and generated
    Word bytes remain on the server and are never serialized into the browser cookie.
    """

    def __init__(
        self,
        state_factory: Callable[[], WorkflowState],
        *,
        ttl_seconds: int = 8 * 60 * 60,
    ) -> None:
        self._state_factory = state_factory
        self._ttl_seconds = max(300, ttl_seconds)
        self._items: dict[str, _StoredState] = {}
        self._lock = threading.RLock()

    def new_id(self) -> str:
        return secrets.token_urlsafe(24)

    def get(self, session_id: str) -> WorkflowState:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            stored = self._items.get(session_id)
            if stored is None:
                stored = _StoredState(self._state_factory(), now)
                self._items[session_id] = stored
            else:
                stored.touched_at = now
            return stored.state

    def reset(self, session_id: str) -> WorkflowState:
        with self._lock:
            state = self._state_factory()
            self._items[session_id] = _StoredState(state, time.time())
            return state

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._items.pop(session_id, None)

    def _prune_locked(self, now: float) -> None:
        expired = [
            session_id
            for session_id, stored in self._items.items()
            if now - stored.touched_at > self._ttl_seconds
        ]
        for session_id in expired:
            self._items.pop(session_id, None)
