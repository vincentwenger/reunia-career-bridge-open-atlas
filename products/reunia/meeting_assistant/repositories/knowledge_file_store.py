from __future__ import annotations

from pathlib import Path
from typing import Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from meeting_assistant.utils.exceptions import DatabaseError, ResourceNotFoundError


class KnowledgeFileStore(Protocol):
    def put(self, object_key: str, content: bytes, content_type: str) -> None: ...

    def get(self, object_key: str) -> bytes: ...

    def delete(self, object_key: str) -> None: ...


class LocalKnowledgeFileStore:
    def __init__(self, root_directory: str | Path) -> None:
        self._root = Path(root_directory).expanduser().resolve()

    def put(self, object_key: str, content: bytes, content_type: str) -> None:
        path = self._resolve(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_bytes(content)
        temporary_path.replace(path)

    def get(self, object_key: str) -> bytes:
        path = self._resolve(object_key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ResourceNotFoundError("The stored document could not be found.") from exc
        except OSError as exc:
            raise DatabaseError("The document could not be read from storage.") from exc

    def delete(self, object_key: str) -> None:
        path = self._resolve(object_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise DatabaseError("The document could not be removed from storage.") from exc
        self._remove_empty_parents(path.parent)

    def _resolve(self, object_key: str) -> Path:
        candidate = (self._root / object_key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise DatabaseError("Invalid document storage path.") from exc
        return candidate

    def _remove_empty_parents(self, directory: Path) -> None:
        while directory != self._root:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent


class S3KnowledgeFileStore:
    def __init__(
        self,
        bucket: str,
        region_name: str,
        *,
        access_key_id: str = "",
        secret_access_key: str = "",
        session_token: str = "",
    ) -> None:
        self._bucket = bucket
        client_options: dict[str, str] = {"region_name": region_name}
        if access_key_id and secret_access_key:
            client_options["aws_access_key_id"] = access_key_id
            client_options["aws_secret_access_key"] = secret_access_key
            if session_token:
                client_options["aws_session_token"] = session_token
        self._client = boto3.client("s3", **client_options)

    def put(self, object_key: str, content: bytes, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=content,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        except (BotoCoreError, ClientError) as exc:
            raise DatabaseError("The document could not be saved to file storage.") from exc

    def get(self, object_key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=object_key)
            return response["Body"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise ResourceNotFoundError("The stored document could not be found.") from exc
            raise DatabaseError("The document could not be read from file storage.") from exc
        except BotoCoreError as exc:
            raise DatabaseError("The document could not be read from file storage.") from exc

    def delete(self, object_key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=object_key)
        except (BotoCoreError, ClientError) as exc:
            raise DatabaseError("The document could not be removed from file storage.") from exc
