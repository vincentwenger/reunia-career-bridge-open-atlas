from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

APPLICATION_STATUS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("planned", "Planned"),
    ("applied", "Applied"),
    ("screening", "Screening"),
    ("interview", "Interview"),
    ("offer", "Offer"),
    ("rejected", "Rejected"),
    ("withdrawn", "Withdrawn"),
)
_APPLICATION_STATUS_VALUES = {value for value, _ in APPLICATION_STATUS_OPTIONS}

RESUME_VERSION_OPTIONS: tuple[str, ...] = (
    "Final Resume",
    "Job-Aligned Resume",
    "Initial Resume",
    "External resume",
)


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
    screening_received: bool
    interview_received: bool
    offer_received: bool
    notes: str
    next_follow_up_date: str
    created_at: str
    updated_at: str
    resume_filename: str
    resume_bytes: bytes | None
    resume_fingerprint: str

    @property
    def status_label(self) -> str:
        return dict(APPLICATION_STATUS_OPTIONS).get(self.status, self.status.title())

    @property
    def has_resume_snapshot(self) -> bool:
        return bool(self.resume_bytes)


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


def normalize_application_status(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    return normalized if normalized in _APPLICATION_STATUS_VALUES else "planned"


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

def infer_outcomes(status: str) -> tuple[bool, bool, bool]:
    normalized = normalize_application_status(status)
    if normalized == "offer":
        return True, True, True
    if normalized == "interview":
        return True, True, False
    if normalized == "screening":
        return True, False, False
    return False, False, False


def build_application_metrics(records: Iterable[ApplicationRecord]) -> ApplicationMetrics:
    items = list(records)
    submitted_items = [item for item in items if item.status != "planned"]
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
        and item.status not in {"rejected", "withdrawn"}
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
    )


class SQLiteApplicationStore:
    """Small persistent store for application outcomes and immutable resume snapshots."""

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
                    screening_received INTEGER NOT NULL DEFAULT 0,
                    interview_received INTEGER NOT NULL DEFAULT 0,
                    offer_received INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    next_follow_up_date TEXT NOT NULL DEFAULT '',
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
                """
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ApplicationRecord:
        return ApplicationRecord(
            id=row["id"],
            owner_id=row["owner_id"],
            company=row["company"],
            role=row["role"],
            job_url=row["job_url"],
            application_date=row["application_date"],
            status=row["status"],
            resume_version=row["resume_version"],
            resume_style=row["resume_style"],
            alignment_score=row["alignment_score"],
            overall_score=row["overall_score"],
            screening_received=bool(row["screening_received"]),
            interview_received=bool(row["interview_received"]),
            offer_received=bool(row["offer_received"]),
            notes=row["notes"],
            next_follow_up_date=row["next_follow_up_date"],
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
                        WHEN 'planned' THEN 0
                        WHEN 'applied' THEN 1
                        WHEN 'screening' THEN 2
                        WHEN 'interview' THEN 3
                        WHEN 'offer' THEN 4
                        ELSE 5
                    END,
                    COALESCE(NULLIF(next_follow_up_date, ''), '9999-12-31'),
                    application_date DESC,
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
        status: str = "planned",
        resume_version: str = "Final Resume",
        resume_style: str = "",
        alignment_score: float | None = None,
        overall_score: float | None = None,
        screening_received: bool | None = None,
        interview_received: bool | None = None,
        offer_received: bool | None = None,
        notes: str = "",
        next_follow_up_date: str = "",
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
            normalize_iso_date(application_date, default_today=True),
            normalized_status,
            resume_version.strip() or "Final Resume",
            resume_style.strip(),
            alignment_score,
            overall_score,
            int(inferred_screening if screening_received is None else screening_received),
            int(inferred_interview if interview_received is None else interview_received),
            int(inferred_offer if offer_received is None else offer_received),
            notes.strip(),
            normalize_iso_date(next_follow_up_date),
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
                    screening_received, interview_received, offer_received, notes,
                    next_follow_up_date, created_at, updated_at, resume_filename,
                    resume_bytes, resume_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        created = self.get(owner_id, application_id)
        if created is None:  # pragma: no cover - defensive database guard
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
    ) -> ApplicationRecord | None:
        normalized_status = normalize_application_status(status)
        inferred_screening, inferred_interview, inferred_offer = infer_outcomes(normalized_status)
        screening = screening_received or inferred_screening
        interview = interview_received or inferred_interview
        offer = offer_received or inferred_offer
        # Later milestones imply earlier ones even when a checkbox is accidentally cleared.
        interview = interview or offer
        screening = screening or interview
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE applications SET
                    company = ?, role = ?, job_url = ?, application_date = ?, status = ?,
                    screening_received = ?, interview_received = ?, offer_received = ?,
                    notes = ?, next_follow_up_date = ?, updated_at = ?
                WHERE owner_id = ? AND id = ?
                """,
                (
                    company.strip() or "Company not specified",
                    role.strip() or "Role not specified",
                    normalize_job_url(job_url),
                    normalize_iso_date(application_date, default_today=True),
                    normalized_status,
                    int(screening),
                    int(interview),
                    int(offer),
                    notes.strip(),
                    normalize_iso_date(next_follow_up_date),
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
