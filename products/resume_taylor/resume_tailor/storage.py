"""Storage contracts and configuration-driven adapter selection.

The Application Builder depends on these protocols rather than concrete storage
classes. The current package provides serialized memory and DynamoDB workflow
stores plus SQLite and DynamoDB application repositories. Additional adapters
can be added without changing Flask routes.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .application_tracker import (
    ApplicationRecord,
    InterviewPreparationRecord,
    ResumeFindingsRecord,
)
from .web_state import WorkflowState

WORKFLOW_STORAGE_BACKENDS = frozenset({"memory", "dynamodb"})
APPLICATION_STORAGE_BACKENDS = frozenset({"sqlite", "dynamodb"})
SCRATCH_WORKFLOW_TTL_CONFIG_KEY = "CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS"
APPLICATION_WORKFLOW_TTL_CONFIG_KEY = "CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS"
LEGACY_WORKFLOW_TTL_CONFIG_KEY = "CAREER_BRIDGE_WORKFLOW_TTL_SECONDS"
DEFAULT_SCRATCH_WORKFLOW_TTL_SECONDS = 8 * 60 * 60


def workflow_retention_class(workflow_key: str) -> str:
    """Classify a workflow key without storing its owner or application ID.

    The Application Builder uses ``<owner>:application:scratch`` for its
    temporary workspace and ``<owner>:application:<application-id>`` for a
    durable application-linked workflow.
    """

    marker = ":application:"
    if marker not in workflow_key:
        return "scratch"
    application_id = workflow_key.rsplit(marker, 1)[1].strip()
    return "application" if application_id and application_id != "scratch" else "scratch"


def configured_scratch_workflow_ttl_seconds(config: Mapping[str, Any]) -> int:
    """Return the sliding TTL for temporary scratch workflows.

    ``CAREER_BRIDGE_WORKFLOW_TTL_SECONDS`` remains a backward-compatible alias,
    but now applies only to scratch workflows.
    """

    raw = config.get(SCRATCH_WORKFLOW_TTL_CONFIG_KEY)
    if raw in (None, ""):
        raw = config.get(LEGACY_WORKFLOW_TTL_CONFIG_KEY)
    return _configured_ttl_seconds(
        raw,
        default=DEFAULT_SCRATCH_WORKFLOW_TTL_SECONDS,
        allow_disabled=False,
    )


def configured_application_workflow_ttl_seconds(config: Mapping[str, Any]) -> int:
    """Return an optional application-workflow TTL; zero means retain."""

    return _configured_ttl_seconds(
        config.get(APPLICATION_WORKFLOW_TTL_CONFIG_KEY),
        default=0,
        allow_disabled=True,
    )


def workflow_ttl_seconds(config: Mapping[str, Any], workflow_key: str) -> int | None:
    """Return the TTL for a workflow, or ``None`` when it must be retained."""

    if workflow_retention_class(workflow_key) == "scratch":
        return configured_scratch_workflow_ttl_seconds(config)
    value = configured_application_workflow_ttl_seconds(config)
    return value or None


def _configured_ttl_seconds(
    raw: Any,
    *,
    default: int,
    allow_disabled: bool,
) -> int:
    try:
        value = int(raw if raw not in (None, "") else default)
    except (TypeError, ValueError):
        value = default
    if allow_disabled and value <= 0:
        return 0
    return max(300, value)


class StorageBackendConfigurationError(RuntimeError):
    """Raised when a configured storage backend is invalid or unavailable."""


def normalize_workflow_request_id(value: Any) -> str:
    """Return a bounded audit identifier for a workflow mutation.

    The Réunia shell creates request IDs before blueprint request hooks run. The
    fallback keeps direct store usage and maintenance operations auditable too.
    """

    request_id = str(value or "").strip()
    if request_id and re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", request_id):
        return request_id
    return f"WF-{secrets.token_hex(8).upper()}"


class WorkflowConflictError(RuntimeError):
    """Raised when a workflow save is based on an out-of-date version."""

    def __init__(
        self,
        workflow_key: str,
        *,
        expected_version: int,
        actual_version: int | None = None,
        actual_updated_by_request: str = "",
    ) -> None:
        self.workflow_key = workflow_key
        self.expected_version = expected_version
        self.actual_version = actual_version
        self.actual_updated_by_request = str(actual_updated_by_request or "")
        detail = (
            f"; the current version is {actual_version}"
            if actual_version is not None
            else ""
        )
        request_detail = (
            f"; last updated by request {self.actual_updated_by_request}"
            if self.actual_updated_by_request
            else ""
        )
        super().__init__(
            "The workflow was updated by another request. "
            f"Expected version {expected_version}{detail}{request_detail}."
        )


@dataclass(frozen=True)
class LoadedWorkflowState:
    """A detached workflow state plus its optimistic-lock metadata."""

    state: WorkflowState
    version: int
    fingerprint: str
    updated_at: str = ""
    updated_by_request: str = ""


@runtime_checkable
class WorkflowStore(Protocol):
    """Persistence boundary for mutable resume-workflow state."""

    def new_id(self) -> str: ...

    def load(self, workflow_key: str) -> LoadedWorkflowState: ...

    def get(self, workflow_key: str) -> WorkflowState: ...

    def save(
        self,
        workflow_key: str,
        state: WorkflowState,
        *,
        expected_version: int,
        updated_by_request: str,
    ) -> LoadedWorkflowState: ...

    def reset(self, workflow_key: str) -> WorkflowState: ...

    def peek(self, workflow_key: str) -> WorkflowState | None: ...

    def delete(self, workflow_key: str) -> None: ...


@runtime_checkable
class ApplicationStore(Protocol):
    """Persistence boundary for job applications and linked career artifacts."""

    def list_for_owner(self, owner_id: str) -> list[ApplicationRecord]: ...

    def get(
        self,
        owner_id: str,
        application_id: str,
        *,
        include_resume_bytes: bool = True,
    ) -> ApplicationRecord | None: ...

    def get_resume_findings(
        self, owner_id: str, application_id: str
    ) -> ResumeFindingsRecord | None: ...

    def save_resume_findings(
        self,
        owner_id: str,
        application_id: str,
        *,
        snapshot_json: str,
        fingerprint: str,
    ) -> ResumeFindingsRecord: ...

    def get_interview_preparation(
        self, owner_id: str, application_id: str
    ) -> InterviewPreparationRecord | None: ...

    def save_interview_preparation(
        self,
        owner_id: str,
        application_id: str,
        *,
        content_json: str,
        job_description_fingerprint: str,
        evidence_fingerprint: str,
        evidence_source_label: str,
        evidence_snapshot_json: str,
        resume_findings_fingerprint: str,
        resume_findings_snapshot_json: str,
        model_name: str,
    ) -> InterviewPreparationRecord: ...

    def find_snapshot(
        self,
        owner_id: str,
        *,
        resume_fingerprint: str,
        company: str,
        role: str,
    ) -> ApplicationRecord | None: ...

    def create(
        self,
        owner_id: str,
        *,
        company: str,
        role: str,
        job_url: str = "",
        application_date: str = "",
        status: str = "draft",
        resume_version: str = "Not started",
        resume_style: str = "",
        alignment_score: float | None = None,
        overall_score: float | None = None,
        interview_readiness: float | None = None,
        screening_received: bool | None = None,
        interview_received: bool | None = None,
        offer_received: bool | None = None,
        notes: str = "",
        next_action: str = "",
        next_follow_up_date: str = "",
        upcoming_event_date: str = "",
        upcoming_event_type: str = "",
        job_description: str = "",
        workflow_step: str = "setup",
        resume_filename: str = "",
        resume_bytes: bytes | None = None,
        resume_fingerprint: str = "",
        resume_pdf_filename: str = "",
        resume_pdf_bytes: bytes | None = None,
    ) -> ApplicationRecord: ...

    def update(
        self,
        owner_id: str,
        application_id: str,
        *,
        company: str,
        role: str,
        job_url: str,
        application_date: str,
        status: str,
        screening_received: bool,
        interview_received: bool,
        offer_received: bool,
        notes: str,
        next_follow_up_date: str,
        interview_readiness: float | None = None,
        next_action: str = "",
        upcoming_event_date: str = "",
        upcoming_event_type: str = "",
        job_description: str | None = None,
    ) -> ApplicationRecord | None: ...

    def update_builder_progress(
        self,
        owner_id: str,
        application_id: str,
        *,
        workflow_step: str,
        resume_version: str | None = None,
        company: str | None = None,
        role: str | None = None,
        job_description: str | None = None,
        status: str | None = None,
        original_resume_key: str | None = None,
    ) -> ApplicationRecord | None: ...

    def attach_resume_snapshot(
        self,
        owner_id: str,
        application_id: str,
        *,
        resume_version: str,
        resume_style: str,
        alignment_score: float | None,
        overall_score: float | None,
        resume_filename: str,
        resume_bytes: bytes,
        resume_fingerprint: str,
        resume_pdf_filename: str = "",
        resume_pdf_bytes: bytes | None = None,
    ) -> ApplicationRecord | None: ...

    def save_impact_snapshot(
        self,
        owner_id: str,
        application_id: str,
        snapshot: dict[str, object],
    ) -> dict[str, object]: ...

    def get_impact_snapshot(
        self, owner_id: str, application_id: str
    ) -> dict[str, object] | None: ...

    def list_impact_snapshots(
        self, owner_id: str
    ) -> list[dict[str, object]]: ...

    def delete(self, owner_id: str, application_id: str) -> bool: ...


def configured_workflow_backend(config: Mapping[str, Any]) -> str:
    """Return and validate the configured workflow backend name."""

    return _configured_backend(
        config,
        key="CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND",
        default="memory",
        allowed=WORKFLOW_STORAGE_BACKENDS,
    )


def configured_application_backend(config: Mapping[str, Any]) -> str:
    """Return and validate the configured application backend name."""

    return _configured_backend(
        config,
        key="CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND",
        default="sqlite",
        allowed=APPLICATION_STORAGE_BACKENDS,
    )


def create_workflow_store(
    config: Mapping[str, Any],
    state_factory: Callable[[], WorkflowState],
    *,
    document_store: Any | None = None,
) -> WorkflowStore:
    """Create the workflow adapter selected by application configuration."""

    backend = configured_workflow_backend(config)
    if backend == "memory":
        from .web_state import InMemoryWorkflowStore

        return InMemoryWorkflowStore(
            state_factory,
            scratch_ttl_seconds=configured_scratch_workflow_ttl_seconds(config),
            application_ttl_seconds=configured_application_workflow_ttl_seconds(config),
        )

    if document_store is None:
        from .object_storage import create_document_store

        document_store = create_document_store(config, require_s3=True)
    factory = _dynamodb_factory("create_dynamodb_workflow_store", backend)
    store = factory(
        config=config,
        state_factory=state_factory,
        document_store=document_store,
    )
    if not isinstance(store, WorkflowStore):
        raise StorageBackendConfigurationError(
            "The DynamoDB workflow adapter does not implement WorkflowStore."
        )
    return store


def create_application_store(
    config: Mapping[str, Any],
    *,
    document_store: Any | None = None,
) -> ApplicationStore:
    """Create the application adapter selected by application configuration."""

    backend = configured_application_backend(config)
    if backend == "sqlite":
        from .application_tracker import SQLiteApplicationStore

        database_path = str(config.get("APPLICATIONS_DB_PATH") or "").strip()
        if not database_path:
            raise StorageBackendConfigurationError(
                "APPLICATIONS_DB_PATH is required when "
                "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=sqlite."
            )
        return SQLiteApplicationStore(database_path)

    if document_store is None:
        from .object_storage import create_document_store

        document_store = create_document_store(config, require_s3=True)
    factory = _dynamodb_factory("create_dynamodb_application_store", backend)
    store = factory(config=config, document_store=document_store)
    if not isinstance(store, ApplicationStore):
        raise StorageBackendConfigurationError(
            "The DynamoDB application adapter does not implement ApplicationStore."
        )
    return store


def _configured_backend(
    config: Mapping[str, Any],
    *,
    key: str,
    default: str,
    allowed: frozenset[str],
) -> str:
    value = str(config.get(key, default) or default).strip().casefold()
    if value not in allowed:
        choices = "|".join(sorted(allowed))
        raise StorageBackendConfigurationError(
            f"{key} must be one of {choices}; received {value!r}."
        )
    return value


def _dynamodb_factory(name: str, backend: str):
    """Load the optional DynamoDB adapter without coupling routes to it.

    Adapter discovery never silently emulates DynamoDB with local storage.
    Selecting a backend whose factory is absent fails during application startup
    with a clear error.
    """

    module_name = f"{__package__}.dynamodb_storage"
    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise StorageBackendConfigurationError(
            f"The {backend} backend was selected, but {module_name} is not "
            "available. Add the production DynamoDB adapter before enabling it."
        ) from exc

    factory = getattr(module, name, None)
    if not callable(factory):
        raise StorageBackendConfigurationError(
            f"The {backend} backend was selected, but {module_name}.{name} "
            "is not available."
        )
    return factory
