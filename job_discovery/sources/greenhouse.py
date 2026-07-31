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
    html_to_text,
    normalize_employment_type,
    normalize_string_list,
    normalize_whitespace,
    normalize_workplace_type,
    parse_datetime,
)
from .base import (
    DEFAULT_JSON_MAX_BYTES,
    CompanyRateLimiter,
    HttpClient,
    UrllibHttpClient,
    company_rate_limit_key,
    fetch_json,
    source_min_request_interval,
    source_redirect_limit,
    source_response_limit,
    source_timeout,
    validate_source_policy,
)


class GreenhouseJobSource:
    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
    ) -> None:
        self.http = http_client or UrllibHttpClient()
        self.rate_limiter = rate_limiter or CompanyRateLimiter()

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        validate_source_policy(source, expected_type=JobSourceType.GREENHOUSE)
        token = quote(source.source_identifier, safe="")
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        self.rate_limiter.wait(
            company_rate_limit_key(source), source_min_request_interval(source)
        )
        payload = fetch_json(
            self.http,
            url,
            timeout=source_timeout(source),
            max_bytes=source_response_limit(source, default=DEFAULT_JSON_MAX_BYTES),
            max_redirects=source_redirect_limit(source),
            allowed_domains=("boards-api.greenhouse.io",),
        )
        jobs: list[DiscoveredJob] = []
        seen_at = utc_now_iso()
        for item in payload.get("jobs", []):
            location = html_to_text((item.get("location") or {}).get("name"))
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
            canonical_url = canonicalize_url(item.get("absolute_url") or "")
            if not canonical_url:
                continue
            external_job_id = str(item.get("id") or item.get("internal_job_id") or canonical_url)
            jobs.append(
                DiscoveredJob(
                    id=discovered_job_id(source.owner_id, source.id, external_job_id),
                    owner_id=source.owner_id,
                    source_id=source.id,
                    external_job_id=external_job_id,
                    company=source.company_name,
                    title=html_to_text(item.get("title")),
                    location=location,
                    locations=(location,) if location else (),
                    workplace_type=normalize_workplace_type("", location=location),
                    employment_type=employment,
                    description=html_to_text(item.get("content")),
                    canonical_url=canonical_url,
                    apply_url=canonical_url,
                    posted_at="",
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_type=source.source_type,
                    department=", ".join(normalize_string_list(item.get("departments"))),
                    skills=skills,
                    metadata={
                        "internal_job_id": item.get("internal_job_id"),
                        "requisition_id": item.get("requisition_id"),
                        "language": html_to_text(item.get("language")),
                        "updated_at": normalize_iso_timestamp(parse_datetime(item.get("updated_at"))),
                    },
                )
            )
        return jobs
