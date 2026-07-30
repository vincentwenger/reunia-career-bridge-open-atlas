"""Object storage for Career Bridge documents and large snapshots.

DynamoDB remains the system of record for searchable metadata and object keys.
Document bytes and serialized artifacts are stored in a private local directory
for development or a private, versioned S3 bucket in production.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .storage import StorageBackendConfigurationError

DOCUMENT_STORAGE_BACKENDS = frozenset({"local", "s3"})
DOCUMENT_STORAGE_BACKEND_CONFIG_KEY = "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND"
DOCUMENT_BUCKET_CONFIG_KEY = "CAREER_BRIDGE_DOCUMENTS_BUCKET"
DOCUMENT_PREFIX_CONFIG_KEY = "CAREER_BRIDGE_DOCUMENTS_PREFIX"
DOCUMENT_LOCAL_PATH_CONFIG_KEY = "CAREER_BRIDGE_DOCUMENTS_LOCAL_PATH"


class ObjectStorageError(RuntimeError):
    """Raised when a Career Bridge object cannot be stored or retrieved."""


class ObjectNotFoundError(ObjectStorageError):
    """Raised when referenced object storage content no longer exists."""


@runtime_checkable
class CareerBridgeObjectStore(Protocol):
    def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None: ...

    def get(self, object_key: str) -> bytes: ...

    def delete(self, object_key: str) -> None: ...


class LocalCareerBridgeObjectStore:
    """Filesystem implementation used only for local development and tests."""

    def __init__(self, root_directory: str | Path) -> None:
        self._root = Path(root_directory).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        del content_type, metadata
        path = self._resolve(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        except OSError as exc:
            raise ObjectStorageError("The Career Bridge document could not be saved.") from exc

    def get(self, object_key: str) -> bytes:
        path = self._resolve(object_key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError("The Career Bridge document could not be found.") from exc
        except OSError as exc:
            raise ObjectStorageError("The Career Bridge document could not be read.") from exc

    def delete(self, object_key: str) -> None:
        if not object_key:
            return
        path = self._resolve(object_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ObjectStorageError("The Career Bridge document could not be removed.") from exc
        parent = path.parent
        while parent != self._root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _resolve(self, object_key: str) -> Path:
        candidate = (self._root / object_key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ObjectStorageError("Invalid Career Bridge object key.") from exc
        return candidate


class S3CareerBridgeObjectStore:
    """Private S3 implementation with server-side encryption enabled."""

    def __init__(
        self,
        bucket: str,
        region_name: str,
        *,
        client: Any | None = None,
        kms_key_id: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        session_token: str = "",
    ) -> None:
        self.bucket = bucket.strip()
        if not self.bucket:
            raise StorageBackendConfigurationError(
                f"{DOCUMENT_BUCKET_CONFIG_KEY} is required when "
                f"{DOCUMENT_STORAGE_BACKEND_CONFIG_KEY}=s3."
            )
        self._kms_key_id = kms_key_id.strip()
        if client is not None:
            self._client = client
            return
        options: dict[str, Any] = {"region_name": region_name}
        if access_key_id and secret_access_key:
            options.update(
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
            )
            if session_token:
                options["aws_session_token"] = session_token
        self._client = boto3.client("s3", **options)

    def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        encryption: dict[str, str] = {"ServerSideEncryption": "AES256"}
        if self._kms_key_id:
            encryption = {
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": self._kms_key_id,
            }
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=content,
                ContentType=content_type,
                Metadata={str(key): str(value) for key, value in (metadata or {}).items()},
                **encryption,
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError("The Career Bridge document could not be saved to S3.") from exc

    def get(self, object_key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=object_key)
            return response["Body"].read()
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code") or "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise ObjectNotFoundError("The Career Bridge document could not be found in S3.") from exc
            raise ObjectStorageError("The Career Bridge document could not be read from S3.") from exc
        except BotoCoreError as exc:
            raise ObjectStorageError("The Career Bridge document could not be read from S3.") from exc

    def delete(self, object_key: str) -> None:
        if not object_key:
            return
        try:
            self._client.delete_object(Bucket=self.bucket, Key=object_key)
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError("The Career Bridge document could not be removed from S3.") from exc


def configured_document_backend(config: Mapping[str, Any]) -> str:
    backend = str(config.get(DOCUMENT_STORAGE_BACKEND_CONFIG_KEY, "local") or "local")
    backend = backend.strip().casefold()
    if backend not in DOCUMENT_STORAGE_BACKENDS:
        choices = "|".join(sorted(DOCUMENT_STORAGE_BACKENDS))
        raise StorageBackendConfigurationError(
            f"{DOCUMENT_STORAGE_BACKEND_CONFIG_KEY} must be one of {choices}; "
            f"received {backend!r}."
        )
    return backend


def create_document_store(
    config: Mapping[str, Any],
    *,
    require_s3: bool = False,
) -> CareerBridgeObjectStore:
    backend = configured_document_backend(config)
    if require_s3 and backend != "s3":
        raise StorageBackendConfigurationError(
            "DynamoDB Career Bridge storage requires "
            f"{DOCUMENT_STORAGE_BACKEND_CONFIG_KEY}=s3 so document bytes are not "
            "written into DynamoDB or ephemeral container storage."
        )
    if backend == "local":
        root = str(config.get(DOCUMENT_LOCAL_PATH_CONFIG_KEY) or "").strip()
        if not root:
            raise StorageBackendConfigurationError(
                f"{DOCUMENT_LOCAL_PATH_CONFIG_KEY} is required when "
                f"{DOCUMENT_STORAGE_BACKEND_CONFIG_KEY}=local."
            )
        return LocalCareerBridgeObjectStore(root)

    region = str(
        config.get("AWS_REGION") or config.get("AWS_DEFAULT_REGION") or "us-west-2"
    ).strip()
    return S3CareerBridgeObjectStore(
        str(config.get(DOCUMENT_BUCKET_CONFIG_KEY) or ""),
        region,
        kms_key_id=str(config.get("CAREER_BRIDGE_DOCUMENTS_KMS_KEY_ID") or ""),
        access_key_id=str(config.get("CAREER_BRIDGE_S3_ACCESS_KEY_ID") or ""),
        secret_access_key=str(config.get("CAREER_BRIDGE_S3_SECRET_ACCESS_KEY") or ""),
        session_token=str(config.get("CAREER_BRIDGE_S3_SESSION_TOKEN") or ""),
    )


def object_prefix(config: Mapping[str, Any]) -> str:
    return str(config.get(DOCUMENT_PREFIX_CONFIG_KEY) or "career-bridge").strip("/") or "career-bridge"


def owner_namespace(owner_id: str) -> str:
    """Return a stable non-PII S3 path component for an application owner."""

    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:32]


def safe_object_name(filename: str, fallback: str) -> str:
    name = Path(filename or fallback).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return name or fallback


def application_object_key(
    config: Mapping[str, Any],
    owner_id: str,
    application_id: str,
    filename: str,
    *,
    category: str = "documents",
    fingerprint: str = "",
) -> str:
    category_name = safe_object_name(category, "documents")
    digest = (fingerprint or "current")[:24]
    return (
        f"{object_prefix(config)}/users/{owner_namespace(owner_id)}/applications/"
        f"{application_id}/{category_name}/{digest}/"
        f"{safe_object_name(filename, 'document.bin')}"
    )


def workflow_object_key(
    config: Mapping[str, Any],
    owner_id: str,
    workflow_key: str,
    category: str,
    filename: str,
    fingerprint: str,
) -> str:
    workflow_namespace = hashlib.sha256(workflow_key.encode("utf-8")).hexdigest()[:24]
    category_name = safe_object_name(category, "documents")
    digest = (fingerprint or "unknown")[:24]
    return (
        f"{object_prefix(config)}/users/{owner_namespace(owner_id)}/workflows/"
        f"{workflow_namespace}/{category_name}/{digest}/"
        f"{safe_object_name(filename, 'document.bin')}"
    )


def workflow_state_object_key(
    config: Mapping[str, Any],
    workflow_key: str,
    version: int,
    fingerprint: str,
    *,
    retention_class: str = "application",
) -> str:
    """Return a private, immutable object key for one workflow-state version.

    Scratch and application workflow-state documents use separate top-level
    prefixes so an S3 lifecycle rule can clean orphaned scratch objects after
    DynamoDB TTL removes their metadata without affecting retained application
    workflows.
    """

    owner_id, marker, _ = workflow_key.partition(":application:")
    if not marker:
        owner_id = workflow_key
    workflow_namespace = hashlib.sha256(workflow_key.encode("utf-8")).hexdigest()[:24]
    digest = (fingerprint or "unknown")[:24]
    safe_retention_class = (
        "scratch" if str(retention_class).strip().casefold() == "scratch" else "application"
    )
    return (
        f"{object_prefix(config)}/workflow-state/{safe_retention_class}/users/"
        f"{owner_namespace(owner_id)}/{workflow_namespace}/{digest}/"
        f"workflow-state-v{max(0, int(version))}.json"
    )
