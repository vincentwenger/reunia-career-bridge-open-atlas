from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

APPLICATION_STATUS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("draft", "Draft"),
    ("considering", "Considering"),
    ("preparing", "Preparing"),
    ("ready_to_apply", "Ready to apply"),
    ("applied", "Applied"),
    ("screening", "Screening"),
    ("interviewing", "Interviewing"),
    ("offered", "Offered"),
    ("accepted", "Accepted"),
    ("rejected", "Rejected"),
    ("withdrawn", "Withdrawn"),
    ("archived", "Archived"),
)
_APPLICATION_STATUS_VALUES = {value for value, _ in APPLICATION_STATUS_OPTIONS}
_APPLICATION_STATUS_ALIASES = {
    "planned": "draft",
    "interview": "interviewing",
    "offer": "offered",
}

RESUME_VERSION_OPTIONS: tuple[str, ...] = (
    "Not started",
    "Initial Resume",
    "Tailored Resume",
    "Final Resume",
    "External resume",
)

UPCOMING_EVENT_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "No upcoming event"),
    ("application_deadline", "Application deadline"),
    ("interview", "Interview"),
    ("follow_up", "Follow-up"),
)

APPLICATION_BUILDER_STEP_LABELS: dict[str, str] = {
    "setup": "Career and Job Setup",
    "confirmation": "Confirm Relevant Experience",
    "review": "Review Tailored Resume",
    "quality": "Improve Resume Quality",
    "finalize": "Finalize Resume",
    "evidence_export": "Evidence Review and Export",
}

APPLICATION_BUILDER_NEXT_ACTIONS: dict[str, str] = {
    "setup": "Complete Career and Job Setup",
    "confirmation": "Confirm relevant experience",
    "review": "Review the tailored resume",
    "quality": "Improve resume quality",
    "finalize": "Finalize the resume",
    "evidence_export": "Review evidence and export the resume",
}


@dataclass(frozen=True)
class ApplicationRecord:
    id: str
    owner_id: str
    company: str
    role: str
    job_url: str
    application_date: str
    status: str
    resume_version: str
    resume_style: str
    alignment_score: float | None
    overall_score: float | None
    interview_readiness: float | None
    screening_received: bool
    interview_received: bool
    offer_received: bool
    notes: str
    next_action: str
    next_follow_up_date: str
    upcoming_event_date: str
    upcoming_event_type: str
    job_description: str
    workflow_step: str
    created_at: str
    updated_at: str
    resume_filename: str
    resume_bytes: bytes | None
    resume_fingerprint: str

    @property
    def status_label(self) -> str:
        return dict(APPLICATION_STATUS_OPTIONS).get(
            normalize_application_status(self.status), self.status.replace("_", " ").title()
        )

    @property
    def has_resume_snapshot(self) -> bool:
        return bool(self.resume_bytes)

    @property
    def interview_readiness_label(self) -> str:
        if self.interview_readiness is None:
            return "Not assessed"
        return f"{self.interview_readiness:.0f}%"

    @property
    def next_action_label(self) -> str:
        if self.next_action.strip():
            return self.next_action.strip()
        status_defaults = {
            "ready_to_apply": "Submit the application",
            "applied": "Track employer response",
            "screening": "Prepare for the screening conversation",
            "interviewing": "Practice for the next interview",
            "offered": "Review the offer",
            "accepted": "Complete onboarding steps",
            "rejected": "Capture lessons and archive when ready",
            "withdrawn": "Archive the application",
            "archived": "No action required",
        }
        status_action = status_defaults.get(normalize_application_status(self.status))
        if status_action:
            return status_action
        return APPLICATION_BUILDER_NEXT_ACTIONS.get(
            self.workflow_step, "Continue this application"
        )

    @property
    def workflow_step_label(self) -> str:
        return APPLICATION_BUILDER_STEP_LABELS.get(
            self.workflow_step, "Career and Job Setup"
        )

    @property
    def upcoming_event_label(self) -> str:
        if not self.upcoming_event_date:
            return "None scheduled"
        event_label = (
            dict(UPCOMING_EVENT_TYPE_OPTIONS).get(self.upcoming_event_type)
            if self.upcoming_event_type
            else None
        ) or "Upcoming milestone"
        return f"{event_label} · {self.upcoming_event_date}"


@dataclass(frozen=True)
class InterviewPreparationRecord:
    application_id: str
    owner_id: str
    content_json: str
    job_description_fingerprint: str
    evidence_fingerprint: str
    evidence_source_label: str
    evidence_snapshot_json: str
    model_name: str
    created_at: str
    updated_at: str

    def payload(self) -> dict[str, object]:
        try:
            value = json.loads(self.content_json)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ApplicationMetrics:
    tracked: int
    submitted: int
    screening_count: int
    interview_count: int
    offer_count: int
    screening_rate: float
    interview_rate: float
    offer_rate: float
    average_interview_alignment: float | None
    follow_ups_due: int
    ready_for_interview: int


def normalize_application_status(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    normalized = _APPLICATION_STATUS_ALIASES.get(normalized, normalized)
    return normalized if normalized in _APPLICATION_STATUS_VALUES else "draft"


def normalize_application_builder_step(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    return normalized if normalized in APPLICATION_BUILDER_STEP_LABELS else "setup"


def normalize_iso_date(value: str | None, *, default_today: bool = False) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return date.today().isoformat() if default_today else ""
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return date.today().isoformat() if default_today else ""


def normalize_job_url(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate


def normalize_optional_score(value: float | int | str | None) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return round(max(0.0, min(100.0, float(value))), 1)
    except (TypeError, ValueError):
        return None


def infer_outcomes(status: str) -> tuple[bool, bool, bool]:
    normalized = normalize_application_status(status)
    if normalized in {"offered", "accepted"}:
        return True, True, True
    if normalized == "interviewing":
        return True, True, False
    if normalized == "screening":
        return True, False, False
    return False, False, False


def build_application_metrics(records: Iterable[ApplicationRecord]) -> ApplicationMetrics:
    items = list(records)
    submitted_items = [
        item
        for item in items
        if normalize_application_status(item.status)
        not in {"draft", "considering", "preparing", "ready_to_apply", "archived"}
    ]
    denominator = len(submitted_items)
    screening_count = sum(item.screening_received for item in submitted_items)
    interview_count = sum(item.interview_received for item in submitted_items)
    offer_count = sum(item.offer_received for item in submitted_items)
    interviewed_scores = [
        item.alignment_score
        for item in submitted_items
        if item.interview_received and item.alignment_score is not None
    ]
    today = date.today().isoformat()
    follow_ups_due = sum(
        bool(item.next_follow_up_date)
        and item.next_follow_up_date <= today
        and normalize_application_status(item.status)
        not in {"rejected", "withdrawn", "archived", "accepted"}
        for item in items
    )
    ready_for_interview = sum(
        item.interview_readiness is not None and item.interview_readiness >= 70
        for item in items
    )

    def rate(count: int) -> float:
        return round((count / denominator) * 100, 1) if denominator else 0.0

    return ApplicationMetrics(
        tracked=len(items),
        submitted=denominator,
        screening_count=screening_count,
        interview_count=interview_count,
        offer_count=offer_count,
        screening_rate=rate(screening_count),
        interview_rate=rate(interview_count),
        offer_rate=rate(offer_count),
        average_interview_alignment=(
            round(sum(interviewed_scores) / len(interviewed_scores), 1)
            if interviewed_scores
            else None
        ),
        follow_ups_due=follow_ups_due,
        ready_for_interview=ready_for_interview,
    )


class SQLiteApplicationStore:
    """Persistent Application Builder dashboard adapter.

    The table is intentionally an application-oriented read model. Resume workflow
    internals remain in the existing workflow store while this record keeps the
    cross-module fields needed by the shared Career Bridge ``JobApplication``.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._lock = threading.RLock()
        path = str(database_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    company TEXT NOT NULL,
                    role TEXT NOT NULL,
                    job_url TEXT NOT NULL DEFAULT '',
                    application_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resume_version TEXT NOT NULL,
                    resume_style TEXT NOT NULL DEFAULT '',
                    alignment_score REAL,
                    overall_score REAL,
                    interview_readiness REAL,
                    screening_received INTEGER NOT NULL DEFAULT 0,
                    interview_received INTEGER NOT NULL DEFAULT 0,
                    offer_received INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    next_action TEXT NOT NULL DEFAULT '',
                    next_follow_up_date TEXT NOT NULL DEFAULT '',
                    upcoming_event_date TEXT NOT NULL DEFAULT '',
                    upcoming_event_type TEXT NOT NULL DEFAULT '',
                    job_description TEXT NOT NULL DEFAULT '',
                    workflow_step TEXT NOT NULL DEFAULT 'setup',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resume_filename TEXT NOT NULL DEFAULT '',
                    resume_bytes BLOB,
                    resume_fingerprint TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS applications_owner_updated_idx
                    ON applications(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS applications_owner_fingerprint_idx
                    ON applications(owner_id, resume_fingerprint);
                CREATE TABLE IF NOT EXISTS interview_preparations (
                    application_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    job_description_fingerprint TEXT NOT NULL,
                    evidence_fingerprint TEXT NOT NULL,
                    evidence_source_label TEXT NOT NULL DEFAULT '',
                    evidence_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    model_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS interview_preparations_owner_updated_idx
                    ON interview_preparations(owner_id, updated_at DESC);
                """
            )
            existing = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(applications)")
            }
            migrations = {
                "interview_readiness": "REAL",
                "next_action": "TEXT NOT NULL DEFAULT ''",
                "upcoming_event_date": "TEXT NOT NULL DEFAULT ''",
                "upcoming_event_type": "TEXT NOT NULL DEFAULT ''",
                "job_description": "TEXT NOT NULL DEFAULT ''",
                "workflow_step": "TEXT NOT NULL DEFAULT 'setup'",
            }
            for column, definition in migrations.items():
                if column not in existing:
                    self._connection.execute(
                        f"ALTER TABLE applications ADD COLUMN {column} {definition}"
                    )
            preparation_columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(interview_preparations)"
                )
            }
            if "evidence_snapshot_json" not in preparation_columns:
                self._connection.execute(
                    "ALTER TABLE interview_preparations "
                    "ADD COLUMN evidence_snapshot_json TEXT NOT NULL DEFAULT '{}'"
                )

            for legacy, canonical in _APPLICATION_STATUS_ALIASES.items():
                self._connection.execute(
                    "UPDATE applications SET status = ? WHERE lower(status) = ?",
                    (canonical, legacy),
                )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ApplicationRecord:
        keys = set(row.keys())
        return ApplicationRecord(
            id=row["id"],
            owner_id=row["owner_id"],
            company=row["company"],
            role=row["role"],
            job_url=row["job_url"],
            application_date=row["application_date"],
            status=normalize_application_status(row["status"]),
            resume_version=row["resume_version"],
            resume_style=row["resume_style"],
            alignment_score=row["alignment_score"],
            overall_score=row["overall_score"],
            interview_readiness=(row["interview_readiness"] if "interview_readiness" in keys else None),
            screening_received=bool(row["screening_received"]),
            interview_received=bool(row["interview_received"]),
            offer_received=bool(row["offer_received"]),
            notes=row["notes"],
            next_action=(row["next_action"] if "next_action" in keys else ""),
            next_follow_up_date=row["next_follow_up_date"],
            upcoming_event_date=(row["upcoming_event_date"] if "upcoming_event_date" in keys else ""),
            upcoming_event_type=(row["upcoming_event_type"] if "upcoming_event_type" in keys else ""),
            job_description=(row["job_description"] if "job_description" in keys else ""),
            workflow_step=(row["workflow_step"] if "workflow_step" in keys else "setup"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            resume_filename=row["resume_filename"],
            resume_bytes=row["resume_bytes"],
            resume_fingerprint=row["resume_fingerprint"],
        )

    def list_for_owner(self, owner_id: str) -> list[ApplicationRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM applications
                WHERE owner_id = ?
                ORDER BY
                    CASE status
                        WHEN 'interviewing' THEN 0
                        WHEN 'screening' THEN 1
                        WHEN 'ready_to_apply' THEN 2
                        WHEN 'preparing' THEN 3
                        WHEN 'draft' THEN 4
                        WHEN 'applied' THEN 5
                        WHEN 'offered' THEN 6
                        ELSE 7
                    END,
                    COALESCE(NULLIF(upcoming_event_date, ''), '9999-12-31'),
                    updated_at DESC
                """,
                (owner_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get(self, owner_id: str, application_id: str) -> ApplicationRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM applications WHERE owner_id = ? AND id = ?",
                (owner_id, application_id),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get_interview_preparation(
        self, owner_id: str, application_id: str
    ) -> InterviewPreparationRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM interview_preparations
                WHERE owner_id = ? AND application_id = ?
                """,
                (owner_id, application_id),
            ).fetchone()
        if row is None:
            return None
        return InterviewPreparationRecord(
            application_id=row["application_id"],
            owner_id=row["owner_id"],
            content_json=row["content_json"],
            job_description_fingerprint=row["job_description_fingerprint"],
            evidence_fingerprint=row["evidence_fingerprint"],
            evidence_source_label=row["evidence_source_label"],
            evidence_snapshot_json=row["evidence_snapshot_json"],
            model_name=row["model_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
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
        model_name: str,
    ) -> InterviewPreparationRecord:
        application = self.get(owner_id, application_id)
        if application is None:
            raise ValueError("The selected application does not exist.")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = self.get_interview_preparation(owner_id, application_id)
        created_at = existing.created_at if existing is not None else now
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO interview_preparations (
                    application_id, owner_id, content_json,
                    job_description_fingerprint, evidence_fingerprint,
                    evidence_source_label, evidence_snapshot_json, model_name,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(application_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    content_json = excluded.content_json,
                    job_description_fingerprint = excluded.job_description_fingerprint,
                    evidence_fingerprint = excluded.evidence_fingerprint,
                    evidence_source_label = excluded.evidence_source_label,
                    evidence_snapshot_json = excluded.evidence_snapshot_json,
                    model_name = excluded.model_name,
                    updated_at = excluded.updated_at
                """,
                (
                    application_id,
                    owner_id,
                    content_json,
                    job_description_fingerprint,
                    evidence_fingerprint,
                    evidence_source_label.strip(),
                    evidence_snapshot_json,
                    model_name.strip(),
                    created_at,
                    now,
                ),
            )
        saved = self.get_interview_preparation(owner_id, application_id)
        if saved is None:  # pragma: no cover
            raise RuntimeError("Interview preparation was not saved.")
        return saved

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
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM applications
                WHERE owner_id = ? AND resume_fingerprint = ?
                  AND lower(company) = lower(?) AND lower(role) = lower(?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (owner_id, resume_fingerprint, company, role),
            ).fetchone()
        return self._row_to_record(row) if row else None

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
    ) -> ApplicationRecord:
        normalized_status = normalize_application_status(status)
        inferred_screening, inferred_interview, inferred_offer = infer_outcomes(normalized_status)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        application_id = uuid.uuid4().hex
        values = (
            application_id,
            owner_id,
            company.strip() or "Company not specified",
            role.strip() or "Role not specified",
            normalize_job_url(job_url),
            normalize_iso_date(application_date),
            normalized_status,
            resume_version.strip() or "Not started",
            resume_style.strip(),
            normalize_optional_score(alignment_score),
            normalize_optional_score(overall_score),
            normalize_optional_score(interview_readiness),
            int(inferred_screening if screening_received is None else screening_received),
            int(inferred_interview if interview_received is None else interview_received),
            int(inferred_offer if offer_received is None else offer_received),
            notes.strip(),
            next_action.strip(),
            normalize_iso_date(next_follow_up_date),
            normalize_iso_date(upcoming_event_date),
            upcoming_event_type.strip() if upcoming_event_type in dict(UPCOMING_EVENT_TYPE_OPTIONS) else "",
            job_description.strip(),
            normalize_application_builder_step(workflow_step),
            now,
            now,
            resume_filename.strip(),
            resume_bytes,
            resume_fingerprint,
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO applications (
                    id, owner_id, company, role, job_url, application_date, status,
                    resume_version, resume_style, alignment_score, overall_score,
                    interview_readiness, screening_received, interview_received,
                    offer_received, notes, next_action, next_follow_up_date,
                    upcoming_event_date, upcoming_event_type, job_description,
                    workflow_step, created_at, updated_at, resume_filename,
                    resume_bytes, resume_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        created = self.get(owner_id, application_id)
        if created is None:  # pragma: no cover
            raise RuntimeError("Application record was not created.")
        return created

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
    ) -> ApplicationRecord | None:
        normalized_status = normalize_application_status(status)
        inferred_screening, inferred_interview, inferred_offer = infer_outcomes(normalized_status)
        screening = screening_received or inferred_screening
        interview = interview_received or inferred_interview
        offer = offer_received or inferred_offer
        interview = interview or offer
        screening = screening or interview
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current = self.get(owner_id, application_id)
        if current is None:
            return None
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE applications SET
                    company = ?, role = ?, job_url = ?, application_date = ?, status = ?,
                    screening_received = ?, interview_received = ?, offer_received = ?,
                    notes = ?, next_action = ?, next_follow_up_date = ?,
                    interview_readiness = ?, upcoming_event_date = ?, upcoming_event_type = ?,
                    job_description = ?, updated_at = ?
                WHERE owner_id = ? AND id = ?
                """,
                (
                    company.strip() or "Company not specified",
                    role.strip() or "Role not specified",
                    normalize_job_url(job_url),
                    normalize_iso_date(application_date),
                    normalized_status,
                    int(screening),
                    int(interview),
                    int(offer),
                    notes.strip(),
                    next_action.strip(),
                    normalize_iso_date(next_follow_up_date),
                    normalize_optional_score(interview_readiness),
                    normalize_iso_date(upcoming_event_date),
                    upcoming_event_type.strip() if upcoming_event_type in dict(UPCOMING_EVENT_TYPE_OPTIONS) else "",
                    current.job_description if job_description is None else job_description.strip(),
                    now,
                    owner_id,
                    application_id,
                ),
            )
        return self.get(owner_id, application_id)

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
    ) -> ApplicationRecord | None:
        current = self.get(owner_id, application_id)
        if current is None:
            return None
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE applications SET workflow_step = ?, resume_version = ?, company = ?,
                    role = ?, job_description = ?, status = ?, updated_at = ?
                WHERE owner_id = ? AND id = ?
                """,
                (
                    normalize_application_builder_step(workflow_step) if workflow_step.strip() else current.workflow_step,
                    (resume_version or current.resume_version).strip(),
                    (company or current.company).strip(),
                    (role or current.role).strip(),
                    current.job_description if job_description is None else job_description.strip(),
                    normalize_application_status(status or current.status),
                    now,
                    owner_id,
                    application_id,
                ),
            )
        return self.get(owner_id, application_id)

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
    ) -> ApplicationRecord | None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE applications SET resume_version = ?, resume_style = ?,
                    alignment_score = ?, overall_score = ?, resume_filename = ?,
                    resume_bytes = ?, resume_fingerprint = ?, workflow_step = 'evidence_export',
                    status = CASE WHEN status IN ('draft', 'considering', 'preparing') THEN 'ready_to_apply' ELSE status END,
                    updated_at = ?
                WHERE owner_id = ? AND id = ?
                """,
                (
                    resume_version,
                    resume_style,
                    normalize_optional_score(alignment_score),
                    normalize_optional_score(overall_score),
                    resume_filename,
                    resume_bytes,
                    resume_fingerprint,
                    now,
                    owner_id,
                    application_id,
                ),
            )
        return self.get(owner_id, application_id)

    def delete(self, owner_id: str, application_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM applications WHERE owner_id = ? AND id = ?",
                (owner_id, application_id),
            )
        return cursor.rowcount > 0
