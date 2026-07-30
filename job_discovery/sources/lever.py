from __future__ import annotations

from urllib.parse import quote

from ..models import (
    CompanySource,
    DiscoveredJob,
    JobSourceType,
    discovered_job_id,
    normalize_iso_timestamp,
    utc_now_iso,
)
from ..normalization import (
    canonicalize_url,
    format_salary_text,
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
        host = "api.eu.lever.co" if str(source.filters.get("region", "")).casefold() == "eu" else "api.lever.co"
        site = quote(source.source_identifier, safe="")
        url = f"https://{host}/v0/postings/{site}?mode=json"
        payload = fetch_json(self.http, url, timeout=source_timeout(source))
        jobs: list[DiscoveredJob] = []
        seen_at = utc_now_iso()
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
            minimum = parse_number(salary.get("min"))
            maximum = parse_number(salary.get("max"))
            currency = normalize_whitespace(salary.get("currency"))
            interval = normalize_whitespace(salary.get("interval"))
            summary = html_to_text(item.get("salaryDescriptionPlain") or item.get("salaryDescription"))
            canonical_url = canonicalize_url(item.get("hostedUrl") or "")
            if not canonical_url:
                continue
            external_job_id = str(item.get("id") or canonical_url)
            jobs.append(
                DiscoveredJob(
                    id=discovered_job_id(source.owner_id, source.id, external_job_id),
                    owner_id=source.owner_id,
                    source_id=source.id,
                    external_job_id=external_job_id,
                    company=source.company_name,
                    title=normalize_whitespace(item.get("text")),
                    location=location,
                    locations=locations,
                    workplace_type=normalize_workplace_type(item.get("workplaceType"), location=location),
                    employment_type=normalize_employment_type(categories.get("commitment")),
                    salary_text=format_salary_text(minimum, maximum, currency, interval, summary),
                    description=description,
                    canonical_url=canonical_url,
                    apply_url=canonicalize_url(item.get("applyUrl") or canonical_url),
                    posted_at=normalize_iso_timestamp(parse_datetime(item.get("createdAt"))),
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_type=source.source_type,
                    department=normalize_whitespace(categories.get("department")),
                    team=normalize_whitespace(categories.get("team")),
                    salary_min=minimum,
                    salary_max=maximum,
                    salary_currency=currency,
                    salary_interval=interval,
                    metadata={"country": item.get("country"), "state": item.get("state")},
                )
            )
        return jobs
