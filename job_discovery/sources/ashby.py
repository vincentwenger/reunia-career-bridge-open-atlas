from __future__ import annotations

from urllib.parse import quote, urlencode

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import (
    canonicalize_url,
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
        include_compensation = bool(source.options.get("include_compensation", True))
        query = urlencode({"includeCompensation": str(include_compensation).lower()})
        board = quote(source.identifier, safe="")
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}?{query}"
        payload = fetch_json(self.http, url, timeout=source_timeout(source))
        jobs: list[DiscoveredJob] = []
        include_unlisted = bool(source.options.get("include_unlisted", False))
        for item in payload.get("jobs", []):
            if item.get("isListed") is False and not include_unlisted:
                continue
            location = normalize_whitespace(item.get("location"))
            secondary = normalize_string_list(item.get("secondaryLocations"))
            locations = normalize_string_list((location, *secondary))
            compensation = item.get("compensation") or {}
            salary_min, salary_max, currency, interval = _compensation_values(compensation)
            job_url = canonicalize_url(item.get("jobUrl") or "")
            if not job_url:
                continue
            jobs.append(
                DiscoveredJob(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    external_id=str(item.get("id") or item.get("jobPostingId") or job_url),
                    company=source.company_name,
                    title=normalize_whitespace(item.get("title")),
                    job_url=job_url,
                    apply_url=canonicalize_url(item.get("applyUrl") or job_url),
                    description=normalize_whitespace(item.get("descriptionPlain")),
                    location=location,
                    locations=locations,
                    workplace_type=normalize_workplace_type(
                        item.get("workplaceType"), location=location, is_remote=item.get("isRemote")
                    ),
                    employment_type=normalize_employment_type(item.get("employmentType")),
                    department=normalize_whitespace(item.get("department")),
                    team=normalize_whitespace(item.get("team")),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=currency,
                    salary_interval=interval,
                    salary_summary=normalize_whitespace(
                        compensation.get("scrapeableCompensationSalarySummary")
                        or compensation.get("compensationTierSummary")
                    ),
                    posted_at=parse_datetime(item.get("publishedAt")),
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
