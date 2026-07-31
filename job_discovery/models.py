from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_MAX_POSTING_AGE_DAYS = 30
MAX_POSTING_AGE_DAYS = 365


class JobSourceType(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKDAY = "workday"
    GENERIC_JSONLD = "generic_jsonld"


class WorkplaceType(str, Enum):
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"
    UNSPECIFIED = "unspecified"


class DiscoveryScheduleCadence(str, Enum):
    MANUAL = "manual"
    DAILY = "daily"
    WEEKLY = "weekly"


class DiscoveryJobDisposition(str, Enum):
    """Explicit user disposition for a discovered posting."""

    SAVED = "saved"
    IGNORED = "ignored"
    APPLICATION_CREATED = "application_created"


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
    revision: int = 0

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
        if source_type in {JobSourceType.GENERIC_JSONLD, JobSourceType.WORKDAY}:
            parsed = urlsplit(careers_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"{source_type.value} sources require an http(s) careers_url")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError(f"{source_type.value} careers_url cannot contain credentials")
            host = parsed.hostname.casefold().rstrip(".")
            if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
                raise ValueError(f"{source_type.value} careers_url must use a public hostname")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ValueError(
                        f"{source_type.value} careers_url cannot use a private or reserved IP"
                    )
            if source_type is JobSourceType.WORKDAY and not (
                host.endswith(".myworkdayjobs.com")
                or host.endswith(".myworkdaysite.com")
            ):
                raise ValueError(
                    "workday careers_url must use a public myworkdayjobs.com or myworkdaysite.com host"
                )
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
        try:
            revision = int(self.revision)
        except (TypeError, ValueError) as exc:
            raise ValueError("revision must be an integer") from exc
        if revision < 0:
            raise ValueError("revision cannot be negative")
        object.__setattr__(self, "revision", revision)

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
class PublicJobCatalogStatus:
    """Shared freshness metadata for one canonical public company source."""

    source_key: str
    source_type: JobSourceType | str
    source_identifier: str
    careers_url: str
    company_name: str
    last_success_at: str = ""
    last_attempt_at: str = ""
    job_count: int = 0
    complete_scan: bool = True
    last_error: str = ""

    def __post_init__(self) -> None:
        source_key = str(self.source_key or "").strip()
        if not source_key:
            raise ValueError("source_key is required")
        source_type = (
            self.source_type
            if isinstance(self.source_type, JobSourceType)
            else JobSourceType(str(self.source_type))
        )
        try:
            job_count = max(0, int(self.job_count))
        except (TypeError, ValueError) as exc:
            raise ValueError("job_count must be an integer") from exc
        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "source_identifier", str(self.source_identifier or "").strip())
        object.__setattr__(self, "careers_url", str(self.careers_url or "").strip())
        object.__setattr__(self, "company_name", str(self.company_name or "").strip())
        object.__setattr__(self, "last_success_at", normalize_iso_timestamp(self.last_success_at))
        object.__setattr__(self, "last_attempt_at", normalize_iso_timestamp(self.last_attempt_at))
        object.__setattr__(self, "job_count", job_count)
        object.__setattr__(self, "complete_scan", bool(self.complete_scan))
        object.__setattr__(self, "last_error", " ".join(str(self.last_error or "").split())[:1000])


@dataclass(frozen=True, slots=True)
class DiscoverySearchPreferences:
    """Owner-scoped controls for deterministic filtering and Search Priority."""

    owner_id: str
    target_titles: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    accepted_workplace_types: tuple[WorkplaceType | str, ...] = ()
    preferred_employment_types: tuple[str, ...] = ()
    preferred_keywords: tuple[str, ...] = ()
    required_keywords: tuple[str, ...] = ()
    minimum_salary: float | None = None
    minimum_salary_currency: str = "USD"
    minimum_salary_interval: str = "year"
    excluded_terms: tuple[str, ...] = ()
    maximum_posting_age_days: int | None = DEFAULT_MAX_POSTING_AGE_DAYS
    require_title_match: bool = False
    require_location_match: bool = False
    require_workplace_match: bool = False
    require_employment_type_match: bool = False
    updated_at: str = ""

    def __post_init__(self) -> None:
        owner_id = str(self.owner_id or "").strip()
        if not owner_id:
            raise ValueError("owner_id is required")
        object.__setattr__(self, "owner_id", owner_id)
        for name in (
            "target_titles",
            "preferred_locations",
            "preferred_employment_types",
            "preferred_keywords",
            "required_keywords",
            "excluded_terms",
        ):
            object.__setattr__(self, name, _clean_tuple(getattr(self, name)))
        workplaces: list[WorkplaceType] = []
        for raw in self.accepted_workplace_types or ():
            value = raw if isinstance(raw, WorkplaceType) else WorkplaceType(str(raw))
            if value is WorkplaceType.UNSPECIFIED or value in workplaces:
                continue
            workplaces.append(value)
        object.__setattr__(self, "accepted_workplace_types", tuple(workplaces))
        if self.minimum_salary in (None, ""):
            object.__setattr__(self, "minimum_salary", None)
        else:
            salary = float(self.minimum_salary)
            if salary < 0:
                raise ValueError("minimum_salary cannot be negative")
            object.__setattr__(self, "minimum_salary", salary)
        object.__setattr__(
            self,
            "minimum_salary_currency",
            str(self.minimum_salary_currency or "USD").strip().upper() or "USD",
        )
        object.__setattr__(
            self,
            "minimum_salary_interval",
            str(self.minimum_salary_interval or "year").strip().casefold() or "year",
        )
        raw_maximum_age = self.maximum_posting_age_days
        if raw_maximum_age in (None, "", "any", "all", 0, "0"):
            maximum_age = None
        else:
            try:
                maximum_age = int(raw_maximum_age)
            except (TypeError, ValueError) as exc:
                raise ValueError("maximum_posting_age_days must be an integer or empty") from exc
            if not 1 <= maximum_age <= MAX_POSTING_AGE_DAYS:
                raise ValueError(
                    f"maximum_posting_age_days must be between 1 and {MAX_POSTING_AGE_DAYS}"
                )
        object.__setattr__(self, "maximum_posting_age_days", maximum_age)
        object.__setattr__(
            self,
            "updated_at",
            normalize_iso_timestamp(self.updated_at) or utc_now_iso(),
        )


@dataclass(frozen=True, slots=True)
class DiscoveryScanSchedule:
    """Owner-managed scan timing consumed only by an external scheduler process."""

    owner_id: str
    cadence: DiscoveryScheduleCadence | str = DiscoveryScheduleCadence.MANUAL
    local_hour: int = 8
    weekday: int = 0
    timezone_name: str = "UTC"
    last_run_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        owner_id = str(self.owner_id or "").strip()
        if not owner_id:
            raise ValueError("owner_id is required")
        cadence = (
            self.cadence
            if isinstance(self.cadence, DiscoveryScheduleCadence)
            else DiscoveryScheduleCadence(str(self.cadence or "manual").strip().casefold())
        )
        try:
            local_hour = int(self.local_hour)
            weekday = int(self.weekday)
        except (TypeError, ValueError) as exc:
            raise ValueError("local_hour and weekday must be integers") from exc
        if not 0 <= local_hour <= 23:
            raise ValueError("local_hour must be between 0 and 23")
        if not 0 <= weekday <= 6:
            raise ValueError("weekday must be between 0 and 6")
        timezone_name = str(self.timezone_name or "UTC").strip() or "UTC"
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown time zone: {timezone_name}") from exc
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "cadence", cadence)
        object.__setattr__(self, "local_hour", local_hour)
        object.__setattr__(self, "weekday", weekday)
        object.__setattr__(self, "timezone_name", timezone_name)
        object.__setattr__(self, "last_run_at", normalize_iso_timestamp(self.last_run_at))
        object.__setattr__(
            self,
            "updated_at",
            normalize_iso_timestamp(self.updated_at) or utc_now_iso(),
        )

    def marked_run(self, ran_at: str | datetime) -> "DiscoveryScanSchedule":
        return replace(
            self,
            last_run_at=normalize_iso_timestamp(ran_at),
            updated_at=utc_now_iso(),
        )


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
    missed_scan_count: int = 0

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
        _validate_public_record_url(self.canonical_url, field_name="canonical_url")
        if self.apply_url:
            _validate_public_record_url(self.apply_url, field_name="apply_url")
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
        try:
            missed_scan_count = int(self.missed_scan_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("missed_scan_count must be an integer") from exc
        if missed_scan_count < 0:
            raise ValueError("missed_scan_count cannot be negative")
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
        object.__setattr__(self, "missed_scan_count", missed_scan_count)
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
            missed_scan_count=0,
        )

    def missed(self, deactivate_after: int) -> "DiscoveredJob":
        threshold = max(2, int(deactivate_after))
        misses = self.missed_scan_count + 1
        return replace(
            self,
            missed_scan_count=misses,
            active=misses < threshold,
        )

    def inactive(self) -> "DiscoveredJob":
        return replace(self, active=False)


@dataclass(frozen=True, slots=True)
class DiscoveryJobState:
    """Owner-scoped saved/ignored/converted state for one discovered job."""

    owner_id: str
    source_id: str
    job_id: str
    disposition: DiscoveryJobDisposition | str
    application_id: str = ""
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        for name in ("owner_id", "source_id", "job_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        disposition = (
            self.disposition
            if isinstance(self.disposition, DiscoveryJobDisposition)
            else DiscoveryJobDisposition(str(self.disposition))
        )
        application_id = str(self.application_id or "").strip()
        if disposition is DiscoveryJobDisposition.APPLICATION_CREATED and not application_id:
            raise ValueError("application_id is required after application creation")
        if disposition is not DiscoveryJobDisposition.APPLICATION_CREATED:
            application_id = ""
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "application_id", application_id)
        object.__setattr__(self, "updated_at", normalize_iso_timestamp(self.updated_at))


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """Traceable Career Profile or Evidence Library record used for a match."""

    record_id: str
    record_type: str
    label: str
    statement: str
    field_name: str = ""
    verification_status: str = ""

    def __post_init__(self) -> None:
        for name in ("record_id", "record_type", "label", "statement"):
            value = " ".join(str(getattr(self, name) or "").split())
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "field_name", str(self.field_name or "").strip())
        object.__setattr__(
            self,
            "verification_status",
            str(self.verification_status or "").strip(),
        )

    @property
    def surface(self) -> str:
        return (
            "Career Evidence Library"
            if self.record_type == "evidence_item"
            else "Career Profile"
        )


@dataclass(frozen=True, slots=True)
class RequirementEvidenceMatch:
    """One scored requirement with the exact records supporting its explanation."""

    requirement_id: str
    requirement: str
    status: str
    evidence: tuple[EvidenceReference, ...] = ()

    def __post_init__(self) -> None:
        requirement_id = str(self.requirement_id or "").strip()
        requirement = " ".join(str(self.requirement or "").split())
        status = str(self.status or "").strip().casefold()
        if not requirement_id:
            raise ValueError("requirement_id is required")
        if not requirement:
            raise ValueError("requirement is required")
        if status not in {"supported", "partial"}:
            raise ValueError("status must be supported or partial")
        cleaned: list[EvidenceReference] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in self.evidence or ():
            reference = raw if isinstance(raw, EvidenceReference) else EvidenceReference(**dict(raw))
            key = (reference.record_type, reference.record_id, reference.field_name)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(reference)
        if not cleaned:
            raise ValueError("traceable evidence is required for a requirement match")
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "requirement", requirement)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence", tuple(cleaned))


@dataclass(frozen=True, slots=True)
class JobFitSnapshot:
    job_id: str
    owner_id: str
    profile_fingerprint: str
    fit_score: float
    recommendation: str
    confidence: str
    description_fingerprint: str = ""
    supported_requirements: tuple[str, ...] = ()
    partial_requirements: tuple[str, ...] = ()
    unsupported_requirements: tuple[str, ...] = ()
    hard_blockers: tuple[str, ...] = ()
    evidence_matches: tuple[RequirementEvidenceMatch, ...] = ()
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
        object.__setattr__(
            self,
            "description_fingerprint",
            str(self.description_fingerprint or "").strip(),
        )
        for name in (
            "supported_requirements",
            "partial_requirements",
            "unsupported_requirements",
            "hard_blockers",
        ):
            object.__setattr__(self, name, _clean_tuple(getattr(self, name), casefold_dedupe=True))
        matches: list[RequirementEvidenceMatch] = []
        seen_match_ids: set[str] = set()
        for raw in self.evidence_matches or ():
            match = (
                raw
                if isinstance(raw, RequirementEvidenceMatch)
                else RequirementEvidenceMatch(**dict(raw))
            )
            if match.requirement_id in seen_match_ids:
                continue
            seen_match_ids.add(match.requirement_id)
            matches.append(match)
        object.__setattr__(self, "evidence_matches", tuple(matches))


@dataclass(frozen=True, slots=True)
class DiscoveryResultRecord:
    """Compact materialized card used by the Job Discovery results index."""

    owner_id: str
    evidence_fingerprint: str
    preference_fingerprint: str
    result_group: str
    job: DiscoveredJob
    recommendation_tier: str = "unassessed"
    confidence_tier: str = "unassessed"
    visibility_category: str = ""
    disposition: DiscoveryJobDisposition | str | None = None
    application_id: str = ""
    fit: JobFitSnapshot | None = None
    preference_score: float = 0.0
    freshness_score: float = 0.0
    search_priority: float | None = None
    posted_label: str = ""
    sort_rank: str = ""
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        for name in ("owner_id", "evidence_fingerprint", "preference_fingerprint"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        if self.job.owner_id != self.owner_id:
            raise ValueError("result job owner_id must match result owner_id")
        group = str(self.result_group or "").strip().casefold()
        if group not in {"recommended", "possible", "pending", "low_match", "saved", "ignored"}:
            raise ValueError(
                "result_group must be recommended, possible, pending, low_match, saved, or ignored"
            )
        disposition = self.disposition
        if disposition not in (None, "") and not isinstance(disposition, DiscoveryJobDisposition):
            disposition = DiscoveryJobDisposition(str(disposition))
        application_id = str(self.application_id or "").strip()
        if disposition is DiscoveryJobDisposition.APPLICATION_CREATED and not application_id:
            raise ValueError("application_id is required for an application-created result")
        if application_id and disposition is not DiscoveryJobDisposition.APPLICATION_CREATED:
            raise ValueError("application_id is valid only for an application-created result")
        if self.fit is not None:
            if self.fit.owner_id != self.owner_id or self.fit.job_id != self.job.id:
                raise ValueError("result fit must belong to the result owner and job")
            if self.fit.profile_fingerprint != self.evidence_fingerprint:
                raise ValueError("result fit profile must match the evidence fingerprint")
        recommendation_tier = str(self.recommendation_tier or "unassessed").strip().casefold()
        if recommendation_tier not in {"strong", "good", "stretch", "low", "unassessed"}:
            raise ValueError("Unknown recommendation_tier")
        confidence_tier = str(self.confidence_tier or "unassessed").strip().casefold()
        if confidence_tier not in {"high", "medium", "low", "unassessed"}:
            raise ValueError("Unknown confidence_tier")
        visibility_category = str(self.visibility_category or group).strip().casefold()
        if visibility_category != group:
            raise ValueError("visibility_category must match result_group")
        object.__setattr__(self, "result_group", group)
        object.__setattr__(self, "recommendation_tier", recommendation_tier)
        object.__setattr__(self, "confidence_tier", confidence_tier)
        object.__setattr__(self, "visibility_category", visibility_category)
        object.__setattr__(self, "disposition", disposition or None)
        object.__setattr__(self, "application_id", application_id)
        for name in ("preference_score", "freshness_score"):
            score = float(getattr(self, name))
            if not 0 <= score <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
            object.__setattr__(self, name, round(score, 2))
        if self.search_priority is not None:
            priority = float(self.search_priority)
            if not 0 <= priority <= 100:
                raise ValueError("search_priority must be between 0 and 100")
            object.__setattr__(self, "search_priority", round(priority, 2))
        object.__setattr__(self, "posted_label", str(self.posted_label or "").strip())
        sort_rank = str(self.sort_rank or "").strip()
        if len(sort_rank) != 8 or not sort_rank.isdigit():
            raise ValueError("sort_rank must be an eight-digit ordinal")
        object.__setattr__(self, "sort_rank", sort_rank)
        object.__setattr__(self, "updated_at", normalize_iso_timestamp(self.updated_at))


@dataclass(frozen=True, slots=True)
class DiscoveryResultIndexSummary:
    """Metadata and category counts for one materialized result index."""

    owner_id: str
    evidence_fingerprint: str
    preference_fingerprint: str
    revision_token: str
    recommended_count: int = 0
    possible_count: int = 0
    pending_count: int = 0
    low_match_count: int = 0
    saved_count: int = 0
    ignored_count: int = 0
    filtered_count: int = 0
    quality_filtered_count: int = 0
    age_filtered_count: int = 0
    built_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        for name in (
            "owner_id",
            "evidence_fingerprint",
            "preference_fingerprint",
            "revision_token",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        for name in (
            "recommended_count",
            "possible_count",
            "pending_count",
            "low_match_count",
            "saved_count",
            "ignored_count",
            "filtered_count",
            "quality_filtered_count",
            "age_filtered_count",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "built_at", normalize_iso_timestamp(self.built_at))


@dataclass(frozen=True, slots=True)
class JobAnalysisRecord:
    """Cached structured job analysis keyed only by the posting description."""

    job_id: str
    owner_id: str
    description_fingerprint: str
    target_title: str
    target_company: str = ""
    requirements: tuple[dict[str, Any], ...] = ()
    ignored_boilerplate: tuple[str, ...] = ()
    analyzed_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        for name in ("job_id", "owner_id", "description_fingerprint", "target_title"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        normalized_requirements: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw in self.requirements or ():
            item = dict(raw or {})
            requirement_id = str(item.get("id") or "").strip()
            requirement = " ".join(str(item.get("requirement") or "").split())
            if not requirement_id or not requirement or requirement_id in seen_ids:
                continue
            seen_ids.add(requirement_id)
            item["id"] = requirement_id
            item["requirement"] = requirement
            raw_keywords = item.get("keywords") or ()
            if isinstance(raw_keywords, str):
                raw_keywords = (raw_keywords,)
            item["keywords"] = list(
                _clean_tuple(tuple(raw_keywords), casefold_dedupe=True)
            )
            normalized_requirements.append(item)
        object.__setattr__(self, "target_company", str(self.target_company or "").strip())
        object.__setattr__(self, "requirements", tuple(normalized_requirements))
        object.__setattr__(
            self,
            "ignored_boilerplate",
            _clean_tuple(self.ignored_boilerplate, casefold_dedupe=True),
        )
        object.__setattr__(self, "analyzed_at", normalize_iso_timestamp(self.analyzed_at))


def profile_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_public_record_url(value: str, *, field_name: str) -> None:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} cannot contain credentials")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValueError(f"{field_name} must use a public hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError(f"{field_name} cannot use a private or reserved IP")


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
