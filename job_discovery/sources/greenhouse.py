from __future__ import annotations

from urllib.parse import quote

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import (
    canonicalize_url,
    html_to_text,
    normalize_employment_type,
    normalize_string_list,
    normalize_whitespace,
    normalize_workplace_type,
    parse_datetime,
)
from .base import HttpClient, UrllibHttpClient, fetch_json, source_timeout


class GreenhouseJobSource:
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self.http = http_client or UrllibHttpClient()

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        if source.source_type is not JobSourceType.GREENHOUSE:
            raise ValueError("GreenhouseJobSource requires a greenhouse CompanySource")
        token = quote(source.identifier, safe="")
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        payload = fetch_json(self.http, url, timeout=source_timeout(source))
        jobs: list[DiscoveredJob] = []
        for item in payload.get("jobs", []):
            location = normalize_whitespace((item.get("location") or {}).get("name"))
            metadata = item.get("metadata") or []
            employment = ""
            skills: tuple[str, ...] = ()
            if isinstance(metadata, list):
                for field in metadata:
                    name = normalize_whitespace(field.get("name")).casefold()
                    value = field.get("value")
                    if "employment" in name or "commitment" in name:
                        employment = normalize_employment_type(value)
                    elif "skill" in name:
                        skills = normalize_string_list(value)
            job_url = canonicalize_url(item.get("absolute_url") or "")
            if not job_url:
                continue
            jobs.append(
                DiscoveredJob(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    external_id=str(item.get("id") or item.get("internal_job_id") or job_url),
                    company=source.company_name,
                    title=normalize_whitespace(item.get("title")),
                    job_url=job_url,
                    apply_url=job_url,
                    description=html_to_text(item.get("content")),
                    location=location,
                    locations=(location,) if location else (),
                    workplace_type=normalize_workplace_type("", location=location),
                    employment_type=employment,
                    department=", ".join(normalize_string_list(item.get("departments"))),
                    skills=skills,
                    updated_at=parse_datetime(item.get("updated_at")),
                    metadata={
                        "internal_job_id": item.get("internal_job_id"),
                        "requisition_id": item.get("requisition_id"),
                        "language": item.get("language"),
                    },
                )
            )
        return jobs
