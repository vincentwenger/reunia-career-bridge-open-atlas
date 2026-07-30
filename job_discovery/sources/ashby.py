from __future__ import annotations

from urllib.parse import quote, urlencode

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
    normalize_employment_type,
    normalize_string_list,
    normalize_whitespace,
    normalize_workplace_type,
    parse_datetime,
    parse_number,
)
from .base import HttpClient, UrllibHttpClient, fetch_json, source_timeout


class AshbyJobSource:
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self.http = http_client or UrllibHttpClient()

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        if source.source_type is not JobSourceType.ASHBY:
            raise ValueError("AshbyJobSource requires an ashby CompanySource")
        include_compensation = bool(source.filters.get("include_compensation", True))
        query = urlencode({"includeCompensation": str(include_compensation).lower()})
        board = quote(source.source_identifier, safe="")
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}?{query}"
        payload = fetch_json(self.http, url, timeout=source_timeout(source))
        jobs: list[DiscoveredJob] = []
        include_unlisted = bool(source.filters.get("include_unlisted", False))
        seen_at = utc_now_iso()
        for item in payload.get("jobs", []):
            if item.get("isListed") is False and not include_unlisted:
                continue
            location = normalize_whitespace(item.get("location"))
            secondary = normalize_string_list(item.get("secondaryLocations"))
            locations = normalize_string_list((location, *secondary))
            compensation = item.get("compensation") or {}
            minimum, maximum, currency, interval = _compensation_values(compensation)
            summary = normalize_whitespace(
                compensation.get("scrapeableCompensationSalarySummary")
                or compensation.get("compensationTierSummary")
            )
            canonical_url = canonicalize_url(item.get("jobUrl") or "")
            if not canonical_url:
                continue
            external_job_id = str(item.get("id") or item.get("jobPostingId") or canonical_url)
            jobs.append(
                DiscoveredJob(
                    id=discovered_job_id(source.owner_id, source.id, external_job_id),
                    owner_id=source.owner_id,
                    source_id=source.id,
                    external_job_id=external_job_id,
                    company=source.company_name,
                    title=normalize_whitespace(item.get("title")),
                    location=location,
                    locations=locations,
                    workplace_type=normalize_workplace_type(
                        item.get("workplaceType"), location=location, is_remote=item.get("isRemote")
                    ),
                    employment_type=normalize_employment_type(item.get("employmentType")),
                    salary_text=format_salary_text(minimum, maximum, currency, interval, summary),
                    description=normalize_whitespace(item.get("descriptionPlain")),
                    canonical_url=canonical_url,
                    apply_url=canonicalize_url(item.get("applyUrl") or canonical_url),
                    posted_at=normalize_iso_timestamp(parse_datetime(item.get("publishedAt"))),
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_type=source.source_type,
                    department=normalize_whitespace(item.get("department")),
                    team=normalize_whitespace(item.get("team")),
                    salary_min=minimum,
                    salary_max=maximum,
                    salary_currency=currency,
                    salary_interval=interval,
                    metadata={"is_listed": item.get("isListed"), "address": item.get("address")},
                )
            )
        return jobs


def _compensation_values(compensation: dict) -> tuple[float | None, float | None, str, str]:
    tiers = compensation.get("compensationTiers") or []
    for tier in tiers:
        components = tier.get("components") or tier.get("summaryComponents") or []
        for component in components:
            minimum = parse_number(component.get("minValue") or component.get("min"))
            maximum = parse_number(component.get("maxValue") or component.get("max"))
            if minimum is not None or maximum is not None:
                return (
                    minimum,
                    maximum,
                    normalize_whitespace(component.get("currencyCode") or component.get("currency")),
                    normalize_whitespace(component.get("interval") or component.get("unit")),
                )
    return None, None, "", ""
