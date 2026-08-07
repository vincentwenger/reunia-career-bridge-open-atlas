from __future__ import annotations

import hashlib
import io
import mimetypes
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any, Iterable
from uuid import uuid4

from career_bridge.career_role_dates import career_role_date_sort_key
from career_bridge.reusable_evidence import (
    evidence_answer_key,
    find_best_evidence_match,
    normalize_evidence_text,
)

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
            list_evidence_answers = getattr(self.repository, "list_evidence_answers", None)
            raw_evidence_answers = (
                list_evidence_answers(user_id) if callable(list_evidence_answers) else []
            )
            list_career_roles = getattr(self.repository, "list_career_roles", None)
            raw_career_roles = (
                list_career_roles(user_id) if callable(list_career_roles) else []
            )
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
        evidence_answers = [self._serialize_evidence_answer(item) for item in raw_evidence_answers]
        evidence_answers.sort(
            key=lambda item: (item.get("updated_at", ""), item.get("question", "")),
            reverse=True,
        )
        career_roles = [self._serialize_career_role(item) for item in raw_career_roles]
        career_roles.sort(key=career_role_date_sort_key)
        return {
            "collections": collections,
            "files": files,
            "evidence_answers": evidence_answers,
            "career_roles": career_roles,
        }

    def list_collections(self, user_id: str) -> list[dict[str, Any]]:
        return self.list_library(user_id)["collections"]

    def list_files(self, user_id: str) -> list[dict[str, Any]]:
        return self.list_library(user_id)["files"]

    def list_evidence_answers(self, user_id: str) -> list[dict[str, Any]]:
        try:
            items = self.repository.list_evidence_answers(user_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("Reusable confirmation answers could not be loaded.") from exc
        serialized = [self._serialize_evidence_answer(item) for item in items]
        serialized.sort(
            key=lambda item: (item.get("updated_at", ""), item.get("question", "")),
            reverse=True,
        )
        return serialized

    def list_career_roles(self, user_id: str) -> list[dict[str, Any]]:
        list_career_roles = getattr(self.repository, "list_career_roles", None)
        if not callable(list_career_roles):
            return []
        try:
            items = list_career_roles(user_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("Employment roles could not be loaded.") from exc
        serialized = [self._serialize_career_role(item) for item in items]
        serialized.sort(key=career_role_date_sort_key)
        return serialized

    def sync_career_roles_from_baseline(
        self,
        user_id: str,
        entries: Iterable[dict[str, Any]],
        *,
        source_fingerprint: str = "",
        target_market: str = "",
    ) -> list[dict[str, Any]]:
        """Upsert structured employment roles extracted from the Baseline Resume.

        Existing user-confirmed interpretations are preserved while unchanged.
        New or materially changed source roles return to a review state.
        """

        upsert_role = getattr(self.repository, "upsert_career_role", None)
        list_roles = getattr(self.repository, "list_career_roles", None)
        if not callable(upsert_role) or not callable(list_roles):
            return []
        try:
            existing_items = list_roles(user_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("Existing employment roles could not be loaded.") from exc
        existing_by_id = {
            str(item.get("role_id") or ""): dict(item)
            for item in existing_items
            if item.get("role_id")
        }
        now = _utc_now()
        seen: set[str] = set()
        saved: list[dict[str, Any]] = []
        for raw in entries:
            entry = dict(raw or {})
            source_experience_id = self._optional_text(
                entry.get("source_experience_id") or entry.get("id"),
                "Source experience ID",
                160,
            )
            official_title = self._required_text(
                entry.get("official_title") or entry.get("title"),
                "Official job title",
                240,
            )
            employer = self._required_text(entry.get("employer"), "Employer", 240)
            dates = self._optional_text(entry.get("dates"), "Employment dates", 160)
            location = self._optional_text(entry.get("location"), "Location", 240)
            responsibilities = self._optional_text(
                entry.get("responsibilities"), "Responsibilities", 10000
            )
            role_id = self._career_role_id(
                source_experience_id, employer, official_title, dates
            )
            seen.add(role_id)
            current = existing_by_id.get(role_id, {})
            core_fingerprint = self._career_role_core_fingerprint(
                official_title=official_title,
                employer=employer,
                dates=dates,
                location=location,
                responsibilities=responsibilities,
            )
            source_changed = bool(
                current and str(current.get("core_fingerprint") or "") != core_fingerprint
            )
            if current and not source_changed:
                official_title = str(current.get("official_title") or official_title).strip()
                employer = str(current.get("employer") or employer).strip()
                dates = str(current.get("dates") or dates).strip()
                location = str(current.get("location") or location).strip()
                responsibilities = str(
                    current.get("responsibilities") or responsibilities
                ).strip()
            previous_official = str(current.get("official_title") or "").strip()
            previous_target = str(current.get("target_market_title") or "").strip()
            target_market_title = previous_target or official_title
            if source_changed and previous_target == previous_official:
                target_market_title = official_title
            status = str(current.get("status") or "needs_review")
            if status not in {"needs_review", "confirmed", "needs_explanation"}:
                status = "needs_review"
            if not current or source_changed:
                status = "needs_review"
            item = {
                "user_id": user_id,
                "item_id": f"career_role#{role_id}",
                "entity_type": "career_employment_role",
                "role_id": role_id,
                "source_experience_id": source_experience_id or role_id,
                "official_title": official_title,
                "employer": employer,
                "dates": dates,
                "location": location,
                "responsibilities": responsibilities,
                "target_market_title": target_market_title,
                "recruiter_explanation": str(
                    current.get("recruiter_explanation") or ""
                ).strip(),
                "status": status,
                "source_type": "baseline_resume",
                "source_active": True,
                "source_fingerprint": str(source_fingerprint or "").strip(),
                "core_fingerprint": core_fingerprint,
                "target_market": str(target_market or current.get("target_market") or "").strip(),
                "created_at": str(current.get("created_at") or now),
                "updated_at": now if source_changed or not current else str(current.get("updated_at") or now),
                "confirmed_at": (
                    "" if source_changed else str(current.get("confirmed_at") or "")
                ),
            }
            try:
                upsert_role(item)
            except (BotoCoreError, ClientError, OSError) as exc:
                raise DatabaseError("An employment role could not be saved.") from exc
            saved.append(self._serialize_career_role(item))

        # Preserve user edits for roles removed from a later Baseline Resume, but
        # make them ineligible for automatic reuse until the source is restored.
        for role_id, current in existing_by_id.items():
            if role_id in seen or not bool(current.get("source_active", True)):
                continue
            current["source_active"] = False
            current["updated_at"] = now
            try:
                upsert_role(current)
            except (BotoCoreError, ClientError, OSError):
                current_app.logger.exception(
                    "Could not mark removed Baseline Resume role %s inactive", role_id
                )
        return saved

    def update_career_role(
        self,
        user_id: str,
        role_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_id = str(role_id or "").strip()
        get_role = getattr(self.repository, "get_career_role", None)
        upsert_role = getattr(self.repository, "upsert_career_role", None)
        if not normalized_id or not callable(get_role) or not callable(upsert_role):
            raise ResourceNotFoundError("Employment role not found.")
        try:
            current = get_role(user_id, normalized_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The employment role could not be loaded.") from exc
        if not current:
            raise ResourceNotFoundError("Employment role not found.")
        official_title = self._required_text(
            payload.get("official_title", current.get("official_title")),
            "Official job title",
            240,
        )
        employer = self._required_text(
            payload.get("employer", current.get("employer")), "Employer", 240
        )
        dates = self._optional_text(
            payload.get("dates", current.get("dates")), "Employment dates", 160
        )
        location = self._optional_text(
            payload.get("location", current.get("location")), "Location", 240
        )
        responsibilities = self._optional_text(
            payload.get("responsibilities", current.get("responsibilities")),
            "Responsibilities",
            10000,
        )
        target_market_title = self._required_text(
            payload.get("target_market_title", current.get("target_market_title")),
            "Target-market title",
            240,
        )
        recruiter_explanation = self._optional_text(
            payload.get("recruiter_explanation", current.get("recruiter_explanation")),
            "Recruiter explanation",
            2000,
        )
        status = str(payload.get("status", current.get("status") or "needs_review")).strip()
        if status not in {"needs_review", "confirmed", "needs_explanation"}:
            raise ValidationError("Select a valid role-review status.")
        now = _utc_now()
        updated = dict(current)
        updated.update(
            {
                "official_title": official_title,
                "employer": employer,
                "dates": dates,
                "location": location,
                "responsibilities": responsibilities,
                "target_market_title": target_market_title,
                "recruiter_explanation": recruiter_explanation,
                "status": status,
                "core_fingerprint": self._career_role_core_fingerprint(
                    official_title=official_title,
                    employer=employer,
                    dates=dates,
                    location=location,
                    responsibilities=responsibilities,
                ),
                "updated_at": now,
                "confirmed_at": now if status == "confirmed" else "",
            }
        )
        try:
            upsert_role(updated)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The employment role could not be updated.") from exc
        return self._serialize_career_role(updated)

    def delete_career_role(self, user_id: str, role_id: str) -> dict[str, Any]:
        normalized_id = str(role_id or "").strip()
        delete_role = getattr(self.repository, "delete_career_role", None)
        if not normalized_id or not callable(delete_role):
            raise ResourceNotFoundError("Employment role not found.")
        try:
            deleted = delete_role(user_id, normalized_id)
        except ResourceNotFoundError:
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The employment role could not be removed.") from exc
        return self._serialize_career_role(deleted)

    def create_manual_evidence_answer(
        self,
        user_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Create user-confirmed evidence directly in Career Evidence Library.

        Manual records use the same durable repository and semantic matching model
        as answers saved from Confirm Relevant Experience. Records marked
        ``needs_review`` remain visible and editable but are excluded from automatic
        reuse until the user confirms them.
        """

        title = self._required_text(
            payload.get("evidence_title") or payload.get("question"),
            "Evidence title",
            240,
        )
        statement = self._required_text(
            payload.get("confirmed_statement") or payload.get("answer_text"),
            "Confirmed statement",
            4000,
        )
        confirmation_status = str(
            payload.get("confirmation_status") or "confirmed"
        ).strip().lower()
        if confirmation_status not in {"confirmed", "needs_review"}:
            raise ValidationError(
                "Confirmation status must be Confirmed or Needs review."
            )

        employer = self._optional_text(payload.get("experience_employer"), "Employer", 200)
        role = self._optional_text(payload.get("experience_title"), "Role", 200)
        dates = self._optional_text(payload.get("experience_dates"), "Dates", 160)
        supported_skills = self._optional_text(
            payload.get("supported_skills"), "Supported skills", 1200
        )
        source_note = self._optional_text(payload.get("source_note"), "Source", 600)
        limitations = self._optional_text(
            payload.get("evidence_limitations"), "Evidence limitations", 2000
        )
        experience_label = self._optional_text(
            payload.get("experience_label"), "Experience", 300
        ) or self._experience_label(employer, role, dates)

        now = _utc_now()
        evidence_id = uuid4().hex
        item = {
            "user_id": user_id,
            "item_id": f"evidence_answer#{evidence_id}",
            "entity_type": "career_evidence_answer",
            "evidence_id": evidence_id,
            "question_key": evidence_answer_key(title, supported_skills),
            "question": title,
            "normalized_question": normalize_evidence_text(title),
            "requirement": supported_skills,
            "normalized_requirement": normalize_evidence_text(supported_skills),
            "answer_type": "long_text",
            "yes_no": True if confirmation_status == "confirmed" else None,
            "answer_text": statement,
            "experience_id": self._optional_text(
                payload.get("experience_id"), "Experience ID", 160
            ),
            "experience_label": experience_label,
            "experience_employer": employer,
            "experience_title": role,
            "experience_dates": dates,
            "supported_skills": supported_skills,
            "source_note": source_note,
            "evidence_limitations": limitations,
            "confirmation_status": confirmation_status,
            "entry_method": "manual",
            "placement": "auto",
            "source_application_id": "",
            "source_job_title": "",
            "source_company": "",
            "created_at": now,
            "updated_at": now,
            "confirmed_at": now if confirmation_status == "confirmed" else "",
            "reuse_count": 0,
            "last_reused_at": "",
        }
        try:
            self.repository.upsert_evidence_answer(item)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The confirmed evidence could not be saved.") from exc
        return self._serialize_evidence_answer(item)

    def save_evidence_answers(
        self,
        user_id: str,
        entries: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prepared_entries = [dict(entry or {}) for entry in entries]
        if not prepared_entries:
            return []
        try:
            existing = self.repository.list_evidence_answers(user_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("Reusable confirmation answers could not be loaded.") from exc

        saved: list[dict[str, Any]] = []
        now = _utc_now()
        for entry in prepared_entries:
            question = self._required_text(entry.get("question"), "Question", 1200)
            requirement = self._optional_text(
                entry.get("requirement"), "Requirement", 1200
            )
            answer_type = self._required_text(
                entry.get("answer_type"), "Answer type", 40
            )
            yes_no = entry.get("yes_no")
            if yes_no not in (True, False, None):
                raise ValidationError("Answer status must be Yes, No, or not applicable.")
            answer_text = self._optional_text(
                entry.get("answer_text"), "Answer", 4000
            )
            if yes_no is not False and not answer_text:
                raise ValidationError("An affirmative or text answer requires a factual detail.")

            match, _score = find_best_evidence_match(
                question,
                requirement,
                existing,
                answer_type=answer_type,
            )
            evidence_id = (
                str(match.get("evidence_id") or "")
                if match is not None
                else evidence_answer_key(question, requirement)[:32]
            )
            if not evidence_id:
                evidence_id = uuid4().hex
            created_at = str(match.get("created_at") or now) if match else now
            item = {
                "user_id": user_id,
                "item_id": f"evidence_answer#{evidence_id}",
                "entity_type": "career_evidence_answer",
                "evidence_id": evidence_id,
                "question_key": evidence_answer_key(question, requirement),
                "question": question,
                "normalized_question": normalize_evidence_text(question),
                "requirement": requirement,
                "normalized_requirement": normalize_evidence_text(requirement),
                "answer_type": answer_type,
                "yes_no": yes_no,
                "answer_text": answer_text,
                "experience_id": self._optional_text(
                    entry.get("experience_id"), "Experience ID", 160
                ),
                "experience_label": self._optional_text(
                    entry.get("experience_label"), "Experience", 300
                ),
                "experience_employer": self._optional_text(
                    entry.get("experience_employer"), "Employer", 200
                ),
                "experience_title": self._optional_text(
                    entry.get("experience_title"), "Role", 200
                ),
                "experience_dates": str(match.get("experience_dates") or "") if match else "",
                "supported_skills": str(match.get("supported_skills") or requirement) if match else requirement,
                "source_note": str(match.get("source_note") or "") if match else "",
                "evidence_limitations": str(match.get("evidence_limitations") or "") if match else "",
                "placement": self._optional_text(
                    entry.get("placement"), "Placement", 40
                )
                or "auto",
                "source_application_id": self._optional_text(
                    entry.get("source_application_id"), "Application ID", 160
                ),
                "source_job_title": self._optional_text(
                    entry.get("source_job_title"), "Target role", 240
                ),
                "source_company": self._optional_text(
                    entry.get("source_company"), "Company", 240
                ),
                "created_at": created_at,
                "updated_at": now,
                "reuse_count": int(match.get("reuse_count") or 0) if match else 0,
                "last_reused_at": str(match.get("last_reused_at") or "") if match else "",
                "confirmation_status": "confirmed",
                "entry_method": str(match.get("entry_method") or "workflow") if match else "workflow",
                "confirmed_at": str(match.get("confirmed_at") or now) if match else now,
            }
            try:
                self.repository.upsert_evidence_answer(item)
            except (BotoCoreError, ClientError, OSError) as exc:
                raise DatabaseError("A reusable confirmation answer could not be saved.") from exc
            existing = [
                candidate
                for candidate in existing
                if str(candidate.get("evidence_id") or "") != evidence_id
            ]
            existing.append(item)
            saved.append(self._serialize_evidence_answer(item))
        return saved

    def update_evidence_answer(
        self,
        user_id: str,
        evidence_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_id = str(evidence_id or "").strip()
        if not normalized_id:
            raise ResourceNotFoundError("Reusable confirmation answer not found.")
        try:
            current = self.repository.get_evidence_answer(user_id, normalized_id)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The reusable confirmation answer could not be loaded.") from exc
        if not current:
            raise ResourceNotFoundError("Reusable confirmation answer not found.")

        question = self._required_text(
            payload.get(
                "evidence_title", payload.get("question", current.get("question"))
            ),
            "Question",
            1200,
        )
        requirement = self._optional_text(
            payload.get(
                "supported_skills",
                payload.get("requirement", current.get("requirement")),
            ),
            "Requirement",
            1200,
        )
        answer_text = self._optional_text(
            payload.get(
                "confirmed_statement",
                payload.get("answer_text", current.get("answer_text")),
            ),
            "Answer",
            4000,
        )
        is_manual = str(current.get("entry_method") or "") == "manual"
        if is_manual:
            confirmation_status = str(
                payload.get(
                    "confirmation_status",
                    current.get("confirmation_status") or "confirmed",
                )
            ).strip().lower()
            if confirmation_status not in {"confirmed", "needs_review"}:
                raise ValidationError(
                    "Confirmation status must be Confirmed or Needs review."
                )
            yes_no = True if confirmation_status == "confirmed" else None
        else:
            # Workflow-generated evidence keeps its Yes/No/Detailed-answer state.
            # Detailed edits may change the wording, role, source, skills, and
            # limitations without silently converting a negative or text answer
            # into affirmative evidence.
            confirmation_status = str(
                current.get("confirmation_status") or "confirmed"
            ).strip().lower()
            yes_no = payload.get("yes_no", current.get("yes_no"))
        if yes_no not in (True, False, None):
            raise ValidationError("Answer status must be Yes, No, or not applicable.")
        if yes_no is not False and not answer_text:
            raise ValidationError("An affirmative or text answer requires a factual detail.")

        employer = self._optional_text(
            payload.get("experience_employer", current.get("experience_employer")),
            "Employer",
            200,
        )
        role = self._optional_text(
            payload.get("experience_title", current.get("experience_title")),
            "Role",
            200,
        )
        dates = self._optional_text(
            payload.get("experience_dates", current.get("experience_dates")),
            "Dates",
            160,
        )
        experience_label = self._optional_text(
            payload.get("experience_label", current.get("experience_label")),
            "Experience",
            300,
        ) or self._experience_label(employer, role, dates)
        now = _utc_now()
        updated = dict(current)
        updated.update(
            {
                "question_key": evidence_answer_key(question, requirement),
                "question": question,
                "normalized_question": normalize_evidence_text(question),
                "requirement": requirement,
                "normalized_requirement": normalize_evidence_text(requirement),
                "yes_no": yes_no,
                "answer_text": answer_text,
                "experience_label": experience_label,
                "experience_employer": employer,
                "experience_title": role,
                "experience_dates": dates,
                "supported_skills": self._optional_text(
                    payload.get("supported_skills", current.get("supported_skills") or requirement),
                    "Supported skills",
                    1200,
                ),
                "source_note": self._optional_text(
                    payload.get("source_note", current.get("source_note")),
                    "Source",
                    600,
                ),
                "evidence_limitations": self._optional_text(
                    payload.get(
                        "evidence_limitations", current.get("evidence_limitations")
                    ),
                    "Evidence limitations",
                    2000,
                ),
                "confirmation_status": confirmation_status,
                "confirmed_at": now
                if confirmation_status == "confirmed"
                else str(current.get("confirmed_at") or ""),
                "updated_at": now,
            }
        )
        try:
            self.repository.upsert_evidence_answer(updated)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The reusable confirmation answer could not be updated.") from exc
        return self._serialize_evidence_answer(updated)

    def record_evidence_reuse(
        self, user_id: str, evidence_ids: Iterable[str]
    ) -> None:
        now = _utc_now()
        for raw_id in dict.fromkeys(str(value or "").strip() for value in evidence_ids):
            if not raw_id:
                continue
            try:
                item = self.repository.get_evidence_answer(user_id, raw_id)
                if not item:
                    continue
                item["reuse_count"] = int(item.get("reuse_count") or 0) + 1
                item["last_reused_at"] = now
                item["updated_at"] = str(item.get("updated_at") or now)
                self.repository.upsert_evidence_answer(item)
            except (BotoCoreError, ClientError, OSError):
                current_app.logger.exception(
                    "Could not record reuse for career evidence answer %s", raw_id
                )

    def delete_evidence_answer(
        self, user_id: str, evidence_id: str
    ) -> dict[str, Any]:
        normalized_id = str(evidence_id or "").strip()
        if not normalized_id:
            raise ResourceNotFoundError("Reusable confirmation answer not found.")
        try:
            deleted = self.repository.delete_evidence_answer(user_id, normalized_id)
        except ResourceNotFoundError:
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            raise DatabaseError("The reusable confirmation answer could not be deleted.") from exc
        return self._serialize_evidence_answer(deleted)

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
    def _experience_label(employer: str, role: str, dates: str = "") -> str:
        primary = " — ".join(value for value in (employer, role) if value)
        if dates and primary:
            return f"{primary} ({dates})"
        return primary or dates

    @staticmethod
    def _serialize_evidence_answer(item: dict[str, Any]) -> dict[str, Any]:
        yes_no = item.get("yes_no")
        if yes_no not in (True, False, None):
            yes_no = None
        return {
            "evidence_id": str(item.get("evidence_id") or ""),
            "question_key": str(item.get("question_key") or ""),
            "question": str(item.get("question") or ""),
            "normalized_question": str(item.get("normalized_question") or ""),
            "requirement": str(item.get("requirement") or ""),
            "normalized_requirement": str(item.get("normalized_requirement") or ""),
            "answer_type": str(item.get("answer_type") or "short_text"),
            "yes_no": yes_no,
            "answer_text": str(item.get("answer_text") or ""),
            "experience_id": str(item.get("experience_id") or ""),
            "experience_label": str(item.get("experience_label") or ""),
            "experience_employer": str(item.get("experience_employer") or ""),
            "experience_title": str(item.get("experience_title") or ""),
            "experience_dates": str(item.get("experience_dates") or ""),
            "supported_skills": str(
                item.get("supported_skills") or item.get("requirement") or ""
            ),
            "source_note": str(item.get("source_note") or ""),
            "evidence_limitations": str(item.get("evidence_limitations") or ""),
            "confirmation_status": str(
                item.get("confirmation_status") or "confirmed"
            ),
            "entry_method": str(item.get("entry_method") or "workflow"),
            "confirmed_at": str(item.get("confirmed_at") or ""),
            "placement": str(item.get("placement") or "auto"),
            "source_application_id": str(item.get("source_application_id") or ""),
            "source_job_title": str(item.get("source_job_title") or ""),
            "source_company": str(item.get("source_company") or ""),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
            "reuse_count": int(item.get("reuse_count") or 0),
            "last_reused_at": str(item.get("last_reused_at") or ""),
        }

    @staticmethod
    def _serialize_career_role(item: dict[str, Any]) -> dict[str, Any]:
        status = str(item.get("status") or "needs_review")
        if status not in {"needs_review", "confirmed", "needs_explanation"}:
            status = "needs_review"
        return {
            "role_id": str(item.get("role_id") or ""),
            "source_experience_id": str(item.get("source_experience_id") or ""),
            "official_title": str(item.get("official_title") or ""),
            "employer": str(item.get("employer") or ""),
            "dates": str(item.get("dates") or ""),
            "location": str(item.get("location") or ""),
            "responsibilities": str(item.get("responsibilities") or ""),
            "target_market_title": str(item.get("target_market_title") or ""),
            "recruiter_explanation": str(item.get("recruiter_explanation") or ""),
            "status": status,
            "status_label": {
                "needs_review": "Needs review",
                "confirmed": "Confirmed",
                "needs_explanation": "Needs explanation",
            }[status],
            "source_type": str(item.get("source_type") or "baseline_resume"),
            "source_active": bool(item.get("source_active", True)),
            "source_fingerprint": str(item.get("source_fingerprint") or ""),
            "target_market": str(item.get("target_market") or ""),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
            "confirmed_at": str(item.get("confirmed_at") or ""),
        }

    @staticmethod
    def _career_role_id(
        source_experience_id: str,
        employer: str,
        official_title: str,
        dates: str,
    ) -> str:
        source_id = str(source_experience_id or "").strip()
        if source_id:
            normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_id).strip("-")
            if normalized:
                return normalized[:160]
        payload = "\n".join(
            normalize_evidence_text(value)
            for value in (employer, official_title, dates)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _career_role_core_fingerprint(
        *,
        official_title: str,
        employer: str,
        dates: str,
        location: str,
        responsibilities: str,
    ) -> str:
        payload = "\n".join(
            normalize_evidence_text(value)
            for value in (
                official_title,
                employer,
                dates,
                location,
                responsibilities,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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
