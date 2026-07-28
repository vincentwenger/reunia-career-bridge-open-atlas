from __future__ import annotations

import hashlib
import io
import mimetypes
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any, Iterable
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from meeting_assistant.repositories.knowledge_file_store import KnowledgeFileStore
from meeting_assistant.repositories.knowledge_repository import KnowledgeRepository
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.exceptions import (
    DatabaseError,
    ResourceNotFoundError,
    ValidationError,
)

_ALLOWED_EXTENSIONS = {"pdf", "docx", "xlsx", "xls", "txt", "md"}
_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "txt": "text/plain",
    "md": "text/plain",
}


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository | None = None,
        file_store: KnowledgeFileStore | None = None,
        user_service: UserService | None = None,
    ) -> None:
        self.repository = repository or current_app.extensions["knowledge_repository"]
        self.file_store = file_store or current_app.extensions["knowledge_file_store"]
        self.user_service = user_service or UserService()

    def list_library(self, user_id: str) -> dict[str, list[dict[str, Any]]]:
        try:
            raw_collections = self.repository.list_collections(user_id)
            raw_files = self.repository.list_files(user_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The document library could not be loaded.") from exc

        raw_files = self._remove_expired_files(user_id, raw_files)
        collections_by_id = {
            str(item.get("collection_id")): item
            for item in raw_collections
            if item.get("collection_id")
        }
        file_counts: dict[str, int] = {}
        for item in raw_files:
            collection_id = str(item.get("collection_id") or "uncategorized")
            file_counts[collection_id] = file_counts.get(collection_id, 0) + 1

        collections = [
            self._serialize_collection(item, file_counts.get(str(item["collection_id"]), 0))
            for item in raw_collections
        ]
        collections.sort(key=lambda item: (item["name"].casefold(), item["collection_id"]))

        files = [
            self._serialize_file(
                item,
                collections_by_id.get(str(item.get("collection_id") or "")),
            )
            for item in raw_files
        ]
        files.sort(key=lambda item: (item.get("created_at", ""), item["filename"]), reverse=True)
        return {"collections": collections, "files": files}

    def list_collections(self, user_id: str) -> list[dict[str, Any]]:
        return self.list_library(user_id)["collections"]

    def list_files(self, user_id: str) -> list[dict[str, Any]]:
        return self.list_library(user_id)["files"]

    def create_collection(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        name = self._required_text(payload.get("name"), "Collection name", 80)
        description = self._optional_text(payload.get("description"), "Description", 500)

        try:
            existing = self.repository.list_collections(user_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The collection could not be created.") from exc
        if any(str(item.get("name") or "").strip().casefold() == name.casefold() for item in existing):
            raise ValidationError("A collection with this name already exists.")

        collection_id = uuid4().hex
        now = _utc_now()
        item = {
            "user_id": user_id,
            "item_id": f"collection#{collection_id}",
            "entity_type": "collection",
            "collection_id": collection_id,
            "name": name,
            "description": description,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self.repository.create_collection(item)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ValidationError("The collection could not be created. Try again.") from exc
            raise DatabaseError("The collection could not be created.") from exc
        except (BotoCoreError, OSError) as exc:
            raise DatabaseError("The collection could not be created.") from exc
        return self._serialize_collection(item, 0)

    def delete_collection(self, user_id: str, collection_id: str) -> dict[str, Any]:
        normalized_id = str(collection_id or "").strip()
        if not normalized_id or normalized_id == "uncategorized":
            raise ResourceNotFoundError("Collection not found.")

        try:
            collection = self.repository.get_collection(user_id, normalized_id)
            if not collection:
                raise ResourceNotFoundError("Collection not found.")

            has_files = any(
                str(item.get("collection_id") or "uncategorized") == normalized_id
                for item in self.repository.list_files(user_id)
            )
            if has_files:
                raise ValidationError(
                    "Delete all files in this collection before deleting the collection."
                )

            deleted = self.repository.delete_collection(user_id, normalized_id)
        except (ResourceNotFoundError, ValidationError):
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The collection could not be deleted.") from exc

        return self._serialize_collection(deleted, 0)

    def upload_files(
        self,
        user_id: str,
        uploads: Iterable[FileStorage],
        *,
        collection_id: str = "",
        tags: str | Iterable[str] = "",
        description: str = "",
    ) -> list[dict[str, Any]]:
        selected_uploads = [upload for upload in uploads if upload and upload.filename]
        if not selected_uploads:
            raise ValidationError("Choose at least one file to upload.")

        normalized_collection_id = str(collection_id or "").strip() or "uncategorized"
        collection = None
        if normalized_collection_id != "uncategorized":
            try:
                collection = self.repository.get_collection(user_id, normalized_collection_id)
            except (BotoCoreError, ClientError, OSError) as exc:
                raise DatabaseError("The selected collection could not be verified.") from exc
            if not collection:
                raise ValidationError("The selected collection does not exist.")

        normalized_tags = self._normalize_tags(tags)
        normalized_description = self._optional_text(description, "Description", 500)
        prepared = [self._prepare_upload(upload) for upload in selected_uploads]

        created_items: list[dict[str, Any]] = []
        retention_days = self._document_retention_days(user_id)
        try:
            for original_name, stored_name, extension, content_type, content in prepared:
                file_id = uuid4().hex
                object_key = self._object_key(user_id, file_id, stored_name)
                now = _utc_now()
                item = {
                    "user_id": user_id,
                    "item_id": f"file#{file_id}",
                    "entity_type": "file",
                    "file_id": file_id,
                    "filename": original_name,
                    "display_name": original_name,
                    "stored_filename": stored_name,
                    "extension": extension,
                    "content_type": content_type,
                    "collection_id": normalized_collection_id,
                    "description": normalized_description,
                    "tags": normalized_tags,
                    "size_bytes": len(content),
                    "status": "ready",
                    "object_key": object_key,
                    "created_at": now,
                    "updated_at": now,
                }
                if retention_days > 0:
                    item["retention_expires_at"] = int(
                        (datetime.now(timezone.utc) + timedelta(days=retention_days)).timestamp()
                    )
                self.file_store.put(object_key, content, content_type)
                try:
                    self.repository.create_file(item)
                except Exception:
                    self.file_store.delete(object_key)
                    raise
                created_items.append(item)
        except ValidationError:
            self._rollback_uploads(user_id, created_items)
            raise
        except ClientError as exc:
            self._rollback_uploads(user_id, created_items)
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ValidationError("A document could not be added. Try uploading it again.") from exc
            raise DatabaseError("The documents could not be saved.") from exc
        except (BotoCoreError, OSError, DatabaseError) as exc:
            self._rollback_uploads(user_id, created_items)
            if isinstance(exc, DatabaseError):
                raise
            raise DatabaseError("The documents could not be saved.") from exc
        except Exception as exc:
            self._rollback_uploads(user_id, created_items)
            raise DatabaseError("The documents could not be saved.") from exc

        return [self._serialize_file(item, collection) for item in created_items]

    def get_file(self, user_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
        item = self._get_file_item(user_id, file_id)
        content = self.file_store.get(str(item["object_key"]))
        return item, content

    def delete_file(self, user_id: str, file_id: str) -> dict[str, Any]:
        item = self._get_file_item(user_id, file_id)
        try:
            deleted = self.repository.delete_file(user_id, file_id)
        except ResourceNotFoundError:
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The document could not be deleted.") from exc

        try:
            self.file_store.delete(str(item["object_key"]))
        except DatabaseError:
            current_app.logger.exception(
                "Document metadata %s was deleted, but object %s could not be removed.",
                file_id,
                item.get("object_key"),
            )
        return self._serialize_file(deleted, None)

    def _get_file_item(self, user_id: str, file_id: str) -> dict[str, Any]:
        normalized_id = str(file_id or "").strip()
        if not normalized_id:
            raise ResourceNotFoundError("Document not found.")
        try:
            item = self.repository.get_file(user_id, normalized_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The document could not be loaded.") from exc
        if not item:
            raise ResourceNotFoundError("Document not found.")
        if self._file_is_expired(item):
            self._delete_expired_file(user_id, item)
            raise ResourceNotFoundError("Document not found.")
        return item

    def _document_retention_days(self, user_id: str) -> int:
        if current_app.testing:
            return 0
        settings = self.user_service.get_settings(user_id)
        return int(settings.get("documentRetentionDays") or 0)

    @staticmethod
    def _file_is_expired(item: dict[str, Any]) -> bool:
        try:
            expires_at = int(item.get("retention_expires_at") or 0)
        except (TypeError, ValueError):
            return False
        return expires_at > 0 and expires_at <= int(datetime.now(timezone.utc).timestamp())

    def _remove_expired_files(
        self,
        user_id: str,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        for item in items:
            if not self._file_is_expired(item):
                active.append(item)
                continue
            self._delete_expired_file(user_id, item)
        return active

    def _delete_expired_file(self, user_id: str, item: dict[str, Any]) -> None:
        try:
            self.repository.delete_file(user_id, str(item.get("file_id") or ""))
            self.file_store.delete(str(item.get("object_key") or ""))
        except Exception:
            current_app.logger.exception(
                "Could not remove expired document %s for %s.",
                item.get("file_id"),
                user_id,
            )

    def _prepare_upload(self, upload: FileStorage) -> tuple[str, str, str, str, bytes]:
        original_name = PurePath(str(upload.filename or "")).name.strip()
        stored_name = secure_filename(original_name)
        if not original_name or not stored_name or "." not in stored_name:
            raise ValidationError("Each document must have a supported file name and extension.")

        extension = stored_name.rsplit(".", 1)[1].lower()
        if extension not in _ALLOWED_EXTENSIONS:
            raise ValidationError("Choose PDF, DOCX, XLSX, XLS, TXT, or Markdown files only.")

        maximum_size = int(current_app.config["KNOWLEDGE_MAX_FILE_BYTES"])
        content = upload.stream.read(maximum_size + 1)
        if len(content) > maximum_size:
            maximum_mb = maximum_size / (1024 * 1024)
            raise ValidationError(f"Each document must be {maximum_mb:g} MB or smaller.")
        if not content:
            raise ValidationError(f"{original_name} is empty.")

        self._validate_content(extension, content, original_name)
        content_type = _CONTENT_TYPES.get(extension) or mimetypes.guess_type(stored_name)[0] or "application/octet-stream"
        return original_name, stored_name, extension, content_type, content

    @staticmethod
    def _validate_content(extension: str, content: bytes, filename: str) -> None:
        if extension == "pdf":
            if not content.startswith(b"%PDF-"):
                raise ValidationError(f"{filename} is not a valid PDF file.")
            return
        if extension == "docx":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    names = set(archive.namelist())
                    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                        raise ValidationError(f"{filename} is not a valid DOCX file.")
            except zipfile.BadZipFile as exc:
                raise ValidationError(f"{filename} is not a valid DOCX file.") from exc
            return
        if extension == "xlsx":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    names = set(archive.namelist())
                    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                        raise ValidationError(f"{filename} is not a valid XLSX file.")
            except zipfile.BadZipFile as exc:
                raise ValidationError(f"{filename} is not a valid XLSX file.") from exc
            return
        if extension == "xls":
            if not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
                raise ValidationError(f"{filename} is not a valid XLS file.")
            return

        if b"\x00" in content:
            raise ValidationError(f"{filename} does not appear to be a text file.")
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"{filename} must use UTF-8 text encoding.") from exc

    def _rollback_uploads(self, user_id: str, items: list[dict[str, Any]]) -> None:
        for item in reversed(items):
            try:
                self.repository.delete_file(user_id, str(item["file_id"]))
            except Exception:
                current_app.logger.exception("Could not roll back document metadata %s", item.get("file_id"))
            try:
                self.file_store.delete(str(item["object_key"]))
            except Exception:
                current_app.logger.exception("Could not roll back document object %s", item.get("object_key"))

    @staticmethod
    def _object_key(user_id: str, file_id: str, stored_name: str) -> str:
        owner_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
        return f"knowledge/{owner_hash}/{file_id}/{stored_name}"

    @staticmethod
    def _serialize_collection(item: dict[str, Any], file_count: int) -> dict[str, Any]:
        return {
            "collection_id": str(item.get("collection_id") or ""),
            "name": str(item.get("name") or "Untitled Collection"),
            "description": str(item.get("description") or ""),
            "file_count": int(file_count),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }

    @staticmethod
    def _serialize_file(item: dict[str, Any], collection: dict[str, Any] | None) -> dict[str, Any]:
        collection_id = str(item.get("collection_id") or "uncategorized")
        collection_name = (
            str(collection.get("name"))
            if collection
            else ("Uncategorized" if collection_id == "uncategorized" else "Deleted Collection")
        )
        size_bytes = int(item.get("size_bytes") or 0)
        return {
            "file_id": str(item.get("file_id") or ""),
            "filename": str(item.get("filename") or item.get("stored_filename") or "Document"),
            "display_name": str(item.get("display_name") or item.get("filename") or "Document"),
            "extension": str(item.get("extension") or ""),
            "content_type": str(item.get("content_type") or "application/octet-stream"),
            "collection_id": collection_id,
            "collection_name": collection_name,
            "description": str(item.get("description") or ""),
            "tags": list(item.get("tags") or []),
            "size_bytes": size_bytes,
            "size_display": _format_bytes(size_bytes),
            "status": str(item.get("status") or "ready"),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }

    @staticmethod
    def _normalize_tags(value: str | Iterable[str]) -> list[str]:
        raw_values = value.split(",") if isinstance(value, str) else list(value)
        tags: list[str] = []
        seen: set[str] = set()
        for raw in raw_values:
            tag = str(raw or "").strip()
            if not tag:
                continue
            if len(tag) > 40:
                raise ValidationError("Each tag must be 40 characters or fewer.")
            key = tag.casefold()
            if key not in seen:
                tags.append(tag)
                seen.add(key)
            if len(tags) > 20:
                raise ValidationError("Use no more than 20 tags.")
        return tags

    @staticmethod
    def _required_text(value: Any, label: str, maximum: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValidationError(f"{label} is required.")
        if len(text) > maximum:
            raise ValidationError(f"{label} must be {maximum} characters or fewer.")
        return text

    @staticmethod
    def _optional_text(value: Any, label: str, maximum: int) -> str:
        text = str(value or "").strip()
        if len(text) > maximum:
            raise ValidationError(f"{label} must be {maximum} characters or fewer.")
        return text


def _format_bytes(size_bytes: int) -> str:
    value = float(max(size_bytes, 0))
    units = ("B", "KB", "MB", "GB")
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
