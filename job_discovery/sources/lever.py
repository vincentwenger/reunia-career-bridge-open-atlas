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
    parse_number,
)
from .base import HttpClient, UrllibHttpClient, fetch_json, source_timeout


class LeverJobSource:
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self.http = http_client or UrllibHttpClient()

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        if source.source_type is not JobSourceType.LEVER:
            raise ValueError("LeverJobSource requires a lever CompanySource")
        host = "api.eu.lever.co" if str(source.options.get("region", "")).casefold() == "eu" else "api.lever.co"
        site = quote(source.identifier, safe="")
        url = f"https://{host}/v0/postings/{site}?mode=json"
        payload = fetch_json(self.http, url, timeout=source_timeout(source))
        jobs: list[DiscoveredJob] = []
        for item in payload if isinstance(payload, list) else []:
            categories = item.get("categories") or {}
            location = normalize_whitespace(categories.get("location"))
            locations = normalize_string_list(categories.get("allLocations") or location)
            description_parts = [item.get("descriptionPlain") or item.get("description")]
            for block in item.get("lists") or []:
                description_parts.extend((block.get("text"), block.get("content")))
            description_parts.append(item.get("additionalPlain") or item.get("additional"))
            description = html_to_text(" ".join(str(part or "") for part in description_parts))
            salary = item.get("salaryRange") or {}
            job_url = canonicalize_url(item.get("hostedUrl") or "")
            if not job_url:
                continue
            jobs.append(
                DiscoveredJob(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    external_id=str(item.get("id") or job_url),
                    company=source.company_name,
                    title=normalize_whitespace(item.get("text")),
                    job_url=job_url,
                    apply_url=canonicalize_url(item.get("applyUrl") or job_url),
                    description=description,
                    location=location,
                    locations=locations,
                    workplace_type=normalize_workplace_type(item.get("workplaceType"), location=location),
                    employment_type=normalize_employment_type(categories.get("commitment")),
                    department=normalize_whitespace(categories.get("department")),
                    team=normalize_whitespace(categories.get("team")),
                    salary_min=parse_number(salary.get("min")),
                    salary_max=parse_number(salary.get("max")),
                    salary_currency=normalize_whitespace(salary.get("currency")),
                    salary_interval=normalize_whitespace(salary.get("interval")),
                    salary_summary=html_to_text(item.get("salaryDescriptionPlain") or item.get("salaryDescription")),
                    posted_at=parse_datetime(item.get("createdAt")),
                    metadata={"country": item.get("country"), "state": item.get("state")},
                )
            )
        return jobs
