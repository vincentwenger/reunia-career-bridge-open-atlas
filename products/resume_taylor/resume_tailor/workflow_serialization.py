"""Explicit, versioned serialization for Career Bridge workflow state.

Workflow state contains a mixture of Pydantic models, standard dataclasses,
optional nested values, lists, dictionaries, and resume report dataclasses.
Pydantic's ``TypeAdapter`` provides one schema-aware round trip for that complete
graph while this module adds a stable envelope, canonical fingerprinting, and a
strict prohibition on embedding binary document bytes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from typing import Any, Mapping

from pydantic import BaseModel, TypeAdapter, ValidationError

from .web_state import WorkflowState

WORKFLOW_STATE_SCHEMA_VERSION = 1
_WORKFLOW_STATE_ADAPTER = TypeAdapter(WorkflowState)


class WorkflowSerializationError(ValueError):
    """Raised when workflow state cannot be safely serialized or restored."""


def serialize_workflow_state(state: WorkflowState) -> dict[str, Any]:
    """Return a JSON-compatible, schema-versioned workflow-state envelope.

    Document bytes must already have been externalized through the configured
    ``CareerBridgeObjectStore``. The serialized state therefore contains only
    S3/local object keys, fingerprints, filenames, and normal structured data.
    """

    _reject_binary_values(state, path="workflow_state", seen=set())
    try:
        payload = _WORKFLOW_STATE_ADAPTER.dump_python(state, mode="json")
    except (TypeError, ValueError) as exc:
        raise WorkflowSerializationError(
            "The workflow state could not be converted to JSON-compatible data."
        ) from exc
    return {
        "schema_version": WORKFLOW_STATE_SCHEMA_VERSION,
        "state": payload,
    }


def deserialize_workflow_state(payload: Mapping[str, Any]) -> WorkflowState:
    """Restore a ``WorkflowState`` from its versioned serialized envelope."""

    if not isinstance(payload, Mapping):
        raise WorkflowSerializationError("The serialized workflow state is invalid.")
    schema_version = payload.get("schema_version")
    if schema_version != WORKFLOW_STATE_SCHEMA_VERSION:
        raise WorkflowSerializationError(
            "Unsupported workflow-state schema version "
            f"{schema_version!r}; expected {WORKFLOW_STATE_SCHEMA_VERSION}."
        )
    state_payload = payload.get("state")
    if not isinstance(state_payload, Mapping):
        raise WorkflowSerializationError(
            "The serialized workflow state does not contain a state object."
        )
    try:
        return _WORKFLOW_STATE_ADAPTER.validate_python(dict(state_payload))
    except ValidationError as exc:
        raise WorkflowSerializationError(
            "The serialized workflow state does not match the current schema."
        ) from exc


def workflow_state_json_bytes(state: WorkflowState) -> bytes:
    """Serialize workflow state as canonical UTF-8 JSON bytes."""

    try:
        return _canonical_json_bytes(serialize_workflow_state(state))
    except (TypeError, ValueError) as exc:
        if isinstance(exc, WorkflowSerializationError):
            raise
        raise WorkflowSerializationError(
            "The workflow state contains a value that cannot be represented "
            "as canonical JSON."
        ) from exc


def workflow_state_from_json_bytes(content: bytes | bytearray | str) -> WorkflowState:
    """Deserialize workflow state from UTF-8 JSON content."""

    try:
        text = (
            bytes(content).decode("utf-8")
            if isinstance(content, (bytes, bytearray))
            else str(content)
        )
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowSerializationError(
            "The stored workflow-state document is not valid UTF-8 JSON."
        ) from exc
    return deserialize_workflow_state(payload)


def workflow_state_fingerprint(state: WorkflowState) -> str:
    """Return a deterministic fingerprint for change detection."""

    return hashlib.sha256(workflow_state_json_bytes(state)).hexdigest()


def workflow_payload_fingerprint(content: bytes | bytearray | str) -> str:
    """Fingerprint the serialized workflow document exactly as persisted.

    This intentionally hashes the stored UTF-8 payload rather than
    deserializing and serializing it again. A newer ``WorkflowState`` schema
    may add fields with defaults. Re-serializing an older, otherwise valid
    document would include those new defaults and produce a different digest,
    incorrectly treating schema evolution as object corruption.
    """

    serialized = (
        bytes(content)
        if isinstance(content, (bytes, bytearray))
        else str(content).encode("utf-8")
    )
    return hashlib.sha256(serialized).hexdigest()


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_binary_values(value: Any, *, path: str, seen: set[int]) -> None:
    """Reject embedded bytes anywhere in the nested workflow object graph."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        raise WorkflowSerializationError(
            f"{path} contains document bytes. Persist the document through object "
            "storage and retain only its object key before saving workflow state."
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return

    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)

    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _reject_binary_values(
                getattr(value, name), path=f"{path}.{name}", seen=seen
            )
        return
    if is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            _reject_binary_values(
                getattr(value, item.name), path=f"{path}.{item.name}", seen=seen
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_binary_values(
                item, path=f"{path}[{key!r}]", seen=seen
            )
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _reject_binary_values(
                item, path=f"{path}[{index}]", seen=seen
            )
