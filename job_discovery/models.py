from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
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


@dataclass(frozen=True, slots=True)
class CompanySource:
    """Configuration for one public company job source.

    ``identifier`` is the Greenhouse board token, Lever site identifier, or
    Ashby job-board name. ``careers_url`` is required for generic JSON-LD.
    Adapter-specific, non-secret settings belong in ``options``.
    """

    source_id: str
    company_name: str
    source_type: JobSourceType
    identifier: str = ""
    careers_url: str = ""
    enabled: bool = True
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_id = self.source_id.strip()
        company_name = self.company_name.strip()
        identifier = self.identifier.strip()
        careers_url = self.careers_url.strip()
        if not source_id:
            raise ValueError("source_id is required")
        if not company_name:
            raise ValueError("company_name is required")
        if not isinstance(self.source_type, JobSourceType):
            object.__setattr__(self, "source_type", JobSourceType(str(self.source_type)))
        if self.source_type is JobSourceType.GENERIC_JSONLD:
            parsed = urlsplit(careers_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("generic_jsonld sources require an http(s) careers_url")
        elif not identifier:
            raise ValueError(f"{self.source_type.value} sources require an identifier")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "company_name", company_name)
        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(self, "careers_url", careers_url)
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


@dataclass(frozen=True, slots=True)
class DiscoveredJob:
    source_id: str
    source_type: JobSourceType
    external_id: str
    company: str
    title: str
    job_url: str
    apply_url: str = ""
    description: str = ""
    location: str = ""
    locations: tuple[str, ...] = ()
    workplace_type: WorkplaceType = WorkplaceType.UNSPECIFIED
    employment_type: str = ""
    department: str = ""
    team: str = ""
    skills: tuple[str, ...] = ()
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_interval: str = ""
    salary_summary: str = ""
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    valid_through: datetime | None = None
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("source_id", "external_id", "company", "title", "job_url"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        if not isinstance(self.source_type, JobSourceType):
            object.__setattr__(self, "source_type", JobSourceType(str(self.source_type)))
        if not isinstance(self.workplace_type, WorkplaceType):
            object.__setattr__(self, "workplace_type", WorkplaceType(str(self.workplace_type)))
        parsed = urlsplit(self.job_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("job_url must be an absolute http(s) URL")
        if self.apply_url:
            apply = urlsplit(self.apply_url)
            if apply.scheme not in {"http", "https"} or not apply.netloc:
                raise ValueError("apply_url must be an absolute http(s) URL")
        for name in ("posted_at", "updated_at", "valid_through", "discovered_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.salary_min is not None and self.salary_max is not None:
            if float(self.salary_min) > float(self.salary_max):
                raise ValueError("salary_min cannot exceed salary_max")
        object.__setattr__(self, "locations", _clean_tuple(self.locations))
        object.__setattr__(self, "skills", _clean_tuple(self.skills, casefold_dedupe=True))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _clean_tuple(values: tuple[str, ...], *, casefold_dedupe: bool = False) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.casefold() if casefold_dedupe else value
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return tuple(result)
