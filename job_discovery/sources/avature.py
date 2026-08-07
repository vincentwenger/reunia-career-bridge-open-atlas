from __future__ import annotations

from .common import (
    bounded_int as _bounded_int,
)

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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
from .base import CompanyRateLimiter, HttpClient, SourceFetchError, validate_source_policy
from .public_portal import (
    PortalProfile,
    PortalTarget,
    PublicPortalJobSource,
    _external_id_from_url,
    _has_complete_detail,
    _merge_record,
    portal_target,
)


_PROFILE = PortalProfile(
    source_type=JobSourceType.AVATURE,
    platform_name="Avature",
    job_url_patterns=(
        re.compile(r"/JobDetail(?:/|\?)", re.IGNORECASE),
        re.compile(r"[?&]jobId=", re.IGNORECASE),
    ),
    allowed_host_suffixes=(".avature.net",),
)


@dataclass(frozen=True, slots=True)
class AvatureTarget:
    listing_url: str
    feed_url: str
    allowed_domains: tuple[str, ...]


def parse_avature_careers_url(value: str) -> AvatureTarget:
    base = portal_target(value, allowed_host_suffixes=_PROFILE.allowed_host_suffixes)
    parsed = urlsplit(base.listing_url)
    path = parsed.path or "/"
    match = re.search(r"/(?:SearchJobs|JobDetail|Dashboard)(?:/|$)", path, re.IGNORECASE)
    if match:
        root = path[: match.start()]
    else:
        root = path.rstrip("/")
    if not root:
        raise ValueError("Avature URL must include the public career-site path")
    listing_path = root.rstrip("/") + "/SearchJobs"
    listing = canonicalize_url(urlunsplit((parsed.scheme, parsed.netloc, listing_path, "", "")))
    feed = canonicalize_url(
        urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                listing_path.rstrip("/") + "/feed/",
                urlencode({"jobRecordsPerPage": 100, "jobOffset": 0}),
                "",
            )
        )
    )
    return AvatureTarget(listing, feed, base.allowed_domains)


class AvatureJobSource:
    """Collect public Avature jobs from the career-site feed with HTML fallback."""

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
        clock=time.monotonic,
    ) -> None:
        self.portal = PublicPortalJobSource(
            _PROFILE, http_client=http_client, rate_limiter=rate_limiter, clock=clock
        )
        self.clock = clock

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        validate_source_policy(source, expected_type=JobSourceType.AVATURE)
        target = parse_avature_careers_url(source.careers_url)
        max_pages = _bounded_int(source.filters.get("max_pages"), 20, 1, 50)
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"), max_jobs, 0, max_jobs
        )
        page_size = min(100, max_jobs)
        records: dict[str, dict[str, Any]] = {}
        for page in range(max_pages):
            feed_url = _feed_page_url(target.feed_url, page * page_size, page_size)
            try:
                body = self.portal._fetch_html(source, feed_url, target.allowed_domains)
            except SourceFetchError:
                if page == 0:
                    return self.portal.fetch_jobs(
                        source, PortalTarget(target.listing_url, target.allowed_domains)
                    )
                break
            page_records = _parse_feed(body, feed_url)
            if not page_records:
                if page == 0:
                    return self.portal.fetch_jobs(
                        source, PortalTarget(target.listing_url, target.allowed_domains)
                    )
                break
            for record in page_records:
                key = normalize_whitespace(record.get("external_id")) or record["canonical_url"]
                records[key] = _merge_record(records.get(key, {}), record)
                if len(records) >= max_jobs:
                    break
            if len(page_records) < page_size or len(records) >= max_jobs:
                break

        jobs: list[DiscoveredJob] = []
        seen_at = utc_now_iso()
        for index, record in enumerate(records.values()):
            detail = dict(record)
            detail_url = canonicalize_url(detail.get("canonical_url"))
            if index < detail_limit and detail_url and not _has_complete_detail(detail):
                try:
                    fetched = self.portal._fetch_detail(source, detail_url, target.allowed_domains)
                except SourceFetchError as exc:
                    detail["detail_error"] = str(exc)
                else:
                    detail = _merge_record(detail, fetched)
            external_id = normalize_whitespace(detail.get("external_id")) or _external_id_from_url(detail_url)
            title = normalize_whitespace(detail.get("title"))
            if not title or not detail_url:
                continue
            description = html_to_text(detail.get("description")) or normalize_whitespace(
                detail.get("listing_context")
            )
            location = normalize_whitespace(detail.get("location"))
            metadata = {
                "portal_platform": "Avature",
                "detail_status": (
                    "failed"
                    if detail.get("detail_error")
                    else "fetched"
                    if index < detail_limit
                    else "deferred"
                ),
            }
            if detail.get("detail_error"):
                metadata["detail_error"] = normalize_whitespace(detail["detail_error"])[:1000]
            jobs.append(
                DiscoveredJob(
                    id=discovered_job_id(source.owner_id, source.id, external_id),
                    owner_id=source.owner_id,
                    source_id=source.id,
                    external_job_id=external_id,
                    company=source.company_name,
                    title=title,
                    location=location,
                    locations=normalize_string_list(detail.get("locations") or location),
                    workplace_type=normalize_workplace_type(
                        detail.get("workplace_type"), location=location
                    ),
                    employment_type=normalize_employment_type(detail.get("employment_type")),
                    description=description,
                    canonical_url=detail_url,
                    apply_url=canonicalize_url(detail.get("apply_url") or detail_url),
                    posted_at=normalize_iso_timestamp(parse_datetime(detail.get("posted_at"))),
                    valid_through=normalize_iso_timestamp(parse_datetime(detail.get("valid_through"))),
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_type=source.source_type,
                    department=normalize_whitespace(detail.get("department")),
                    skills=normalize_string_list(detail.get("skills")),
                    metadata=metadata,
                )
            )
        return jobs

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        target = parse_avature_careers_url(job.canonical_url)
        return self.portal.fetch_job_description(
            job, PortalTarget(target.listing_url, target.allowed_domains)
        )


def _feed_page_url(url: str, offset: int, limit: int) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"jobRecordsPerPage": str(limit), "jobOffset": str(offset)})
    return canonicalize_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")))


def _parse_feed(body: str, page_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        root = None
    if root is not None:
        for item in root.findall(".//item"):
            title = normalize_whitespace(item.findtext("title"))
            link = normalize_whitespace(item.findtext("link") or item.findtext("guid"))
            if not title or not link:
                continue
            posted = normalize_whitespace(item.findtext("pubDate"))
            try:
                posted_value = parsedate_to_datetime(posted).isoformat() if posted else ""
            except (TypeError, ValueError, OverflowError):
                posted_value = posted
            records.append(
                {
                    "external_id": _external_id_from_url(link),
                    "title": title,
                    "canonical_url": canonicalize_url(urljoin(page_url, link)),
                    "description": html_to_text(item.findtext("description")),
                    "posted_at": posted_value,
                }
            )
        if records:
            return records
    for match in re.finditer(r"https?://[^\s<>'\"]+/JobDetail(?:/[^\s<>'\"]+|\?[^\s<>'\"]+)", body, re.IGNORECASE):
        link = canonicalize_url(match.group(0))
        prefix = html_to_text(body[max(0, match.start() - 220) : match.start()])
        title = normalize_whitespace(prefix.split("\n")[-1] if prefix else "")
        records.append(
            {
                "external_id": _external_id_from_url(link),
                "title": title or f"Job {_external_id_from_url(link)}",
                "canonical_url": link,
            }
        )
    return records
