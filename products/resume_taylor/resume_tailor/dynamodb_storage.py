"""DynamoDB storage adapters for the Career Bridge Application Builder.

Workflow state uses a dedicated table keyed by a hashed ``workflow_id``. The
table stores optimistic-lock metadata and a private S3 pointer to canonical JSON;
the serialized state body and all document bytes remain outside DynamoDB.

The application repository uses a single DynamoDB table with:

* ``owner_id`` (String) as the partition key.
* ``storage_key`` (String) as the sort key.

Each application and its linked artifacts are separate items in the same owner
partition.  The layout keeps every read tenant-scoped and avoids table scans:

* ``APP#<application_id>``
* ``RESUME_FINDINGS#<application_id>``
* ``INTERVIEW_PREPARATION#<application_id>``
* ``IMPACT#<application_id>``
* ``SOURCE_JOB#<discovered_job_id>`` (duplicate-prevention link)

Document bytes and serialized report payloads are stored in private S3 object
storage. DynamoDB contains only searchable metadata, fingerprints, and S3 object
keys, keeping application items safely below DynamoDB's 400 KB item-size limit.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

import boto3
from botocore.exceptions import ClientError

from .application_tracker import (
    ApplicationRecord,
    InterviewPreparationRecord,
    ResumeFindingsRecord,
    infer_outcomes,
    normalize_application_builder_step,
    normalize_application_status,
    normalize_iso_date,
    normalize_interview_audience,
    normalize_job_url,
    normalize_optional_score,
)
from .object_storage import (
    CareerBridgeObjectStore,
    ObjectNotFoundError,
    application_object_key,
    create_document_store,
    workflow_state_object_key,
)
from .storage import (
    LoadedWorkflowState,
    StorageBackendConfigurationError,
    WorkflowConflictError,
    configured_application_workflow_ttl_seconds,
    configured_scratch_workflow_ttl_seconds,
    normalize_workflow_request_id,
    workflow_retention_class,
)
from .web_state import WorkflowState
from .workflow_serialization import (
    workflow_state_fingerprint,
    workflow_state_from_json_bytes,
    workflow_state_json_bytes,
)

APPLICATION_TABLE_CONFIG_KEY = "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME"
WORKFLOW_TABLE_CONFIG_KEY = "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME"
_APPLICATION_PREFIX = "APP#"
_RESUME_FINDINGS_PREFIX = "RESUME_FINDINGS#"
_INTERVIEW_PREPARATION_PREFIX = "INTERVIEW_PREPARATION#"
_IMPACT_PREFIX = "IMPACT#"
_SOURCE_JOB_PREFIX = "SOURCE_JOB#"

_STATUS_ORDER = {
    "interviewing": 0,
    "screening": 1,
    "ready_to_apply": 2,
    "preparing": 3,
    "considering": 4,
    "draft": 5,
    "applied": 6,
    "offered": 7,
}
_VALID_UPCOMING_EVENT_TYPES = {
    "",
    "application_deadline",
    "interview",
    "follow_up",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _application_key(application_id: str) -> str:
    return f"{_APPLICATION_PREFIX}{application_id}"


def _source_job_key(source_job_id: str) -> str:
    return f"{_SOURCE_JOB_PREFIX}{source_job_id}"


def _resume_findings_key(application_id: str) -> str:
    return f"{_RESUME_FINDINGS_PREFIX}{application_id}"


def _interview_preparation_key(application_id: str) -> str:
    return f"{_INTERVIEW_PREPARATION_PREFIX}{application_id}"


def _impact_key(application_id: str) -> str:
    return f"{_IMPACT_PREFIX}{application_id}"


def _to_dynamodb(value: Any) -> Any:
    """Convert Python floats recursively because boto3 rejects them."""

    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(key): _to_dynamodb(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamodb(item) for item in value]
    return value


def _from_dynamodb(value: Any) -> Any:
    """Return JSON-friendly values from DynamoDB resource responses."""

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _from_dynamodb(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamodb(item) for item in value]
    # boto3 Binary exposes ``value``; bytes and bytearray should pass through.
    binary_value = getattr(value, "value", None)
    if binary_value is not None and value.__class__.__name__ == "Binary":
        return bytes(binary_value)
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None



class DynamoDBWorkflowStore:
    """Versioned workflow repository with DynamoDB metadata and S3 state bodies.

    The DynamoDB table uses ``workflow_id`` (String) as its partition key. The
    original browser/session key is never stored; a SHA-256 digest is used
    instead. Each item contains `version`, `updated_at`,
    `updated_by_request`, other optimistic-lock metadata, and an object key for
    the canonical JSON state document, keeping report-heavy workflows below
    DynamoDB's 400 KB item limit.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        state_factory: Callable[[], WorkflowState],
        *,
        table: Any | None = None,
        document_store: CareerBridgeObjectStore | None = None,
        clock: Callable[[], str] | None = None,
        epoch_clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._state_factory = state_factory
        self._table_override = table
        self._documents = document_store or create_document_store(
            config, require_s3=True
        )
        self._clock = clock or _now
        self._epoch_clock = epoch_clock or time.time
        self._resolved_table: Any | None = None
        self._scratch_ttl_seconds = configured_scratch_workflow_ttl_seconds(config)
        self._application_ttl_seconds = configured_application_workflow_ttl_seconds(
            config
        )

        table_name = str(config.get(WORKFLOW_TABLE_CONFIG_KEY) or "").strip()
        if table is None and not table_name:
            raise StorageBackendConfigurationError(
                f"{WORKFLOW_TABLE_CONFIG_KEY} is required when "
                "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND=dynamodb."
            )
        self._table_name = table_name

    def _table(self):
        if self._table_override is not None:
            return self._table_override
        if self._resolved_table is None:
            region = str(
                self._config.get("AWS_REGION")
                or self._config.get("AWS_DEFAULT_REGION")
                or "us-west-2"
            ).strip()
            self._resolved_table = boto3.resource(
                "dynamodb", region_name=region
            ).Table(self._table_name)
        return self._resolved_table

    @staticmethod
    def _workflow_id(workflow_key: str) -> str:
        return hashlib.sha256(workflow_key.encode("utf-8")).hexdigest()

    def _key(self, workflow_key: str) -> dict[str, str]:
        return {"workflow_id": self._workflow_id(workflow_key)}

    def _retention(self, workflow_key: str) -> tuple[str, int | None]:
        workflow_type = workflow_retention_class(workflow_key)
        if workflow_type == "scratch":
            return workflow_type, self._scratch_ttl_seconds
        return workflow_type, self._application_ttl_seconds or None

    def _migrate_legacy_workflow_metadata(
        self,
        workflow_key: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        """Backfill concurrency metadata and remove obsolete blanket TTLs.

        The migration itself is a conditional, version-incrementing update so
        it cannot race with a browser mutation or silently preserve an item
        without ``updated_by_request``.
        """

        workflow_type, ttl_seconds = self._retention(workflow_key)
        remove_expiry = (
            workflow_type == "application"
            and ttl_seconds is None
            and "expires_at" in item
        )
        missing_request_id = not str(item.get("updated_by_request") or "").strip()
        missing_updated_at = not str(item.get("updated_at") or "").strip()
        missing_version = "version" not in item
        missing_retention = (
            not str(item.get("workflow_type") or "").strip()
            or not str(item.get("retention_policy") or "").strip()
        )
        if not (
            remove_expiry
            or missing_request_id
            or missing_updated_at
            or missing_version
            or missing_retention
        ):
            return item

        current_version = int(item.get("version") or 0)
        migrated_version = current_version + 1
        migrated_at = self._clock()
        migrated_by = (
            "SYSTEM-TTL-MIGRATION"
            if remove_expiry
            else "SYSTEM-CONCURRENCY-MIGRATION"
        )
        retention_policy = "dynamodb_ttl" if ttl_seconds else "retained"
        condition = (
            "attribute_not_exists(#version)"
            if missing_version
            else "#version = :expected_version"
        )
        values: dict[str, Any] = {
            ":workflow_type": workflow_type,
            ":retention_policy": retention_policy,
            ":new_version": migrated_version,
            ":updated_at": migrated_at,
            ":updated_by_request": migrated_by,
        }
        if not missing_version:
            values[":expected_version"] = current_version
        try:
            self._table().update_item(
                Key=self._key(workflow_key),
                UpdateExpression=(
                    "SET #workflow_type = :workflow_type, "
                    "#retention_policy = :retention_policy, "
                    "#version = :new_version, #updated_at = :updated_at, "
                    "#updated_by_request = :updated_by_request"
                    + (" REMOVE #expires_at" if remove_expiry else "")
                ),
                ConditionExpression=condition,
                ExpressionAttributeNames={
                    "#workflow_type": "workflow_type",
                    "#retention_policy": "retention_policy",
                    "#version": "version",
                    "#updated_at": "updated_at",
                    "#updated_by_request": "updated_by_request",
                    **({"#expires_at": "expires_at"} if remove_expiry else {}),
                },
                ExpressionAttributeValues=_to_dynamodb(values),
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code") or "")
            if code != "ConditionalCheckFailedException":
                raise
            return item

        migrated = dict(item)
        if remove_expiry:
            migrated.pop("expires_at", None)
        migrated["workflow_type"] = workflow_type
        migrated["retention_policy"] = retention_policy
        migrated["version"] = migrated_version
        migrated["updated_at"] = migrated_at
        migrated["updated_by_request"] = migrated_by
        return migrated

    def _get_item(self, workflow_key: str) -> dict[str, Any] | None:
        response = self._table().get_item(
            Key=self._key(workflow_key), ConsistentRead=True
        )
        item = response.get("Item")
        return _from_dynamodb(item) if item else None

    def _state_from_item(self, item: Mapping[str, Any]) -> WorkflowState:
        object_key = str(item.get("state_json_key") or "")
        if object_key:
            content: bytes | str = self._documents.get(object_key)
        else:
            # Backward-compatible migration path for an early inline prototype.
            inline = item.get("state_json") or item.get("state")
            if isinstance(inline, Mapping):
                content = json.dumps(inline, ensure_ascii=False, sort_keys=True)
            elif isinstance(inline, (bytes, bytearray, str)):
                content = inline
            else:
                raise RuntimeError(
                    "The DynamoDB workflow item does not reference serialized state."
                )
        state = workflow_state_from_json_bytes(content)
        actual_fingerprint = workflow_state_fingerprint(state)
        stored_fingerprint = str(item.get("fingerprint") or "")
        if stored_fingerprint and stored_fingerprint != actual_fingerprint:
            raise RuntimeError(
                "The stored workflow-state fingerprint does not match its object."
            )
        return state

    def new_id(self) -> str:
        return secrets.token_urlsafe(24)

    def load(self, workflow_key: str) -> LoadedWorkflowState:
        item = self._get_item(workflow_key)
        if item is None:
            state = self._state_factory()
            return LoadedWorkflowState(
                state=state,
                version=0,
                fingerprint=workflow_state_fingerprint(state),
                updated_at="",
                updated_by_request="",
            )
        item = self._migrate_legacy_workflow_metadata(workflow_key, item)
        state = self._state_from_item(item)
        return LoadedWorkflowState(
            state=state,
            version=max(0, int(item.get("version") or 0)),
            fingerprint=workflow_state_fingerprint(state),
            updated_at=str(item.get("updated_at") or ""),
            updated_by_request=str(item.get("updated_by_request") or ""),
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
    ) -> LoadedWorkflowState:
        expected_version = max(0, int(expected_version))
        request_id = normalize_workflow_request_id(updated_by_request)
        new_version = expected_version + 1
        serialized = workflow_state_json_bytes(state)
        fingerprint = hashlib.sha256(serialized).hexdigest()
        workflow_type, ttl_seconds = self._retention(workflow_key)
        object_key = workflow_state_object_key(
            self._config,
            workflow_key,
            new_version,
            fingerprint,
            retention_class=workflow_type,
        )
        self._documents.put(
            object_key,
            serialized,
            "application/json",
            metadata={
                "artifact-type": "workflow-state",
                "schema-version": "1",
                "workflow-version": str(new_version),
                "fingerprint": fingerprint,
                "retention-class": workflow_type,
            },
        )

        names = {
            "#entity_type": "entity_type",
            "#workflow_type": "workflow_type",
            "#retention_policy": "retention_policy",
            "#version": "version",
            "#fingerprint": "fingerprint",
            "#state_json_key": "state_json_key",
            "#updated_at": "updated_at",
            "#updated_by_request": "updated_by_request",
            "#expires_at": "expires_at",
            # Remove fields used by early inline-state prototypes whenever a
            # workflow is successfully rewritten through the S3-backed adapter.
            "#legacy_state_json": "state_json",
            "#legacy_state": "state",
        }
        values: dict[str, Any] = {
            ":entity_type": "career_bridge_workflow",
            ":workflow_type": workflow_type,
            ":retention_policy": "dynamodb_ttl" if ttl_seconds else "retained",
            ":new_version": new_version,
            ":fingerprint": fingerprint,
            ":state_json_key": object_key,
            ":updated_at": self._clock(),
            ":updated_by_request": request_id,
        }
        if ttl_seconds is not None:
            values[":expires_at"] = int(self._epoch_clock()) + ttl_seconds
        if expected_version == 0:
            condition = "attribute_not_exists(#workflow_id)"
            names["#workflow_id"] = "workflow_id"
        else:
            condition = "#version = :expected_version"
            values[":expected_version"] = expected_version

        try:
            response = self._table().update_item(
                Key=self._key(workflow_key),
                UpdateExpression=(
                    "SET #entity_type = :entity_type, "
                    "#workflow_type = :workflow_type, "
                    "#retention_policy = :retention_policy, "
                    "#version = :new_version, #fingerprint = :fingerprint, "
                    "#state_json_key = :state_json_key, #updated_at = :updated_at, "
                    "#updated_by_request = :updated_by_request"
                    + (
                        ", #expires_at = :expires_at"
                        if ttl_seconds is not None
                        else ""
                    )
                    + " REMOVE #legacy_state_json, #legacy_state"
                    + (", #expires_at" if ttl_seconds is None else "")
                ),
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=_to_dynamodb(values),
                ReturnValues="ALL_OLD",
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code") or "")
            if code == "ConditionalCheckFailedException":
                latest = self._get_item(workflow_key)
                if str((latest or {}).get("state_json_key") or "") != object_key:
                    self._delete_object_quietly(object_key)
                raise WorkflowConflictError(
                    workflow_key,
                    expected_version=expected_version,
                    actual_version=(
                        int(latest.get("version") or 0) if latest else 0
                    ),
                    actual_updated_by_request=str(
                        (latest or {}).get("updated_by_request") or ""
                    ),
                ) from exc
            self._delete_if_unreferenced(workflow_key, object_key)
            raise
        except Exception:
            self._delete_if_unreferenced(workflow_key, object_key)
            raise

        previous = _from_dynamodb(response.get("Attributes") or {})
        previous_key = str(previous.get("state_json_key") or "")
        if previous_key and previous_key != object_key:
            self._delete_object_quietly(previous_key)
        return LoadedWorkflowState(
            state=workflow_state_from_json_bytes(serialized),
            version=new_version,
            fingerprint=fingerprint,
            updated_at=str(values[":updated_at"]),
            updated_by_request=request_id,
        )

    def reset(self, workflow_key: str) -> WorkflowState:
        loaded = self.load(workflow_key)
        return self.save(
            workflow_key,
            self._state_factory(),
            expected_version=loaded.version,
            updated_by_request="SYSTEM-RESET",
        ).state

    def peek(self, workflow_key: str) -> WorkflowState | None:
        item = self._get_item(workflow_key)
        return self._state_from_item(item) if item is not None else None

    def delete(self, workflow_key: str) -> None:
        response = self._table().delete_item(
            Key=self._key(workflow_key), ReturnValues="ALL_OLD"
        )
        previous = _from_dynamodb(response.get("Attributes") or {})
        self._delete_object_quietly(str(previous.get("state_json_key") or ""))

    def _delete_object_quietly(self, object_key: str) -> None:
        if not object_key:
            return
        try:
            self._documents.delete(object_key)
        except Exception:
            # A failed cleanup must not hide the original conditional-write or
            # DynamoDB error. Bucket versioning/lifecycle policies provide the
            # final orphan-object safety net.
            return

    def _delete_if_unreferenced(self, workflow_key: str, object_key: str) -> None:
        """Avoid deleting an object another identical concurrent save committed."""

        try:
            latest = self._get_item(workflow_key)
        except Exception:
            latest = None
        if str((latest or {}).get("state_json_key") or "") != object_key:
            self._delete_object_quietly(object_key)


class DynamoDBApplicationStore:
    """DynamoDB implementation of the complete ``ApplicationStore`` protocol."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        table: Any | None = None,
        document_store: CareerBridgeObjectStore | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._table_override = table
        self._documents = document_store or create_document_store(config, require_s3=True)
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or _now
        self._resolved_table: Any | None = None

        table_name = str(config.get(APPLICATION_TABLE_CONFIG_KEY) or "").strip()
        if table is None and not table_name:
            raise StorageBackendConfigurationError(
                f"{APPLICATION_TABLE_CONFIG_KEY} is required when "
                "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=dynamodb."
            )
        self._table_name = table_name

    def _table(self):
        if self._table_override is not None:
            return self._table_override
        if self._resolved_table is None:
            region = str(
                self._config.get("AWS_REGION")
                or self._config.get("AWS_DEFAULT_REGION")
                or "us-west-2"
            ).strip()
            self._resolved_table = boto3.resource(
                "dynamodb",
                region_name=region,
            ).Table(self._table_name)
        return self._resolved_table

    @staticmethod
    def _key(owner_id: str, storage_key: str) -> dict[str, str]:
        return {"owner_id": owner_id, "storage_key": storage_key}

    def _get_item(self, owner_id: str, storage_key: str) -> dict[str, Any] | None:
        response = self._table().get_item(
            Key=self._key(owner_id, storage_key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return _from_dynamodb(item) if item else None

    def _put_item(self, item: dict[str, Any], **kwargs: Any) -> None:
        self._table().put_item(Item=_to_dynamodb(item), **kwargs)

    def _query_prefix(self, owner_id: str, prefix: str) -> list[dict[str, Any]]:
        query_args: dict[str, Any] = {
            "KeyConditionExpression": (
                "#owner_id = :owner_id AND begins_with(#storage_key, :prefix)"
            ),
            "ExpressionAttributeNames": {
                "#owner_id": "owner_id",
                "#storage_key": "storage_key",
            },
            "ExpressionAttributeValues": {
                ":owner_id": owner_id,
                ":prefix": prefix,
            },
        }
        items: list[dict[str, Any]] = []
        while True:
            response = self._table().query(**query_args)
            items.extend(_from_dynamodb(item) for item in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            query_args["ExclusiveStartKey"] = last_key

    def _put_document(
        self,
        owner_id: str,
        application_id: str,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        artifact_type: str,
    ) -> str:
        fingerprint = hashlib.sha256(content).hexdigest()
        object_key = application_object_key(
            self._config,
            owner_id,
            application_id,
            filename,
            category=artifact_type,
            fingerprint=fingerprint,
        )
        self._documents.put(
            object_key,
            content,
            content_type,
            metadata={
                "owner-namespace": hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:32],
                "application-id": application_id,
                "artifact-type": artifact_type,
            },
        )
        return object_key

    def _put_json_document(
        self,
        owner_id: str,
        application_id: str,
        filename: str,
        payload: str,
        *,
        artifact_type: str,
    ) -> str:
        return self._put_document(
            owner_id,
            application_id,
            filename,
            payload.encode("utf-8"),
            "application/json",
            artifact_type=artifact_type,
        )

    def _read_json_document(
        self, item: Mapping[str, Any], key_field: str, legacy_field: str
    ) -> str:
        object_key = str(item.get(key_field) or "")
        if object_key:
            return self._documents.get(object_key).decode("utf-8")
        return str(item.get(legacy_field) or "{}")

    def _delete_object_keys(self, *items: Mapping[str, Any] | None) -> None:
        keys: set[str] = set()
        for item in items:
            if not item:
                continue
            for field, value in item.items():
                if field.endswith("_key") and isinstance(value, str) and value:
                    keys.add(value)
        for object_key in keys:
            self._documents.delete(object_key)

    @staticmethod
    def _application_record(item: Mapping[str, Any]) -> ApplicationRecord:
        # ``resume_bytes`` is read only for migration compatibility with items
        # written before S3 externalization. New writes never include it.
        legacy_resume_bytes = item.get("resume_bytes")
        if isinstance(legacy_resume_bytes, bytearray):
            legacy_resume_bytes = bytes(legacy_resume_bytes)
        return ApplicationRecord(
            id=str(item.get("id") or ""),
            owner_id=str(item.get("owner_id") or ""),
            company=str(item.get("company") or ""),
            role=str(item.get("role") or ""),
            job_url=str(item.get("job_url") or ""),
            interview_audience=str(item.get("interview_audience") or ""),
            application_date=str(item.get("application_date") or ""),
            status=normalize_application_status(str(item.get("status") or "")),
            resume_version=str(item.get("resume_version") or "Not started"),
            resume_style=str(item.get("resume_style") or ""),
            alignment_score=_optional_float(item.get("alignment_score")),
            overall_score=_optional_float(item.get("overall_score")),
            interview_readiness=_optional_float(item.get("interview_readiness")),
            screening_received=bool(item.get("screening_received")),
            interview_received=bool(item.get("interview_received")),
            offer_received=bool(item.get("offer_received")),
            notes=str(item.get("notes") or ""),
            next_action=str(item.get("next_action") or ""),
            next_follow_up_date=str(item.get("next_follow_up_date") or ""),
            upcoming_event_date=str(item.get("upcoming_event_date") or ""),
            upcoming_event_type=str(item.get("upcoming_event_type") or ""),
            job_description=str(item.get("job_description") or ""),
            workflow_step=normalize_application_builder_step(
                str(item.get("workflow_step") or "setup")
            ),
            created_at=str(item.get("created_at") or ""),
            updated_at=str(item.get("updated_at") or ""),
            resume_filename=str(item.get("resume_filename") or ""),
            resume_bytes=(
                legacy_resume_bytes if isinstance(legacy_resume_bytes, bytes) else None
            ),
            resume_fingerprint=str(item.get("resume_fingerprint") or ""),
            resume_docx_key=str(item.get("resume_docx_key") or ""),
            resume_pdf_key=str(item.get("resume_pdf_key") or ""),
            resume_pdf_filename=str(item.get("resume_pdf_filename") or ""),
            original_resume_key=str(item.get("original_resume_key") or ""),
            source_job_id=str(item.get("source_job_id") or ""),
        )

    @staticmethod
    def _application_item(record: ApplicationRecord) -> dict[str, Any]:
        # Deliberately exclude ``record.resume_bytes``. DynamoDB stores only S3
        # keys and metadata for documents.
        return {
            "owner_id": record.owner_id,
            "storage_key": _application_key(record.id),
            "entity_type": "application",
            "id": record.id,
            "company": record.company,
            "company_key": record.company.casefold(),
            "role": record.role,
            "role_key": record.role.casefold(),
            "job_url": record.job_url,
            "interview_audience": record.interview_audience,
            "application_date": record.application_date,
            "status": record.status,
            "resume_version": record.resume_version,
            "resume_style": record.resume_style,
            "alignment_score": record.alignment_score,
            "overall_score": record.overall_score,
            "interview_readiness": record.interview_readiness,
            "screening_received": record.screening_received,
            "interview_received": record.interview_received,
            "offer_received": record.offer_received,
            "notes": record.notes,
            "next_action": record.next_action,
            "next_follow_up_date": record.next_follow_up_date,
            "upcoming_event_date": record.upcoming_event_date,
            "upcoming_event_type": record.upcoming_event_type,
            "job_description": record.job_description,
            "workflow_step": record.workflow_step,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "resume_filename": record.resume_filename,
            "resume_docx_key": record.resume_docx_key,
            "resume_pdf_key": record.resume_pdf_key,
            "resume_pdf_filename": record.resume_pdf_filename,
            "original_resume_key": record.original_resume_key,
            "source_job_id": record.source_job_id,
            "resume_fingerprint": record.resume_fingerprint,
        }

    def list_for_owner(self, owner_id: str) -> list[ApplicationRecord]:
        records = [
            self._application_record(item)
            for item in self._query_prefix(owner_id, _APPLICATION_PREFIX)
        ]
        # Stable sorts preserve the status/event/updated ordering contract.
        records.sort(key=lambda item: item.updated_at, reverse=True)
        records.sort(key=lambda item: item.upcoming_event_date or "9999-12-31")
        records.sort(key=lambda item: _STATUS_ORDER.get(item.status, 8))
        return records

    def get(
        self,
        owner_id: str,
        application_id: str,
        *,
        include_resume_bytes: bool = True,
    ) -> ApplicationRecord | None:
        item = self._get_item(owner_id, _application_key(application_id))
        if not item:
            return None
        record = self._application_record(item)
        if (
            include_resume_bytes
            and record.resume_bytes is None
            and record.resume_docx_key
        ):
            try:
                resume_bytes = self._documents.get(record.resume_docx_key)
            except ObjectNotFoundError:
                resume_bytes = None
            record = ApplicationRecord(
                **{**record.__dict__, "resume_bytes": resume_bytes}
            )
        return record

    def get_resume_findings(
        self, owner_id: str, application_id: str
    ) -> ResumeFindingsRecord | None:
        item = self._get_item(owner_id, _resume_findings_key(application_id))
        if item is None:
            return None
        return ResumeFindingsRecord(
            application_id=application_id,
            owner_id=owner_id,
            snapshot_json=self._read_json_document(
                item, "snapshot_json_key", "snapshot_json"
            ),
            fingerprint=str(item.get("fingerprint") or ""),
            created_at=str(item.get("created_at") or ""),
            updated_at=str(item.get("updated_at") or ""),
        )

    def save_resume_findings(
        self,
        owner_id: str,
        application_id: str,
        *,
        snapshot_json: str,
        fingerprint: str,
    ) -> ResumeFindingsRecord:
        if self.get(owner_id, application_id, include_resume_bytes=False) is None:
            raise ValueError("The selected application does not exist.")
        now = self._clock()
        existing_item = self._get_item(owner_id, _resume_findings_key(application_id))
        existing = self.get_resume_findings(owner_id, application_id)
        snapshot_json_key = self._put_json_document(
            owner_id,
            application_id,
            "resume-findings.json",
            snapshot_json,
            artifact_type="resume-findings",
        )
        self._put_item(
            {
                "owner_id": owner_id,
                "storage_key": _resume_findings_key(application_id),
                "entity_type": "resume_findings",
                "application_id": application_id,
                "snapshot_json_key": snapshot_json_key,
                "fingerprint": fingerprint.strip(),
                "created_at": existing.created_at if existing else now,
                "updated_at": now,
            }
        )
        if existing_item and existing_item.get("snapshot_json_key") != snapshot_json_key:
            self._delete_object_keys(existing_item)
        saved = self.get_resume_findings(owner_id, application_id)
        if saved is None:  # pragma: no cover
            raise RuntimeError("The resume findings snapshot was not saved.")
        return saved

    def get_interview_preparation(
        self, owner_id: str, application_id: str
    ) -> InterviewPreparationRecord | None:
        item = self._get_item(owner_id, _interview_preparation_key(application_id))
        if item is None:
            return None
        return InterviewPreparationRecord(
            application_id=application_id,
            owner_id=owner_id,
            content_json=self._read_json_document(
                item, "content_json_key", "content_json"
            ),
            job_description_fingerprint=str(
                item.get("job_description_fingerprint") or ""
            ),
            evidence_fingerprint=str(item.get("evidence_fingerprint") or ""),
            evidence_source_label=str(item.get("evidence_source_label") or ""),
            evidence_snapshot_json=self._read_json_document(
                item, "evidence_snapshot_json_key", "evidence_snapshot_json"
            ),
            resume_findings_fingerprint=str(
                item.get("resume_findings_fingerprint") or ""
            ),
            resume_findings_snapshot_json=self._read_json_document(
                item,
                "resume_findings_snapshot_json_key",
                "resume_findings_snapshot_json",
            ),
            model_name=str(item.get("model_name") or ""),
            created_at=str(item.get("created_at") or ""),
            updated_at=str(item.get("updated_at") or ""),
        )

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
    ) -> InterviewPreparationRecord:
        if self.get(owner_id, application_id, include_resume_bytes=False) is None:
            raise ValueError("The selected application does not exist.")
        now = self._clock()
        existing_item = self._get_item(
            owner_id, _interview_preparation_key(application_id)
        )
        existing = self.get_interview_preparation(owner_id, application_id)
        content_json_key = self._put_json_document(
            owner_id,
            application_id,
            "interview-preparation.json",
            content_json,
            artifact_type="interview-preparation",
        )
        evidence_snapshot_json_key = self._put_json_document(
            owner_id,
            application_id,
            "evidence-snapshot.json",
            evidence_snapshot_json,
            artifact_type="interview-evidence",
        )
        resume_findings_snapshot_json_key = self._put_json_document(
            owner_id,
            application_id,
            "resume-findings-snapshot.json",
            resume_findings_snapshot_json,
            artifact_type="interview-resume-findings",
        )
        self._put_item(
            {
                "owner_id": owner_id,
                "storage_key": _interview_preparation_key(application_id),
                "entity_type": "interview_preparation",
                "application_id": application_id,
                "content_json_key": content_json_key,
                "job_description_fingerprint": job_description_fingerprint.strip(),
                "evidence_fingerprint": evidence_fingerprint.strip(),
                "evidence_source_label": evidence_source_label.strip(),
                "evidence_snapshot_json_key": evidence_snapshot_json_key,
                "resume_findings_fingerprint": resume_findings_fingerprint.strip(),
                "resume_findings_snapshot_json_key": resume_findings_snapshot_json_key,
                "model_name": model_name.strip(),
                "created_at": existing.created_at if existing else now,
                "updated_at": now,
            }
        )
        if existing_item:
            retained = {
                content_json_key,
                evidence_snapshot_json_key,
                resume_findings_snapshot_json_key,
            }
            stale = {
                value
                for key, value in existing_item.items()
                if key.endswith("_key") and isinstance(value, str) and value
                and value not in retained
            }
            for object_key in stale:
                self._documents.delete(object_key)
        saved = self.get_interview_preparation(owner_id, application_id)
        if saved is None:  # pragma: no cover
            raise RuntimeError("Interview preparation was not saved.")
        return saved

    def find_by_source_job(
        self, owner_id: str, source_job_id: str
    ) -> ApplicationRecord | None:
        normalized = str(source_job_id or "").strip()
        if not normalized:
            return None
        link = self._get_item(owner_id, _source_job_key(normalized))
        if link is not None:
            application_id = str(link.get("application_id") or "")
            if application_id:
                return self.get(owner_id, application_id, include_resume_bytes=False)
        # Migration fallback for application items written before source links.
        return next(
            (
                record
                for record in self.list_for_owner(owner_id)
                if record.source_job_id == normalized
            ),
            None,
        )

    def find_snapshot(
        self,
        owner_id: str,
        *,
        resume_fingerprint: str,
        company: str,
        role: str,
    ) -> ApplicationRecord | None:
        if not resume_fingerprint:
            return None
        company_key = company.casefold()
        role_key = role.casefold()
        matches = [
            record
            for record in self.list_for_owner(owner_id)
            if record.resume_fingerprint == resume_fingerprint
            and record.company.casefold() == company_key
            and record.role.casefold() == role_key
        ]
        return max(matches, key=lambda item: item.created_at) if matches else None

    def create(
        self,
        owner_id: str,
        *,
        company: str,
        role: str,
        job_url: str = "",
        interview_audience: str = "",
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
        source_job_id: str = "",
    ) -> ApplicationRecord:
        normalized_status = normalize_application_status(status)
        inferred_screening, inferred_interview, inferred_offer = infer_outcomes(
            normalized_status
        )
        now = self._clock()
        application_id = self._id_factory()
        normalized_resume_filename = resume_filename.strip()
        resume_docx_key = ""
        resume_pdf_key = ""
        if resume_bytes is not None:
            resume_docx_key = self._put_document(
                owner_id,
                application_id,
                normalized_resume_filename or "resume.docx",
                resume_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                artifact_type="final-resume-docx",
            )
        normalized_pdf_filename = resume_pdf_filename.strip()
        if resume_pdf_bytes is not None:
            if not normalized_pdf_filename:
                normalized_pdf_filename = (
                    f"{Path(normalized_resume_filename).stem}.pdf"
                    if normalized_resume_filename
                    else "resume.pdf"
                )
            resume_pdf_key = self._put_document(
                owner_id,
                application_id,
                normalized_pdf_filename,
                resume_pdf_bytes,
                "application/pdf",
                artifact_type="final-resume-pdf",
            )
        record = ApplicationRecord(
            id=application_id,
            owner_id=owner_id,
            company=company.strip() or "Company not specified",
            role=role.strip() or "Role not specified",
            job_url=normalize_job_url(job_url),
            interview_audience=normalize_interview_audience(interview_audience),
            application_date=normalize_iso_date(application_date),
            status=normalized_status,
            resume_version=resume_version.strip() or "Not started",
            resume_style=resume_style.strip(),
            alignment_score=normalize_optional_score(alignment_score),
            overall_score=normalize_optional_score(overall_score),
            interview_readiness=normalize_optional_score(interview_readiness),
            screening_received=(
                inferred_screening
                if screening_received is None
                else bool(screening_received)
            ),
            interview_received=(
                inferred_interview
                if interview_received is None
                else bool(interview_received)
            ),
            offer_received=(
                inferred_offer if offer_received is None else bool(offer_received)
            ),
            notes=notes.strip(),
            next_action=next_action.strip(),
            next_follow_up_date=normalize_iso_date(next_follow_up_date),
            upcoming_event_date=normalize_iso_date(upcoming_event_date),
            upcoming_event_type=(
                upcoming_event_type.strip()
                if upcoming_event_type in _VALID_UPCOMING_EVENT_TYPES
                else ""
            ),
            job_description=job_description.strip(),
            workflow_step=normalize_application_builder_step(workflow_step),
            created_at=now,
            updated_at=now,
            resume_filename=normalized_resume_filename,
            resume_bytes=resume_bytes,
            resume_fingerprint=resume_fingerprint,
            resume_docx_key=resume_docx_key,
            resume_pdf_key=resume_pdf_key,
            resume_pdf_filename=normalized_pdf_filename,
            source_job_id=str(source_job_id or "").strip(),
        )
        source_link_created = False
        try:
            if record.source_job_id:
                self._put_item(
                    {
                        "owner_id": owner_id,
                        "storage_key": _source_job_key(record.source_job_id),
                        "entity_type": "application_source_job_link",
                        "source_job_id": record.source_job_id,
                        "application_id": record.id,
                        "created_at": now,
                    },
                    ConditionExpression="attribute_not_exists(#storage_key)",
                    ExpressionAttributeNames={"#storage_key": "storage_key"},
                )
                source_link_created = True
            self._put_item(
                self._application_item(record),
                ConditionExpression="attribute_not_exists(#storage_key)",
                ExpressionAttributeNames={"#storage_key": "storage_key"},
            )
        except Exception:
            if source_link_created:
                self._table().delete_item(
                    Key=self._key(owner_id, _source_job_key(record.source_job_id))
                )
            self._delete_object_keys(
                {
                    "resume_docx_key": resume_docx_key,
                    "resume_pdf_key": resume_pdf_key,
                }
            )
            raise
        return record

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
        interview_audience: str | None = None,
    ) -> ApplicationRecord | None:
        current = self.get(owner_id, application_id, include_resume_bytes=False)
        if current is None:
            return None
        normalized_status = normalize_application_status(status)
        inferred_screening, inferred_interview, inferred_offer = infer_outcomes(
            normalized_status
        )
        offer = bool(offer_received or inferred_offer)
        interview = bool(interview_received or inferred_interview or offer)
        screening = bool(screening_received or inferred_screening or interview)
        record = ApplicationRecord(
            **{
                **current.__dict__,
                "company": company.strip() or "Company not specified",
                "role": role.strip() or "Role not specified",
                "job_url": normalize_job_url(job_url),
                "interview_audience": (
                    current.interview_audience
                    if interview_audience is None
                    else normalize_interview_audience(interview_audience)
                ),
                "application_date": normalize_iso_date(application_date),
                "status": normalized_status,
                "screening_received": screening,
                "interview_received": interview,
                "offer_received": offer,
                "notes": notes.strip(),
                "next_action": next_action.strip(),
                "next_follow_up_date": normalize_iso_date(next_follow_up_date),
                "interview_readiness": normalize_optional_score(
                    interview_readiness
                ),
                "upcoming_event_date": normalize_iso_date(upcoming_event_date),
                "upcoming_event_type": (
                    upcoming_event_type.strip()
                    if upcoming_event_type in _VALID_UPCOMING_EVENT_TYPES
                    else ""
                ),
                "job_description": (
                    current.job_description
                    if job_description is None
                    else job_description.strip()
                ),
                "updated_at": self._clock(),
            }
        )
        self._put_item(self._application_item(record))
        return record

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
    ) -> ApplicationRecord | None:
        current = self.get(owner_id, application_id, include_resume_bytes=False)
        if current is None:
            return None
        record = ApplicationRecord(
            **{
                **current.__dict__,
                "workflow_step": (
                    normalize_application_builder_step(workflow_step)
                    if workflow_step.strip()
                    else current.workflow_step
                ),
                "resume_version": (resume_version or current.resume_version).strip(),
                "company": (company or current.company).strip(),
                "role": (role or current.role).strip(),
                "job_description": (
                    current.job_description
                    if job_description is None
                    else job_description.strip()
                ),
                "status": normalize_application_status(status or current.status),
                "original_resume_key": (
                    current.original_resume_key
                    if original_resume_key is None
                    else original_resume_key.strip()
                ),
                "updated_at": self._clock(),
            }
        )
        self._put_item(self._application_item(record))
        return record

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
    ) -> ApplicationRecord | None:
        current = self.get(
            owner_id, application_id, include_resume_bytes=False
        )
        if current is None:
            return None
        normalized_resume_filename = resume_filename.strip() or "resume.docx"
        resume_docx_key = self._put_document(
            owner_id,
            application_id,
            normalized_resume_filename,
            resume_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            artifact_type="final-resume-docx",
        )
        resume_pdf_key = current.resume_pdf_key
        normalized_pdf_filename = (
            resume_pdf_filename.strip() or current.resume_pdf_filename
        )
        if resume_pdf_bytes is not None:
            if not normalized_pdf_filename:
                normalized_pdf_filename = f"{Path(normalized_resume_filename).stem}.pdf"
            resume_pdf_key = self._put_document(
                owner_id,
                application_id,
                normalized_pdf_filename,
                resume_pdf_bytes,
                "application/pdf",
                artifact_type="final-resume-pdf",
            )
        status = current.status
        if status in {"draft", "considering", "preparing"}:
            status = "ready_to_apply"
        record = ApplicationRecord(
            **{
                **current.__dict__,
                "resume_version": resume_version,
                "resume_style": resume_style,
                "alignment_score": normalize_optional_score(alignment_score),
                "overall_score": normalize_optional_score(overall_score),
                "resume_filename": normalized_resume_filename,
                "resume_bytes": resume_bytes,
                "resume_fingerprint": resume_fingerprint,
                "resume_docx_key": resume_docx_key,
                "resume_pdf_key": resume_pdf_key,
                "resume_pdf_filename": normalized_pdf_filename,
                "workflow_step": "evidence_export",
                "status": status,
                "updated_at": self._clock(),
            }
        )
        self._put_item(self._application_item(record))
        stale_keys = {current.resume_docx_key, current.resume_pdf_key} - {
            resume_docx_key,
            resume_pdf_key,
            "",
        }
        for object_key in stale_keys:
            self._documents.delete(object_key)
        return record

    def save_impact_snapshot(
        self,
        owner_id: str,
        application_id: str,
        snapshot: dict[str, object],
    ) -> dict[str, object]:
        if self.get(owner_id, application_id, include_resume_bytes=False) is None:
            raise ValueError("The selected application does not exist.")
        now = self._clock()
        existing_item = self._get_item(owner_id, _impact_key(application_id))
        existing = self.get_impact_snapshot(owner_id, application_id)
        alignment_improvement = snapshot.get("alignment_improvement")
        item = {
            "owner_id": owner_id,
            "storage_key": _impact_key(application_id),
            "entity_type": "impact_snapshot",
            "application_id": application_id,
            "credentials_identified": int(
                snapshot.get("credentials_identified") or 0
            ),
            "terminology_clarified": int(
                snapshot.get("terminology_clarified") or 0
            ),
            "unsupported_claims_prevented": int(
                snapshot.get("unsupported_claims_prevented") or 0
            ),
            "relevant_experience_recovered": int(
                snapshot.get("relevant_experience_recovered") or 0
            ),
            "baseline_alignment_score": normalize_optional_score(
                snapshot.get("baseline_alignment_score")
            ),
            "current_alignment_score": normalize_optional_score(
                snapshot.get("current_alignment_score")
            ),
            "alignment_improvement": (
                round(float(alignment_improvement), 1)
                if alignment_improvement is not None
                else None
            ),
            "verified_resume_ready": bool(snapshot.get("verified_resume_ready")),
            "details_json_key": self._put_json_document(
                owner_id,
                application_id,
                "impact-snapshot.json",
                json.dumps(
                    snapshot,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
                artifact_type="impact-snapshot",
            ),
            "created_at": str(existing.get("created_at")) if existing else now,
            "updated_at": now,
        }
        self._put_item(item)
        if (
            existing_item
            and existing_item.get("details_json_key") != item.get("details_json_key")
        ):
            self._delete_object_keys(existing_item)
        saved = self.get_impact_snapshot(owner_id, application_id)
        if saved is None:  # pragma: no cover
            raise RuntimeError("The application impact snapshot was not saved.")
        return saved

    def get_impact_snapshot(
        self, owner_id: str, application_id: str
    ) -> dict[str, object] | None:
        item = self._get_item(owner_id, _impact_key(application_id))
        return self._impact_snapshot(item) if item else None

    def list_impact_snapshots(self, owner_id: str) -> list[dict[str, object]]:
        snapshots = [
            self._impact_snapshot(item)
            for item in self._query_prefix(owner_id, _IMPACT_PREFIX)
        ]
        snapshots.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return snapshots

    def _impact_snapshot(self, item: Mapping[str, Any]) -> dict[str, object]:
        try:
            details = json.loads(
                self._read_json_document(item, "details_json_key", "details_json")
            )
        except (TypeError, json.JSONDecodeError):
            details = {}
        return {
            "application_id": str(item.get("application_id") or ""),
            "owner_id": str(item.get("owner_id") or ""),
            "credentials_identified": int(item.get("credentials_identified") or 0),
            "terminology_clarified": int(item.get("terminology_clarified") or 0),
            "unsupported_claims_prevented": int(
                item.get("unsupported_claims_prevented") or 0
            ),
            "relevant_experience_recovered": int(
                item.get("relevant_experience_recovered") or 0
            ),
            "baseline_alignment_score": _optional_float(
                item.get("baseline_alignment_score")
            ),
            "current_alignment_score": _optional_float(
                item.get("current_alignment_score")
            ),
            "alignment_improvement": _optional_float(
                item.get("alignment_improvement")
            ),
            "verified_resume_ready": bool(item.get("verified_resume_ready")),
            "details": details if isinstance(details, dict) else {},
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }

    def delete(self, owner_id: str, application_id: str) -> bool:
        application_item = self._get_item(owner_id, _application_key(application_id))
        if application_item is None:
            return False
        artifact_items = [
            self._get_item(owner_id, storage_key)
            for storage_key in (
                _resume_findings_key(application_id),
                _interview_preparation_key(application_id),
                _impact_key(application_id),
            )
        ]
        response = self._table().delete_item(
            Key=self._key(owner_id, _application_key(application_id)),
            ReturnValues="ALL_OLD",
        )
        if not response.get("Attributes"):
            return False
        # DynamoDB has no foreign keys, so explicitly cascade deletion of
        # CASCADE behavior for linked artifact records. S3 deletions create delete
        # markers when bucket versioning is enabled, allowing operator recovery.
        for storage_key in (
            _resume_findings_key(application_id),
            _interview_preparation_key(application_id),
            _impact_key(application_id),
        ):
            self._table().delete_item(Key=self._key(owner_id, storage_key))
        source_job_id = str(application_item.get("source_job_id") or "")
        if source_job_id:
            self._table().delete_item(
                Key=self._key(owner_id, _source_job_key(source_job_id))
            )
        self._delete_object_keys(application_item, *artifact_items)
        return True
def create_dynamodb_workflow_store(
    *,
    config: Mapping[str, Any],
    state_factory: Callable[[], WorkflowState],
    document_store: CareerBridgeObjectStore | None = None,
) -> DynamoDBWorkflowStore:
    """Factory loaded by ``resume_tailor.storage.create_workflow_store``."""

    return DynamoDBWorkflowStore(
        config,
        state_factory,
        document_store=document_store,
    )


def create_dynamodb_application_store(
    *,
    config: Mapping[str, Any],
    document_store: CareerBridgeObjectStore | None = None,
) -> DynamoDBApplicationStore:
    """Factory loaded by ``resume_tailor.storage.create_application_store``."""

    return DynamoDBApplicationStore(
        config,
        table=config.get("CAREER_BRIDGE_APPLICATIONS_TABLE_RESOURCE"),
        document_store=document_store,
    )
