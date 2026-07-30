from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit


class JobSourceType(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    GENERIC_JSONLD = "generic_jsonld"


class WorkplaceType(str, Enum):
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"
    UNSPECIFIED = "unspecified"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_iso_timestamp(value: str | datetime | None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    text = str(value).strip()
    if not text:
        return ""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def description_fingerprint(description: str) -> str:
    normalized = " ".join(str(description or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def discovered_job_id(owner_id: str, source_id: str, external_job_id: str) -> str:
    material = "\x1f".join((owner_id.strip(), source_id.strip(), external_job_id.strip()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class CompanySource:
    """One owner-scoped configuration for a public company job source."""

    id: str
    owner_id: str
    company_name: str
    careers_url: str
    source_type: JobSourceType | str
    source_identifier: str
    enabled: bool = True
    last_checked_at: str = ""
    filters: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_id = str(self.id or "").strip()
        owner_id = str(self.owner_id or "").strip()
        company_name = str(self.company_name or "").strip()
        careers_url = str(self.careers_url or "").strip()
        source_identifier = str(self.source_identifier or "").strip()
        if not source_id:
            raise ValueError("id is required")
        if not owner_id:
            raise ValueError("owner_id is required")
        if not company_name:
            raise ValueError("company_name is required")
        source_type = (
            self.source_type
            if isinstance(self.source_type, JobSourceType)
            else JobSourceType(str(self.source_type))
        )
        if source_type is JobSourceType.GENERIC_JSONLD:
            parsed = urlsplit(careers_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("generic_jsonld sources require an http(s) careers_url")
        elif not source_identifier:
            raise ValueError(f"{source_type.value} sources require a source_identifier")
        object.__setattr__(self, "id", source_id)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "company_name", company_name)
        object.__setattr__(self, "careers_url", careers_url)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "source_identifier", source_identifier)
        object.__setattr__(self, "last_checked_at", normalize_iso_timestamp(self.last_checked_at))
        object.__setattr__(self, "filters", dict(self.filters or {}))

    # Compatibility/readability aliases used by the connector layer.
    @property
    def source_id(self) -> str:
        return self.id

    @property
    def identifier(self) -> str:
        return self.source_identifier

    @property
    def options(self) -> Mapping[str, object]:
        return self.filters

    def checked(self, checked_at: str | datetime) -> "CompanySource":
        return replace(self, last_checked_at=normalize_iso_timestamp(checked_at))


@dataclass(frozen=True, slots=True)
class DiscoveredJob:
    """A collected posting that remains outside the JobApplication lifecycle."""

    id: str
    owner_id: str
    source_id: str
    external_job_id: str
    company: str
    title: str
    location: str = ""
    workplace_type: WorkplaceType | str = WorkplaceType.UNSPECIFIED
    employment_type: str = ""
    salary_text: str = ""
    description: str = ""
    canonical_url: str = ""
    posted_at: str = ""
    description_fingerprint: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    active: bool = True

    # Additional normalized public-source attributes retained for ranking and UI.
    source_type: JobSourceType | str = JobSourceType.GENERIC_JSONLD
    apply_url: str = ""
    locations: tuple[str, ...] = ()
    department: str = ""
    team: str = ""
    skills: tuple[str, ...] = ()
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_interval: str = ""
    valid_through: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required = (
            "id",
            "owner_id",
            "source_id",
            "external_job_id",
            "company",
            "title",
            "canonical_url",
        )
        for name in required:
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        parsed = urlsplit(self.canonical_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("canonical_url must be an absolute http(s) URL")
        if self.apply_url:
            apply = urlsplit(self.apply_url)
            if apply.scheme not in {"http", "https"} or not apply.netloc:
                raise ValueError("apply_url must be an absolute http(s) URL")
        source_type = (
            self.source_type
            if isinstance(self.source_type, JobSourceType)
            else JobSourceType(str(self.source_type))
        )
        workplace_type = (
            self.workplace_type
            if isinstance(self.workplace_type, WorkplaceType)
            else WorkplaceType(str(self.workplace_type or WorkplaceType.UNSPECIFIED.value))
        )
        first_seen = normalize_iso_timestamp(self.first_seen_at) or utc_now_iso()
        last_seen = normalize_iso_timestamp(self.last_seen_at) or first_seen
        posted_at = normalize_iso_timestamp(self.posted_at)
        valid_through = normalize_iso_timestamp(self.valid_through)
        if last_seen < first_seen:
            raise ValueError("last_seen_at cannot precede first_seen_at")
        if self.salary_min is not None and self.salary_max is not None:
            if float(self.salary_min) > float(self.salary_max):
                raise ValueError("salary_min cannot exceed salary_max")
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "workplace_type", workplace_type)
        object.__setattr__(self, "posted_at", posted_at)
        object.__setattr__(self, "valid_through", valid_through)
        object.__setattr__(self, "first_seen_at", first_seen)
        object.__setattr__(self, "last_seen_at", last_seen)
        object.__setattr__(
            self,
            "description_fingerprint",
            str(self.description_fingerprint or "").strip()
            or description_fingerprint(self.description),
        )
        object.__setattr__(self, "locations", _clean_tuple(self.locations))
        object.__setattr__(self, "skills", _clean_tuple(self.skills, casefold_dedupe=True))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def external_id(self) -> str:
        return self.external_job_id

    @property
    def job_url(self) -> str:
        return self.canonical_url

    @property
    def salary_summary(self) -> str:
        return self.salary_text

    def seen(self, seen_at: str | datetime, *, first_seen_at: str | None = None) -> "DiscoveredJob":
        return replace(
            self,
            first_seen_at=first_seen_at or self.first_seen_at,
            last_seen_at=normalize_iso_timestamp(seen_at),
            active=True,
        )

    def inactive(self) -> "DiscoveredJob":
        return replace(self, active=False)


@dataclass(frozen=True, slots=True)
class JobFitSnapshot:
    job_id: str
    owner_id: str
    profile_fingerprint: str
    fit_score: float
    recommendation: str
    confidence: str
    supported_requirements: tuple[str, ...] = ()
    partial_requirements: tuple[str, ...] = ()
    unsupported_requirements: tuple[str, ...] = ()
    hard_blockers: tuple[str, ...] = ()
    analyzed_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        for name in ("job_id", "owner_id", "profile_fingerprint", "recommendation", "confidence"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        score = float(self.fit_score)
        if score < 0 or score > 100:
            raise ValueError("fit_score must be between 0 and 100")
        object.__setattr__(self, "fit_score", round(score, 2))
        object.__setattr__(self, "analyzed_at", normalize_iso_timestamp(self.analyzed_at))
        for name in (
            "supported_requirements",
            "partial_requirements",
            "unsupported_requirements",
            "hard_blockers",
        ):
            object.__setattr__(self, name, _clean_tuple(getattr(self, name), casefold_dedupe=True))


def profile_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _clean_tuple(values: tuple[str, ...], *, casefold_dedupe: bool = False) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.casefold() if casefold_dedupe else value
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return tuple(result)
