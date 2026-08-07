from __future__ import annotations

from .common import (
    bounded_int as _bounded_int,
    bounded_float as _bounded_float,
)

import os
import re
from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import canonicalize_url, html_to_text, normalize_whitespace
from .base import CompanyRateLimiter, HttpClient, SourceFetchError
from .indexed_search import IndexedPostingSearch, OpenAIIndexedPostingSearch
from .public_portal import PortalProfile, PublicPortalJobSource, portal_target


_PROFILE = PortalProfile(
    source_type=JobSourceType.TALEMETRY_TTC,
    platform_name="Talemetry / TTC Portals",
    job_url_patterns=(
        # Typical TalentTech/Talemetry Career Sites detail URL:
        # /jobs/17599619-program-manager-iii-insurance-services
        re.compile(
            r"/jobs/(?!search(?:/|$))\d{3,}(?:-[^/?#]+)?(?:[/?#]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"/jobs/(?!search(?:/|$))[^/?#]+-\d{3,}(?:[/?#]|$)",
            re.IGNORECASE,
        ),
    ),
    allowed_host_suffixes=(".ttcportals.com",),
)

_FIRST_TECH_TTC_HOST = "firsttechfedcareers.ttcportals.com"
_FIRST_TECH_SYNDICATION_URL = (
    "https://jobs.partnersindiversity.org/employerjobs/ydcr/"
    "first-tech-federal-credit-union"
)
_FIRST_TECH_SYNDICATION_PROFILE = PortalProfile(
    # The saved company source remains Talemetry/TTC. This secondary profile is
    # only an allow-listed read-only syndication fallback for that exact source.
    source_type=JobSourceType.TALEMETRY_TTC,
    platform_name="Partners in Diversity employer syndication",
    job_url_patterns=(
        re.compile(r"/job/[A-Za-z0-9_-]{4,}/[^?#]+", re.IGNORECASE),
    ),
    allowed_host_suffixes=(".partnersindiversity.org",),
)


def parse_talemetry_ttc_careers_url(value: str):
    """Normalize a public Talemetry/TTC URL to the unfiltered jobs listing."""

    target = portal_target(value, allowed_host_suffixes=_PROFILE.allowed_host_suffixes)
    parsed = urlsplit(target.listing_url)
    path = parsed.path or "/"
    lower = path.casefold().rstrip("/")

    # Both /search/jobs and /jobs/search are observed on public TTC portals.
    # Keep an explicitly supplied listing convention; normalize roots, filtered
    # listings, and job-detail URLs to the broadly supported /search/jobs form.
    if lower == "/jobs/search":
        listing_path = "/jobs/search"
    elif lower == "/search/jobs":
        listing_path = "/search/jobs"
    else:
        listing_path = "/search/jobs"

    listing = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, listing_path, "", ""))
    )
    return type(target)(listing, target.allowed_domains)


class TalemetryTtcJobSource:
    """Collect public Talemetry/TTC postings without relying on blocked HTML.

    The connector uses the platform's paged JSON listing route first and enriches
    a bounded number of jobs from their official detail pages. For First Tech's
    specifically blocked tenant, it next uses an allow-listed employer syndication
    page that exposes the same public openings without an AI search dependency.
    Other blocked TTC tenants retain the exact-domain hosted-index fallback.
    Fallback scans are marked partial so a transient secondary-source gap never
    deactivates previously collected jobs.
    """

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
        indexed_search: IndexedPostingSearch | None = None,
    ) -> None:
        self.portal = PublicPortalJobSource(
            _PROFILE, http_client=http_client, rate_limiter=rate_limiter
        )
        self.syndicated_portal = PublicPortalJobSource(
            _FIRST_TECH_SYNDICATION_PROFILE,
            http_client=http_client,
            rate_limiter=rate_limiter,
        )
        self.indexed_search = indexed_search or OpenAIIndexedPostingSearch()

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        target = parse_talemetry_ttc_careers_url(source.careers_url)
        effective_source = _with_ttc_defaults(source)
        try:
            return self._fetch_json_listing(effective_source, target)
        except SourceFetchError as feed_error:
            if _is_http_403(feed_error):
                return self._fetch_blocked_listing_fallback(
                    effective_source,
                    target,
                    blocked=feed_error,
                )

            # Older or customized tenants may not expose the JSON envelope. Keep
            # the HTML parser as a compatibility fallback for those boards.
            try:
                return self.portal.fetch_jobs(effective_source, target)
            except SourceFetchError as html_error:
                if _is_http_403(html_error):
                    return self._fetch_blocked_listing_fallback(
                        effective_source,
                        target,
                        blocked=html_error,
                    )
                raise feed_error from html_error

    def _fetch_json_listing(self, source: CompanySource, target) -> list[DiscoveredJob]:
        parsed = urlsplit(target.listing_url)
        base_url = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        feed_path = "/search/jobs.json"
        max_pages = _bounded_int(source.filters.get("max_pages"), 50, 1, 50)
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)

        urls: list[str] = []
        records_by_url: dict[str, dict[str, Any]] = {}
        page = 1
        while page <= max_pages and len(urls) < max_jobs:
            feed_url = canonicalize_url(
                urlunsplit(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        feed_path,
                        urlencode({"page": page}),
                        "",
                    )
                )
            )
            payload = self.portal.fetch_json_document(
                source,
                feed_url,
                target.allowed_domains,
                headers={
                    "Accept": "application/json",
                    "Referer": f"{base_url}/jobs",
                },
            )
            if not isinstance(payload, Mapping):
                raise SourceFetchError(
                    f"GET {feed_url} returned an invalid Talemetry/TTC listing envelope"
                )
            entries = payload.get("entries") or []
            if not isinstance(entries, list):
                raise SourceFetchError(
                    f"GET {feed_url} returned invalid Talemetry/TTC entries"
                )

            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                external_id = normalize_whitespace(
                    entry.get("talemetry_job_id") or entry.get("id")
                )
                title = normalize_whitespace(entry.get("title"))
                permalink = normalize_whitespace(
                    entry.get("permalink") or entry.get("url")
                )
                if not external_id or not title:
                    continue
                if not permalink:
                    permalink = f"/jobs/{external_id}"
                detail_url = canonicalize_url(urljoin(f"{base_url}/", permalink))
                detail_host = (urlsplit(detail_url).hostname or "").casefold()
                if detail_host != (parsed.hostname or "").casefold():
                    continue
                if not any(
                    pattern.search(urlsplit(detail_url).path or "/")
                    for pattern in _PROFILE.job_url_patterns
                ):
                    continue
                if detail_url in records_by_url:
                    continue
                urls.append(detail_url)
                records_by_url[detail_url] = {
                    "external_id": external_id,
                    "title": title,
                    "location": _listing_location(entry.get("location")),
                    "posted_at": entry.get("date_posted")
                    or entry.get("posted_at")
                    or entry.get("datePosted"),
                    "department": entry.get("category")
                    or entry.get("department"),
                    "metadata": {
                        "listing_source": "talemetry_json",
                        "talemetry_row_id": normalize_whitespace(entry.get("id")),
                    },
                }
                if len(urls) >= max_jobs:
                    break

            current_page = _bounded_int(payload.get("current_page"), page, 1, 100000)
            per_page = _bounded_int(payload.get("per_page"), len(entries) or 1, 1, 10000)
            total_entries = _bounded_int(
                payload.get("total_entries"),
                (current_page - 1) * per_page + len(entries),
                0,
                10_000_000,
            )
            seen = (current_page - 1) * per_page + len(entries)
            if not entries or seen >= total_entries:
                break
            page += 1

        if not urls:
            raise SourceFetchError(
                f"GET {base_url}{feed_path} returned no public Talemetry/TTC jobs"
            )
        return self.portal.fetch_known_job_urls(
            source,
            target,
            urls,
            records_by_url=records_by_url,
        )

    def _fetch_blocked_listing_fallback(
        self,
        source: CompanySource,
        target,
        *,
        blocked: SourceFetchError,
    ) -> list[DiscoveredJob]:
        """Use a deterministic employer feed before any hosted-index search.

        First Tech's TTC tenant blocks server-side listing requests, while its
        employer-authorized Partners in Diversity page exposes the same current
        postings as ordinary public HTML. Reading that allow-listed page avoids
        making an OpenAI web-search timeout part of the normal refresh path.
        Other TTC tenants retain the existing exact-domain indexed fallback.
        """

        if _is_first_tech_target(target) and _syndicated_fallback_enabled(source):
            try:
                return self._fetch_first_tech_syndication(source, target)
            except SourceFetchError as syndication_error:
                try:
                    return self._fetch_indexed_fallback(
                        source,
                        target,
                        blocked=blocked,
                    )
                except SourceFetchError as indexed_error:
                    raise SourceFetchError(
                        f"{blocked}. First Tech's verified syndication fallback "
                        f"also failed: {syndication_error}. {indexed_error}"
                    ) from indexed_error
        return self._fetch_indexed_fallback(source, target, blocked=blocked)

    def _fetch_first_tech_syndication(
        self,
        source: CompanySource,
        official_target,
    ) -> list[DiscoveredJob]:
        fallback_target = portal_target(
            _FIRST_TECH_SYNDICATION_URL,
            allowed_host_suffixes=_FIRST_TECH_SYNDICATION_PROFILE.allowed_host_suffixes,
        )
        filters = dict(source.filters)
        filters["max_pages"] = _bounded_int(
            filters.get("syndicated_max_pages"), 5, 1, 10
        )
        filters["detail_fetch_limit"] = _bounded_int(
            filters.get("syndicated_detail_fetch_limit"), 10, 0, 50
        )
        # The employer page contains listing summaries for every role. A modest
        # detail cap keeps an admin scan bounded while on-demand enrichment can
        # retrieve any remaining full descriptions later.
        filters["min_request_interval_seconds"] = _bounded_float(
            filters.get("syndicated_min_request_interval_seconds"),
            0.25,
            0.0,
            5.0,
        )
        fallback_source = replace(source, filters=filters)
        jobs = self.syndicated_portal.fetch_jobs(
            fallback_source,
            fallback_target,
        )
        if not jobs:
            raise SourceFetchError(
                "The verified Partners in Diversity employer page returned no "
                "First Tech postings"
            )
        return [
            replace(
                job,
                metadata={
                    **dict(job.metadata),
                    "discovery_mode": "verified_employer_syndication",
                    "official_careers_url": official_target.listing_url,
                    "syndication_url": _FIRST_TECH_SYNDICATION_URL,
                    "listing_route_http_status": 403,
                    # The syndication page can lag the ATS briefly. Preserve an
                    # unseen prior posting until normal age policy removes it.
                    "scan_completeness": "partial",
                },
            )
            for job in jobs
        ]

    def _fetch_indexed_fallback(
        self,
        source: CompanySource,
        target,
        *,
        blocked: SourceFetchError,
    ) -> list[DiscoveredJob]:
        if not _indexed_fallback_enabled(source):
            raise blocked

        host = (urlsplit(target.listing_url).hostname or "").casefold()
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"),
            _bounded_int(source.filters.get("max_jobs"), 100, 1, 100),
            1,
            100,
        )
        indexed_limit = _bounded_int(
            source.filters.get("indexed_search_max_results"),
            detail_limit,
            1,
            100,
        )
        try:
            hits = self.indexed_search.find_postings(
                company_name=source.company_name,
                host=host,
                path_pattern=_PROFILE.job_url_patterns[0],
                max_results=indexed_limit,
                index_page_url=target.listing_url,
            )
        except SourceFetchError as exc:
            raise SourceFetchError(
                f"{blocked}. The compliant indexed fallback was unavailable: {exc}"
            ) from exc

        if not hits:
            raise SourceFetchError(
                f"{blocked}. The compliant indexed fallback found no current official posting URLs."
            )

        active_hits = [hit for hit in hits if hit.is_active is not False]
        if not active_hits:
            raise SourceFetchError(
                f"{blocked}. Indexed results were found, but none were confirmed as active."
            )

        records_by_url: dict[str, dict[str, Any]] = {}
        for hit in active_hits:
            records_by_url[hit.url] = {
                "title": hit.title,
                "location": hit.location,
                "posted_at": hit.posted_at,
                "description": hit.description,
                "metadata": {
                    "listing_source": "hosted_search_index",
                    "indexed_active_status": (
                        "confirmed_open" if hit.is_active is True else "unknown"
                    ),
                    "indexed_description_available": bool(hit.description),
                },
            }

        # Do not immediately reopen URLs that the same server path has already
        # demonstrated it cannot read. The hosted search result itself contains
        # enough verified metadata to create a usable partial posting record.
        indexed_source = replace(
            source,
            filters={**dict(source.filters), "detail_fetch_limit": 0},
        )
        jobs = self.portal.fetch_known_job_urls(
            indexed_source,
            target,
            [hit.url for hit in active_hits],
            records_by_url=records_by_url,
        )
        if not jobs:
            raise SourceFetchError(
                f"{blocked}. Indexed posting metadata could not be converted into job records."
            )
        return [
            replace(
                job,
                metadata={
                    **dict(job.metadata),
                    "detail_status": (
                        "indexed" if job.description else "indexed_summary_missing"
                    ),
                    "discovery_mode": "indexed_metadata_fallback",
                    "listing_route_http_status": 403,
                    "scan_completeness": "partial",
                },
            )
            for job in jobs
        ]

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        host = (urlsplit(job.canonical_url).hostname or "").casefold()
        if host == "jobs.partnersindiversity.org":
            target = portal_target(
                job.canonical_url,
                allowed_host_suffixes=(".partnersindiversity.org",),
            )
            # Keep the source configuration on the official TTC host so the
            # Talemetry source-policy validation remains strict, while the exact
            # detail request is constrained to the allow-listed syndication host.
            official_url = normalize_whitespace(
                job.metadata.get("official_careers_url")
            ) or f"https://{_FIRST_TECH_TTC_HOST}/search/jobs"
            detail_source = CompanySource(
                id=f"detail-{job.source_id}",
                owner_id=job.owner_id,
                company_name=job.company,
                careers_url=official_url,
                source_type=JobSourceType.TALEMETRY_TTC,
                source_identifier="",
                filters={
                    "timeout_seconds": 8.0,
                    "max_redirects": 3,
                    "min_request_interval_seconds": 0.0,
                },
            )
            detail = self.syndicated_portal._fetch_detail(  # noqa: SLF001
                detail_source,
                job.canonical_url,
                target.allowed_domains,
            )
            return html_to_text(detail.get("description"))
        return self.portal.fetch_job_description(
            job, parse_talemetry_ttc_careers_url(job.canonical_url)
        )

    @staticmethod
    def scan_is_complete(
        source: CompanySource, jobs: list[DiscoveredJob]
    ) -> bool:
        del source
        return not any(
            job.metadata.get("discovery_mode")
            in {
                "indexed_fallback",
                "indexed_metadata_fallback",
                "verified_employer_syndication",
            }
            for job in jobs
        )


def _with_ttc_defaults(source: CompanySource) -> CompanySource:
    filters = dict(source.filters)
    filters.setdefault("min_request_interval_seconds", 1.0)
    filters.setdefault("detail_fetch_limit", 10)
    filters.setdefault("max_pages", 50)
    return replace(source, filters=filters)


def _listing_location(value: object) -> str:
    if isinstance(value, Mapping):
        parts = [
            value.get("city"),
            value.get("state") or value.get("region"),
            value.get("country"),
        ]
        return ", ".join(
            part for part in (normalize_whitespace(item) for item in parts) if part
        )
    if isinstance(value, (list, tuple)):
        return ", ".join(
            part for part in (normalize_whitespace(item) for item in value) if part
        )
    return normalize_whitespace(value)


def _is_http_403(exc: SourceFetchError) -> bool:
    return bool(re.search(r"\bHTTP\s+403\b", str(exc), re.IGNORECASE))


def _is_first_tech_target(target) -> bool:
    return (
        (urlsplit(target.listing_url).hostname or "").casefold()
        == _FIRST_TECH_TTC_HOST
    )


def _syndicated_fallback_enabled(source: CompanySource) -> bool:
    value = source.filters.get("verified_syndication_fallback")
    if value is None:
        value = os.getenv("JOB_DISCOVERY_VERIFIED_SYNDICATION_FALLBACK", "true")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _indexed_fallback_enabled(source: CompanySource) -> bool:
    value = source.filters.get("indexed_search_fallback")
    if value is None:
        value = os.getenv("JOB_DISCOVERY_INDEXED_SEARCH_FALLBACK", "true")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }
