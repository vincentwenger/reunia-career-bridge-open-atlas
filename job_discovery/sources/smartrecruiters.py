from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

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
from .base import (
    DEFAULT_JSON_MAX_BYTES,
    CompanyRateLimiter,
    HttpClient,
    SourceFetchError,
    UrllibHttpClient,
    company_rate_limit_key,
    fetch_json,
    source_min_request_interval,
    source_redirect_limit,
    source_response_limit,
    source_timeout,
    validate_fetch_url,
    validate_source_policy,
)


_API_HOST = "api.smartrecruiters.com"
_ALLOWED_PUBLIC_HOSTS = {
    "careers.smartrecruiters.com",
    "jobs.smartrecruiters.com",
    "www.smartrecruiters.com",
    "smartrecruiters.com",
}


@dataclass(frozen=True, slots=True)
class SmartRecruitersTarget:
    company_identifier: str
    listing_url: str


def parse_smartrecruiters_careers_url(value: str) -> SmartRecruitersTarget:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("SmartRecruiters requires a public career-site URL")
    host = parsed.hostname.casefold().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    identifier = ""
    if host == _API_HOST:
        try:
            company_index = [part.casefold() for part in parts].index("companies")
        except ValueError:
            company_index = -1
        if company_index >= 0 and company_index + 1 < len(parts):
            identifier = parts[company_index + 1]
    elif host in _ALLOWED_PUBLIC_HOSTS:
        if parts:
            identifier = parts[0]
    else:
        raise ValueError(
            "SmartRecruiters URL must use careers.smartrecruiters.com, "
            "jobs.smartrecruiters.com, or api.smartrecruiters.com"
        )
    identifier = normalize_whitespace(identifier).strip("/")
    if not identifier or len(identifier) > 200:
        raise ValueError("Unable to determine the SmartRecruiters company identifier")
    return SmartRecruitersTarget(
        company_identifier=identifier,
        listing_url=f"https://careers.smartrecruiters.com/{identifier}",
    )


class SmartRecruitersJobSource:
    """Collect public postings from SmartRecruiters' documented postings API."""

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
        clock=time.monotonic,
    ) -> None:
        self.http = http_client or UrllibHttpClient()
        self.rate_limiter = rate_limiter or CompanyRateLimiter()
        self.clock = clock

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        validate_source_policy(source, expected_type=JobSourceType.SMARTRECRUITERS)
        target = parse_smartrecruiters_careers_url(source.careers_url)
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)
        max_pages = _bounded_int(source.filters.get("max_pages"), 20, 1, 50)
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"), max_jobs, 0, max_jobs
        )
        page_size = min(100, max_jobs)
        records: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            url = _listing_api_url(target.company_identifier, offset, page_size)
            payload = self._fetch_json(source, url)
            content = payload.get("content") if isinstance(payload, Mapping) else None
            if not isinstance(content, list):
                content = payload.get("jobs") if isinstance(payload, Mapping) else None
            if not isinstance(content, list):
                raise SourceFetchError("SmartRecruiters postings response did not contain jobs")
            records.extend(item for item in content if isinstance(item, Mapping))
            total = _safe_int(payload.get("totalFound") if isinstance(payload, Mapping) else 0)
            offset += len(content)
            if not content or len(records) >= max_jobs or (total and offset >= total):
                break

        jobs: list[DiscoveredJob] = []
        seen_at = utc_now_iso()
        for index, raw_record in enumerate(records[:max_jobs]):
            record = dict(raw_record)
            external_id = normalize_whitespace(
                record.get("id") or record.get("uuid") or record.get("jobId")
            )
            if not external_id:
                continue
            if index < detail_limit and not _has_description(record):
                detail_url = _detail_api_url(target.company_identifier, external_id)
                try:
                    detail = self._fetch_json(source, detail_url)
                except SourceFetchError as exc:
                    record["detail_error"] = str(exc)
                else:
                    if isinstance(detail, Mapping):
                        record.update(detail)
            title = normalize_whitespace(record.get("name") or record.get("title"))
            location = _location(record.get("location"))
            canonical_url = canonicalize_url(
                record.get("jobAdUrl")
                or record.get("jobUrl")
                or record.get("applyUrl")
                or f"https://jobs.smartrecruiters.com/{target.company_identifier}/{external_id}"
            )
            if not title or not canonical_url:
                continue
            salary_min, salary_max, currency, interval, salary_text = _compensation(
                record.get("compensation")
            )
            description = _description(record)
            metadata = {
                "smartrecruiters_company_identifier": target.company_identifier,
                "detail_status": (
                    "failed"
                    if record.get("detail_error")
                    else "fetched"
                    if index < detail_limit
                    else "deferred"
                ),
            }
            if record.get("detail_error"):
                metadata["detail_error"] = normalize_whitespace(record["detail_error"])[:1000]
            jobs.append(
                DiscoveredJob(
                    id=discovered_job_id(source.owner_id, source.id, external_id),
                    owner_id=source.owner_id,
                    source_id=source.id,
                    external_job_id=external_id,
                    company=source.company_name,
                    title=title,
                    location=location,
                    locations=normalize_string_list(location),
                    workplace_type=normalize_workplace_type(
                        record.get("workplaceType") or record.get("remote"),
                        location=location,
                    ),
                    employment_type=normalize_employment_type(
                        _label(record.get("typeOfEmployment"))
                        or record.get("employmentType")
                    ),
                    salary_text=format_salary_text(
                        salary_min,
                        salary_max,
                        currency,
                        interval,
                        summary=salary_text,
                    ),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=currency,
                    salary_interval=interval,
                    description=description,
                    canonical_url=canonical_url,
                    apply_url=canonicalize_url(record.get("applyUrl") or canonical_url),
                    posted_at=normalize_iso_timestamp(
                        parse_datetime(record.get("releasedDate") or record.get("postedDate"))
                    ),
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_type=source.source_type,
                    department=_label(record.get("department"))
                    or _label(record.get("function")),
                    skills=normalize_string_list(record.get("skills")),
                    metadata=metadata,
                )
            )
        return jobs

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        if job.source_type is not JobSourceType.SMARTRECRUITERS:
            raise ValueError("SmartRecruiters detail lookup requires a SmartRecruiters job")
        target = parse_smartrecruiters_careers_url(job.canonical_url)
        external_id = normalize_whitespace(job.external_job_id)
        if not external_id:
            raise ValueError("SmartRecruiters job is missing its posting ID")
        source = CompanySource(
            id=f"detail-{job.source_id}",
            owner_id=job.owner_id,
            company_name=job.company,
            careers_url=target.listing_url,
            source_type=JobSourceType.SMARTRECRUITERS,
            source_identifier="",
            filters={"min_request_interval_seconds": 0.0},
        )
        payload = self._fetch_json(source, _detail_api_url(target.company_identifier, external_id))
        return _description(payload if isinstance(payload, Mapping) else {})

    def _fetch_json(self, source: CompanySource, url: str) -> Any:
        self.rate_limiter.wait(
            company_rate_limit_key(source),
            source_min_request_interval(source, default=0.2),
        )
        return fetch_json(
            self.http,
            url,
            timeout=source_timeout(source),
            max_bytes=source_response_limit(source, default=DEFAULT_JSON_MAX_BYTES),
            max_redirects=source_redirect_limit(source),
            allowed_domains=(_API_HOST,),
        )


def _listing_api_url(identifier: str, offset: int, limit: int) -> str:
    query = urlencode({"limit": limit, "offset": offset})
    return f"https://{_API_HOST}/v1/companies/{identifier}/postings?{query}"


def _detail_api_url(identifier: str, posting_id: str) -> str:
    return f"https://{_API_HOST}/v1/companies/{identifier}/postings/{posting_id}"


def _description(record: Mapping[str, Any]) -> str:
    job_ad = record.get("jobAd")
    if isinstance(job_ad, Mapping):
        sections = job_ad.get("sections")
        if isinstance(sections, Mapping):
            values: list[str] = []
            for name in (
                "companyDescription",
                "jobDescription",
                "qualifications",
                "additionalInformation",
            ):
                section = sections.get(name)
                if isinstance(section, Mapping):
                    text = html_to_text(section.get("text"))
                else:
                    text = html_to_text(section)
                if text:
                    values.append(text)
            if values:
                return "\n\n".join(values)
        values = [
            html_to_text(job_ad.get(name))
            for name in (
                "companyDescription",
                "jobDescription",
                "qualifications",
                "additionalInformation",
            )
        ]
        values = [value for value in values if value]
        if values:
            return "\n\n".join(values)
    return html_to_text(record.get("description"))


def _has_description(record: Mapping[str, Any]) -> bool:
    return len(_description(record)) >= 200


def _location(value: Any) -> str:
    if isinstance(value, str):
        return normalize_whitespace(value)
    if not isinstance(value, Mapping):
        return ""
    parts = [value.get("city"), value.get("region"), value.get("country")]
    return ", ".join(normalize_whitespace(part) for part in parts if normalize_whitespace(part))


def _label(value: Any) -> str:
    if isinstance(value, Mapping):
        return normalize_whitespace(value.get("label") or value.get("name"))
    return normalize_whitespace(value)


def _compensation(value: Any) -> tuple[float | None, float | None, str, str, str]:
    if not isinstance(value, Mapping):
        return None, None, "", "", ""
    return (
        parse_number(value.get("min") or value.get("minimum")),
        parse_number(value.get("max") or value.get("maximum")),
        normalize_whitespace(value.get("currency")),
        normalize_whitespace(value.get("interval") or value.get("unit")),
        normalize_whitespace(value.get("description") or value.get("summary")),
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)
