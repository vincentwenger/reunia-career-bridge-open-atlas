"""Durable queue for long-running Career Bridge AI work.

Web requests create small job records and return immediately. A separately
managed worker claims queued records, renews a lease while processing, and
persists progress after each bounded unit of work. The DynamoDB adapter can
share the Job Discovery table because it uses the same ``owner_id`` /
``storage_key`` key schema and a reserved queue partition.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, runtime_checkable


ASYNC_JOBS_BACKEND_KEY = "CAREER_BRIDGE_ASYNC_JOB_STORAGE_BACKEND"
ASYNC_JOBS_TABLE_KEY = "CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME"
ASYNC_JOBS_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
ASYNC_JOB_MAX_PAYLOAD_BYTES = 320 * 1024
_QUEUE_OWNER_ID = "__CAREER_BRIDGE_ASYNC_QUEUE__"
_JOB_PREFIX = "ASYNC#JOB#"
_QUEUE_PREFIX = "ASYNC#QUEUED#"
_WORKER_HEARTBEAT_KEY = "ASYNC#WORKER#HEARTBEAT"
ASYNC_WORKER_HEARTBEAT_TTL_SECONDS = 24 * 60 * 60


class AsyncJobType(str, Enum):
    JOB_DISCOVERY_ASSESSMENT = "job_discovery_assessment"
    INTERVIEW_PREPARATION = "interview_preparation"
    RESUME_BASELINE_TRANSLATION = "resume_baseline_translation"
    RESUME_TAILORING = "resume_tailoring"
    RESUME_REPORT = "resume_report"
    RESUME_FINAL_OPTIMIZATION = "resume_final_optimization"
    RESUME_EXPORT = "resume_export"


class AsyncJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in {
            AsyncJobStatus.COMPLETED,
            AsyncJobStatus.COMPLETED_WITH_ERRORS,
            AsyncJobStatus.FAILED,
            AsyncJobStatus.CANCELED,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_timestamp(value: str | datetime | None) -> str:
    if value in (None, ""):
        return ""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class AsyncJob:
    id: str
    owner_id: str
    job_type: AsyncJobType | str
    payload: dict[str, Any] = field(default_factory=dict)
    status: AsyncJobStatus | str = AsyncJobStatus.QUEUED
    total_count: int = 0
    attempted_count: int = 0
    completed_count: int = 0
    failed_items: tuple[dict[str, str], ...] = ()
    message: str = "Queued for background processing."
    cancel_requested: bool = False
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    started_at: str = ""
    completed_at: str = ""
    lease_owner: str = ""
    lease_expires_at: str = ""
    revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id or "").strip())
        object.__setattr__(self, "owner_id", str(self.owner_id or "").strip())
        if not self.id or not self.owner_id:
            raise ValueError("Async job id and owner_id are required")
        object.__setattr__(
            self,
            "job_type",
            self.job_type if isinstance(self.job_type, AsyncJobType) else AsyncJobType(str(self.job_type)),
        )
        object.__setattr__(
            self,
            "status",
            self.status if isinstance(self.status, AsyncJobStatus) else AsyncJobStatus(str(self.status)),
        )
        normalized_payload = json.loads(json.dumps(dict(self.payload or {}), default=str))
        payload_size = len(
            json.dumps(normalized_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if payload_size > ASYNC_JOB_MAX_PAYLOAD_BYTES:
            raise ValueError(
                "Async job payloads must remain compact; store large inputs in object storage."
            )
        object.__setattr__(self, "payload", normalized_payload)
        object.__setattr__(self, "total_count", max(0, int(self.total_count)))
        object.__setattr__(self, "attempted_count", max(0, int(self.attempted_count)))
        object.__setattr__(self, "completed_count", max(0, int(self.completed_count)))
        object.__setattr__(
            self,
            "failed_items",
            tuple(
                {str(key): str(value) for key, value in dict(item or {}).items()}
                for item in (self.failed_items or ())
            ),
        )
        for name in ("created_at", "updated_at", "started_at", "completed_at", "lease_expires_at"):
            object.__setattr__(self, name, _normalize_timestamp(getattr(self, name)))
        object.__setattr__(self, "lease_owner", str(self.lease_owner or "").strip())
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "revision", max(0, int(self.revision)))

    @property
    def remaining_count(self) -> int:
        return max(0, self.total_count - self.attempted_count)

    @property
    def progress_percent(self) -> int:
        if self.total_count <= 0:
            return 100 if self.status.terminal else 0
        return min(100, round((self.attempted_count / self.total_count) * 100))

    @classmethod
    def queued(
        cls,
        *,
        owner_id: str,
        job_type: AsyncJobType,
        payload: Mapping[str, Any],
        total_count: int,
        message: str = "Queued for background processing.",
    ) -> "AsyncJob":
        now = utc_now_iso()
        return cls(
            id=uuid.uuid4().hex,
            owner_id=owner_id,
            job_type=job_type,
            payload=dict(payload),
            total_count=total_count,
            message=message,
            created_at=now,
            updated_at=now,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_type": self.job_type.value,
            "status": self.status.value,
            "terminal": self.status.terminal,
            "total_count": self.total_count,
            "attempted_count": self.attempted_count,
            "completed_count": self.completed_count,
            "remaining_count": self.remaining_count,
            "failed_count": len(self.failed_items),
            "failed_items": list(self.failed_items),
            "message": self.message,
            "cancel_requested": self.cancel_requested,
            "progress_percent": self.progress_percent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class AsyncWorkerHeartbeat:
    worker_id: str
    started_at: str
    last_heartbeat_at: str
    state: str = "idle"
    processed_jobs: int = 0
    current_job_id: str = ""
    current_job_type: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "worker_id", str(self.worker_id or "").strip())
        if not self.worker_id:
            raise ValueError("Async worker heartbeat requires worker_id")
        object.__setattr__(self, "started_at", _normalize_timestamp(self.started_at))
        object.__setattr__(
            self, "last_heartbeat_at", _normalize_timestamp(self.last_heartbeat_at)
        )
        normalized_state = str(self.state or "idle").strip().casefold()
        if normalized_state not in {"idle", "working", "stopping"}:
            normalized_state = "idle"
        object.__setattr__(self, "state", normalized_state)
        object.__setattr__(self, "processed_jobs", max(0, int(self.processed_jobs)))
        object.__setattr__(self, "current_job_id", str(self.current_job_id or "").strip())
        object.__setattr__(
            self, "current_job_type", str(self.current_job_type or "").strip()
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "started_at": self.started_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "state": self.state,
            "processed_jobs": self.processed_jobs,
            "current_job_id": self.current_job_id,
            "current_job_type": self.current_job_type,
        }


def async_worker_health_payload(
    heartbeat: AsyncWorkerHeartbeat | None,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return non-secret worker freshness metadata for health checks."""

    threshold = max(15, int(max_age_seconds))
    payload: dict[str, Any] = {
        "status": "missing",
        "last_heartbeat_at": "",
        "age_seconds": None,
        "max_age_seconds": threshold,
        "worker_id": "",
        "state": "unknown",
        "processed_jobs": 0,
        "current_job_id": "",
        "current_job_type": "",
        "started_at": "",
    }
    if heartbeat is None:
        return payload

    payload.update(heartbeat.to_public_dict())
    try:
        heartbeat_at = datetime.fromisoformat(heartbeat.last_heartbeat_at)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            current = current.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((current.astimezone(timezone.utc) - heartbeat_at).total_seconds()))
    except (TypeError, ValueError):
        payload["status"] = "invalid"
        payload["age_seconds"] = None
        return payload

    payload["age_seconds"] = age_seconds
    if heartbeat.state == "stopping":
        payload["status"] = "stopping"
    else:
        payload["status"] = "healthy" if age_seconds <= threshold else "stale"
    return payload


class AsyncJobConflictError(RuntimeError):
    pass


@runtime_checkable
class AsyncJobStore(Protocol):
    def create(self, job: AsyncJob) -> AsyncJob: ...
    def get(self, owner_id: str, job_id: str) -> AsyncJob | None: ...
    def list_for_owner(self, owner_id: str, *, limit: int = 25) -> list[AsyncJob]: ...
    def find_active(self, owner_id: str, job_type: AsyncJobType) -> AsyncJob | None: ...
    def claim_next(self, worker_id: str, *, lease_seconds: int = 300) -> AsyncJob | None: ...
    def save(self, job: AsyncJob, *, expected_revision: int) -> AsyncJob: ...
    def request_cancel(self, owner_id: str, job_id: str) -> AsyncJob | None: ...
    def record_worker_heartbeat(self, heartbeat: AsyncWorkerHeartbeat) -> None: ...
    def get_worker_heartbeat(self) -> AsyncWorkerHeartbeat | None: ...


class InMemoryAsyncJobStore:
    def __init__(self, *, clock: Callable[[], str] | None = None) -> None:
        self._clock = clock or utc_now_iso
        self._jobs: dict[tuple[str, str], AsyncJob] = {}
        self._worker_heartbeat: AsyncWorkerHeartbeat | None = None
        self._lock = threading.RLock()

    def create(self, job: AsyncJob) -> AsyncJob:
        with self._lock:
            key = (job.owner_id, job.id)
            if key in self._jobs:
                raise AsyncJobConflictError(f"Async job {job.id} already exists")
            stored = replace(job, revision=1, updated_at=self._clock())
            self._jobs[key] = stored
            return stored

    def get(self, owner_id: str, job_id: str) -> AsyncJob | None:
        with self._lock:
            return self._jobs.get((owner_id, job_id))

    def list_for_owner(self, owner_id: str, *, limit: int = 25) -> list[AsyncJob]:
        with self._lock:
            jobs = [job for (owner, _), job in self._jobs.items() if owner == owner_id]
        jobs.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return jobs[: max(1, int(limit))]

    def find_active(self, owner_id: str, job_type: AsyncJobType) -> AsyncJob | None:
        return next(
            (
                job
                for job in self.list_for_owner(owner_id, limit=100)
                if job.job_type is job_type and not job.status.terminal
            ),
            None,
        )

    def claim_next(self, worker_id: str, *, lease_seconds: int = 300) -> AsyncJob | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            candidates = sorted(self._jobs.values(), key=lambda item: (item.created_at, item.id))
            for job in candidates:
                lease_expired = bool(
                    job.status is AsyncJobStatus.RUNNING
                    and job.lease_expires_at
                    and datetime.fromisoformat(job.lease_expires_at) <= now
                )
                if job.status is not AsyncJobStatus.QUEUED and not lease_expired:
                    continue
                if job.cancel_requested:
                    canceled = replace(
                        job,
                        status=AsyncJobStatus.CANCELED,
                        message="Canceled before processing started.",
                        completed_at=self._clock(),
                        updated_at=self._clock(),
                        revision=job.revision + 1,
                    )
                    self._jobs[(job.owner_id, job.id)] = canceled
                    continue
                claimed = replace(
                    job,
                    status=AsyncJobStatus.RUNNING,
                    started_at=job.started_at or self._clock(),
                    updated_at=self._clock(),
                    lease_owner=worker_id,
                    lease_expires_at=(now + timedelta(seconds=max(30, lease_seconds))).isoformat(timespec="seconds"),
                    message="Your resume is being prepared.",
                    revision=job.revision + 1,
                )
                self._jobs[(job.owner_id, job.id)] = claimed
                return claimed
        return None

    def save(self, job: AsyncJob, *, expected_revision: int) -> AsyncJob:
        with self._lock:
            current = self._jobs.get((job.owner_id, job.id))
            if current is None:
                raise KeyError(job.id)
            if current.revision != expected_revision:
                raise AsyncJobConflictError(
                    f"Async job {job.id} changed from revision {expected_revision} to {current.revision}"
                )
            stored = replace(job, revision=current.revision + 1, updated_at=self._clock())
            self._jobs[(job.owner_id, job.id)] = stored
            return stored

    def request_cancel(self, owner_id: str, job_id: str) -> AsyncJob | None:
        with self._lock:
            current = self._jobs.get((owner_id, job_id))
            if current is None or current.status.terminal:
                return current
            status = AsyncJobStatus.CANCELED if current.status is AsyncJobStatus.QUEUED else current.status
            message = (
                "Canceled before processing started."
                if status is AsyncJobStatus.CANCELED
                else "Cancellation requested. The current item will finish first."
            )
            stored = replace(
                current,
                cancel_requested=True,
                status=status,
                message=message,
                completed_at=self._clock() if status is AsyncJobStatus.CANCELED else current.completed_at,
                updated_at=self._clock(),
                revision=current.revision + 1,
            )
            self._jobs[(owner_id, job_id)] = stored
            return stored

    def record_worker_heartbeat(self, heartbeat: AsyncWorkerHeartbeat) -> None:
        with self._lock:
            self._worker_heartbeat = heartbeat

    def get_worker_heartbeat(self) -> AsyncWorkerHeartbeat | None:
        with self._lock:
            return self._worker_heartbeat


class DynamoDBAsyncJobStore:
    def __init__(self, config: Mapping[str, Any], *, table: Any | None = None) -> None:
        self._config = config
        self._table_override = table
        self._resolved_table: Any | None = None
        self._table_name = str(
            config.get(ASYNC_JOBS_TABLE_KEY)
            or config.get("CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME")
            or ""
        ).strip()
        if table is None and not self._table_name:
            raise RuntimeError(
                f"{ASYNC_JOBS_TABLE_KEY} or CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME is required"
            )
        self._ttl_seconds = max(
            3600,
            int(config.get("CAREER_BRIDGE_ASYNC_JOB_TTL_SECONDS") or ASYNC_JOBS_DEFAULT_TTL_SECONDS),
        )

    def _table(self):
        if self._table_override is not None:
            return self._table_override
        if self._resolved_table is None:
            import boto3
            region = str(
                self._config.get("AWS_REGION")
                or self._config.get("AWS_DEFAULT_REGION")
                or "us-west-2"
            ).strip()
            self._resolved_table = boto3.resource("dynamodb", region_name=region).Table(self._table_name)
        return self._resolved_table

    @staticmethod
    def _job_key(owner_id: str, job_id: str) -> dict[str, str]:
        return {"owner_id": owner_id, "storage_key": f"{_JOB_PREFIX}{job_id}"}

    @staticmethod
    def _queue_key(job: AsyncJob) -> dict[str, str]:
        return {
            "owner_id": _QUEUE_OWNER_ID,
            "storage_key": f"{_QUEUE_PREFIX}{job.created_at}#{job.owner_id}#{job.id}",
        }

    def create(self, job: AsyncJob) -> AsyncJob:
        stored = replace(job, revision=1, updated_at=utc_now_iso())
        item = _job_to_item(stored, ttl_seconds=self._ttl_seconds)
        self._table().put_item(
            Item=_to_dynamodb(item),
            ConditionExpression="attribute_not_exists(#storage_key)",
            ExpressionAttributeNames={"#storage_key": "storage_key"},
        )
        queue_item = {
            **self._queue_key(stored),
            "entity_type": "async_job_queue_ticket",
            "job_owner_id": stored.owner_id,
            "job_id": stored.id,
            "created_at": stored.created_at,
            "expires_at": int(time.time()) + self._ttl_seconds,
        }
        try:
            self._table().put_item(Item=_to_dynamodb(queue_item))
        except Exception:
            # Do not leave a queued job that no worker can discover if the
            # second write fails. The caller can safely retry job creation.
            self._table().delete_item(Key=self._job_key(stored.owner_id, stored.id))
            raise
        return stored

    def get(self, owner_id: str, job_id: str) -> AsyncJob | None:
        response = self._table().get_item(Key=self._job_key(owner_id, job_id), ConsistentRead=True)
        item = response.get("Item")
        return _job_from_item(_from_dynamodb(item)) if item else None

    def list_for_owner(self, owner_id: str, *, limit: int = 25) -> list[AsyncJob]:
        response = self._table().query(
            KeyConditionExpression="#owner_id = :owner_id AND begins_with(#storage_key, :prefix)",
            ExpressionAttributeNames={"#owner_id": "owner_id", "#storage_key": "storage_key"},
            ExpressionAttributeValues={":owner_id": owner_id, ":prefix": _JOB_PREFIX},
        )
        jobs = [_job_from_item(_from_dynamodb(item)) for item in response.get("Items", [])]
        jobs.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return jobs[: max(1, int(limit))]

    def find_active(self, owner_id: str, job_type: AsyncJobType) -> AsyncJob | None:
        return next(
            (
                job
                for job in self.list_for_owner(owner_id, limit=100)
                if job.job_type is job_type and not job.status.terminal
            ),
            None,
        )

    def _queue_tickets(self, *, limit: int = 20) -> list[dict[str, Any]]:
        response = self._table().query(
            KeyConditionExpression="#owner_id = :owner_id AND begins_with(#storage_key, :prefix)",
            ExpressionAttributeNames={"#owner_id": "owner_id", "#storage_key": "storage_key"},
            ExpressionAttributeValues={":owner_id": _QUEUE_OWNER_ID, ":prefix": _QUEUE_PREFIX},
            Limit=max(1, int(limit)),
        )
        return [_from_dynamodb(item) for item in response.get("Items", [])]

    def claim_next(self, worker_id: str, *, lease_seconds: int = 300) -> AsyncJob | None:
        now = datetime.now(timezone.utc)
        for ticket in self._queue_tickets():
            owner_id = str(ticket.get("job_owner_id") or "")
            job_id = str(ticket.get("job_id") or "")
            job = self.get(owner_id, job_id)
            if job is None or job.status.terminal:
                self._table().delete_item(Key={"owner_id": ticket["owner_id"], "storage_key": ticket["storage_key"]})
                continue
            if job.cancel_requested and job.status is AsyncJobStatus.QUEUED:
                self.request_cancel(owner_id, job_id)
                continue
            lease_expired = bool(
                job.status is AsyncJobStatus.RUNNING
                and job.lease_expires_at
                and datetime.fromisoformat(job.lease_expires_at) <= now
            )
            if job.status is not AsyncJobStatus.QUEUED and not lease_expired:
                continue
            claimed = replace(
                job,
                status=AsyncJobStatus.RUNNING,
                started_at=job.started_at or utc_now_iso(),
                lease_owner=worker_id,
                lease_expires_at=(now + timedelta(seconds=max(30, lease_seconds))).isoformat(timespec="seconds"),
                message="Your resume is being prepared.",
            )
            try:
                return self.save(claimed, expected_revision=job.revision)
            except AsyncJobConflictError:
                continue
        return None

    def save(self, job: AsyncJob, *, expected_revision: int) -> AsyncJob:
        stored = replace(job, revision=expected_revision + 1, updated_at=utc_now_iso())
        item = _job_to_item(stored, ttl_seconds=self._ttl_seconds)
        try:
            self._table().put_item(
                Item=_to_dynamodb(item),
                ConditionExpression="#revision = :expected_revision",
                ExpressionAttributeNames={"#revision": "revision"},
                ExpressionAttributeValues={":expected_revision": expected_revision},
            )
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
            if code == "ConditionalCheckFailedException":
                raise AsyncJobConflictError(f"Async job {job.id} was updated by another process") from exc
            raise
        if stored.status.terminal:
            self._delete_queue_tickets(stored.owner_id, stored.id)
        return stored

    def request_cancel(self, owner_id: str, job_id: str) -> AsyncJob | None:
        for _ in range(3):
            current = self.get(owner_id, job_id)
            if current is None or current.status.terminal:
                return current
            queued = current.status is AsyncJobStatus.QUEUED
            updated = replace(
                current,
                cancel_requested=True,
                status=AsyncJobStatus.CANCELED if queued else current.status,
                message=(
                    "Canceled before processing started."
                    if queued
                    else "Cancellation requested. The current item will finish first."
                ),
                completed_at=utc_now_iso() if queued else current.completed_at,
            )
            try:
                return self.save(updated, expected_revision=current.revision)
            except AsyncJobConflictError:
                continue
        raise AsyncJobConflictError(f"Could not request cancellation for {job_id}")

    def _delete_queue_tickets(self, owner_id: str, job_id: str) -> None:
        for ticket in self._queue_tickets(limit=100):
            if str(ticket.get("job_owner_id") or "") == owner_id and str(ticket.get("job_id") or "") == job_id:
                self._table().delete_item(Key={"owner_id": ticket["owner_id"], "storage_key": ticket["storage_key"]})

    def record_worker_heartbeat(self, heartbeat: AsyncWorkerHeartbeat) -> None:
        item = {
            "owner_id": _QUEUE_OWNER_ID,
            "storage_key": _WORKER_HEARTBEAT_KEY,
            "entity_type": "async_worker_heartbeat",
            **heartbeat.to_public_dict(),
            "expires_at": int(time.time()) + ASYNC_WORKER_HEARTBEAT_TTL_SECONDS,
        }
        self._table().put_item(Item=_to_dynamodb(item))

    def get_worker_heartbeat(self) -> AsyncWorkerHeartbeat | None:
        response = self._table().get_item(
            Key={"owner_id": _QUEUE_OWNER_ID, "storage_key": _WORKER_HEARTBEAT_KEY},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        values = _from_dynamodb(item)
        return AsyncWorkerHeartbeat(
            worker_id=str(values.get("worker_id") or ""),
            started_at=str(values.get("started_at") or values.get("last_heartbeat_at") or utc_now_iso()),
            last_heartbeat_at=str(values.get("last_heartbeat_at") or ""),
            state=str(values.get("state") or "idle"),
            processed_jobs=int(values.get("processed_jobs") or 0),
            current_job_id=str(values.get("current_job_id") or ""),
            current_job_type=str(values.get("current_job_type") or ""),
        )


def configured_async_job_backend(config: Mapping[str, Any]) -> str:
    return str(
        config.get(ASYNC_JOBS_BACKEND_KEY)
        or config.get("CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND")
        or "memory"
    ).strip().casefold()


def create_async_job_store(config: Mapping[str, Any], *, table: Any | None = None) -> AsyncJobStore:
    backend = configured_async_job_backend(config)
    if backend == "memory":
        return InMemoryAsyncJobStore()
    if backend == "dynamodb":
        return DynamoDBAsyncJobStore(config, table=table)
    raise RuntimeError(f"{ASYNC_JOBS_BACKEND_KEY} must be memory or dynamodb; received {backend!r}")


def _job_to_item(job: AsyncJob, *, ttl_seconds: int) -> dict[str, Any]:
    return {
        "owner_id": job.owner_id,
        "storage_key": f"{_JOB_PREFIX}{job.id}",
        "entity_type": "async_ai_job",
        "id": job.id,
        "job_type": job.job_type.value,
        "payload_json": json.dumps(job.payload, ensure_ascii=False, separators=(",", ":")),
        "status": job.status.value,
        "total_count": job.total_count,
        "attempted_count": job.attempted_count,
        "completed_count": job.completed_count,
        "failed_items": list(job.failed_items),
        "message": job.message,
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "lease_owner": job.lease_owner,
        "lease_expires_at": job.lease_expires_at,
        "revision": job.revision,
        "expires_at": int(time.time()) + ttl_seconds,
    }


def _job_from_item(item: Mapping[str, Any]) -> AsyncJob:
    try:
        payload = json.loads(str(item.get("payload_json") or "{}"))
    except (TypeError, json.JSONDecodeError):
        payload = {}
    return AsyncJob(
        id=str(item.get("id") or ""),
        owner_id=str(item.get("owner_id") or ""),
        job_type=str(item.get("job_type") or AsyncJobType.JOB_DISCOVERY_ASSESSMENT.value),
        payload=payload,
        status=str(item.get("status") or AsyncJobStatus.QUEUED.value),
        total_count=int(item.get("total_count") or 0),
        attempted_count=int(item.get("attempted_count") or 0),
        completed_count=int(item.get("completed_count") or 0),
        failed_items=tuple(item.get("failed_items") or ()),
        message=str(item.get("message") or ""),
        cancel_requested=bool(item.get("cancel_requested")),
        created_at=str(item.get("created_at") or utc_now_iso()),
        updated_at=str(item.get("updated_at") or item.get("created_at") or utc_now_iso()),
        started_at=str(item.get("started_at") or ""),
        completed_at=str(item.get("completed_at") or ""),
        lease_owner=str(item.get("lease_owner") or ""),
        lease_expires_at=str(item.get("lease_expires_at") or ""),
        revision=int(item.get("revision") or 0),
    )


def _to_dynamodb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(key): _to_dynamodb(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamodb(item) for item in value]
    return value


def _from_dynamodb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _from_dynamodb(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamodb(item) for item in value]
    return value
