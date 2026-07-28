from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError


class RecorderJobStore(Protocol):
    def create(self, job_id: str) -> None: ...
    def read(self, job_id: str) -> dict[str, Any]: ...
    def write(self, job: dict[str, Any]) -> None: ...
    def remove(self, job_id: str) -> None: ...
    def persist_source(self, job_id: str, source: dict[str, Any]) -> dict[str, Any]: ...
    def materialize_sources(self, job: dict[str, Any], directory: Path) -> list[dict[str, Any]]: ...
    def delete_sources(self, job: dict[str, Any]) -> None: ...
    def cleanup(self, retention_seconds: int) -> None: ...
    def list_recoverable_jobs(self) -> list[str]: ...


class LocalRecorderJobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, job_id: str) -> None:
        self._job_dir(job_id).mkdir(parents=True, exist_ok=False)

    def read(self, job_id: str) -> dict[str, Any]:
        return json.loads(self._job_file(job_id).read_text(encoding="utf-8"))

    def write(self, job: dict[str, Any]) -> None:
        path = self._job_file(str(job["job_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def remove(self, job_id: str) -> None:
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    def persist_source(self, job_id: str, source: dict[str, Any]) -> dict[str, Any]:
        return dict(source)

    def materialize_sources(self, job: dict[str, Any], directory: Path) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in job.get("sources") or []:
            materialized = dict(item)
            materialized["path"] = Path(str(item["path"]))
            results.append(materialized)
        return results

    def delete_sources(self, job: dict[str, Any]) -> None:
        for item in job.get("sources") or []:
            path = Path(str(item.get("path") or ""))
            if path:
                path.unlink(missing_ok=True)

    def cleanup(self, retention_seconds: int) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=int(retention_seconds))
        if not self.root.exists():
            return
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            try:
                modified = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if modified < cutoff:
                shutil.rmtree(child, ignore_errors=True)

    def list_recoverable_jobs(self) -> list[str]:
        if not self.root.exists():
            return []
        job_ids: list[str] = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            try:
                job = self.read(child.name)
            except (OSError, ValueError):
                continue
            if str(job.get("status") or "") in {"queued", "processing"}:
                job_ids.append(child.name)
        return sorted(job_ids)

    def _job_dir(self, job_id: str) -> Path:
        return self.root / str(job_id)

    def _job_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"


class S3RecorderJobStore:
    """Durable recorder job metadata and audio storage for production."""

    def __init__(
        self,
        bucket: str,
        region: str,
        *,
        prefix: str = "recorder-jobs",
        access_key_id: str = "",
        secret_access_key: str = "",
        session_token: str = "",
    ) -> None:
        kwargs: dict[str, Any] = {"region_name": region}
        if access_key_id and secret_access_key:
            kwargs.update(
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
            )
            if session_token:
                kwargs["aws_session_token"] = session_token
        self._s3 = boto3.client("s3", **kwargs)
        self.bucket = bucket
        self.prefix = prefix.strip("/") or "recorder-jobs"

    def create(self, job_id: str) -> None:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=self._job_key(job_id))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return
            raise
        raise FileExistsError(f"Recorder job {job_id} already exists.")

    def read(self, job_id: str) -> dict[str, Any]:
        response = self._s3.get_object(Bucket=self.bucket, Key=self._job_key(job_id))
        return json.loads(response["Body"].read().decode("utf-8"))

    def write(self, job: dict[str, Any]) -> None:
        self._s3.put_object(
            Bucket=self.bucket,
            Key=self._job_key(str(job["job_id"])),
            Body=json.dumps(job, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )

    def remove(self, job_id: str) -> None:
        self._delete_prefix(self._job_prefix(job_id))

    def persist_source(self, job_id: str, source: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(source["path"]))
        object_key = f"{self._job_prefix(job_id)}audio/{path.name}"
        extra: dict[str, Any] = {
            "ContentType": str(source.get("mime_type") or "application/octet-stream"),
            "ServerSideEncryption": "AES256",
        }
        self._s3.upload_file(str(path), self.bucket, object_key, ExtraArgs=extra)
        updated = dict(source)
        updated["object_key"] = object_key
        updated.pop("path", None)
        path.unlink(missing_ok=True)
        return updated

    def materialize_sources(self, job: dict[str, Any], directory: Path) -> list[dict[str, Any]]:
        directory.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, Any]] = []
        for index, item in enumerate(job.get("sources") or []):
            object_key = str(item.get("object_key") or "")
            if not object_key:
                raise FileNotFoundError("Recorder source object is missing.")
            suffix = Path(str(item.get("filename") or "audio.webm")).suffix or ".webm"
            sequence = int(item["sequence"]) if item.get("sequence") is not None else index
            source = str(item.get("source") or "audio").lower()
            path = directory / f"{index:03d}-{source}-{sequence:04d}{suffix}"
            self._s3.download_file(self.bucket, object_key, str(path))
            materialized = dict(item)
            materialized["path"] = path
            results.append(materialized)
        return results

    def delete_sources(self, job: dict[str, Any]) -> None:
        objects = [
            {"Key": str(item.get("object_key"))}
            for item in job.get("sources") or []
            if item.get("object_key")
        ]
        if objects:
            self._s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objects, "Quiet": True})

    def cleanup(self, retention_seconds: int) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=int(retention_seconds))
        paginator = self._s3.get_paginator("list_objects_v2")
        job_ids: dict[str, datetime] = {}
        root = self.prefix + "/"
        for page in paginator.paginate(Bucket=self.bucket, Prefix=root):
            for item in page.get("Contents", []):
                key = str(item.get("Key") or "")
                remainder = key[len(root):]
                job_id = remainder.split("/", 1)[0]
                if job_id:
                    modified = item.get("LastModified")
                    if modified and (job_id not in job_ids or modified > job_ids[job_id]):
                        job_ids[job_id] = modified
        for job_id, modified in job_ids.items():
            if modified < cutoff:
                self.remove(job_id)

    def list_recoverable_jobs(self) -> list[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        root = self.prefix + "/"
        job_ids: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=root):
            for item in page.get("Contents", []):
                key = str(item.get("Key") or "")
                if not key.endswith("/job.json"):
                    continue
                remainder = key[len(root):]
                job_id = remainder.split("/", 1)[0]
                if not job_id:
                    continue
                try:
                    job = self.read(job_id)
                except (ClientError, ValueError, UnicodeDecodeError):
                    continue
                if str(job.get("status") or "") in {"queued", "processing"}:
                    job_ids.append(job_id)
        return sorted(set(job_ids))

    def _job_prefix(self, job_id: str) -> str:
        return f"{self.prefix}/{job_id}/"

    def _job_key(self, job_id: str) -> str:
        return f"{self._job_prefix(job_id)}job.json"

    def _delete_prefix(self, prefix: str) -> None:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                self._s3.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )
