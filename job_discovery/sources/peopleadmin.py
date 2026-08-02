from __future__ import annotations

import os
import re
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import canonicalize_url
from .base import CompanyRateLimiter, HttpClient, RobotsDeniedError, SourceFetchError
from .indexed_search import IndexedPostingSearch, OpenAIIndexedPostingSearch
from .public_portal import PortalProfile, PublicPortalJobSource, portal_target


_PROFILE = PortalProfile(
    source_type=JobSourceType.PEOPLEADMIN,
    platform_name="PeopleAdmin",
    job_url_patterns=(
        # PeopleAdmin HigherEd job detail pages use /postings/<numeric id>.
        # Keep the match anchored so /postings/<id>/pre_apply is not treated as
        # a second posting.
        re.compile(r"/postings/\d+/?(?:\?.*)?$", re.IGNORECASE),
    ),
)


def parse_peopleadmin_careers_url(value: str):
    """Normalize a public PeopleAdmin board or posting URL to /postings/search.

    PeopleAdmin boards may use a vendor hostname such as
    ``institution.peopleadmin.com`` or an institution-owned vanity hostname,
    such as Portland State's ``jobs.hrc.pdx.edu``. Fetches remain restricted to
    the exact configured hostname by :func:`portal_target`.
    """

    target = portal_target(value)
    parsed = urlsplit(target.listing_url)
    path = parsed.path or "/"
    match = re.search(r"/postings(?:/|$)", path, re.IGNORECASE)
    if match:
        prefix = path[: match.start()].rstrip("/")
    else:
        # A root or branded landing-page URL still resolves to the standard
        # public PeopleAdmin search endpoint on the same host.
        prefix = ""
    listing_path = f"{prefix}/postings/search" or "/postings/search"
    listing = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, listing_path, "", ""))
    )
    return type(target)(listing, target.allowed_domains)


class PeopleAdminJobSource:
    """Collect public PeopleAdmin HigherEd postings and detail pages.

    Some institutions block the PeopleAdmin listing route while permitting
    individual public posting pages. In that case, this connector may use a
    domain-restricted hosted search index to discover exact posting URLs. It
    then fetches only those detail pages through the normal robots-aware portal
    client. This is a partial scan and never bypasses the blocked listing path.
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
        self.indexed_search = indexed_search or OpenAIIndexedPostingSearch()

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        target = parse_peopleadmin_careers_url(source.careers_url)
        try:
            return self.portal.fetch_jobs(source, target)
        except RobotsDeniedError as denied:
            if not _indexed_fallback_enabled(source):
                raise

            host = (urlsplit(target.listing_url).hostname or "").casefold()
            detail_limit = _bounded_int(
                source.filters.get("detail_fetch_limit"),
                _bounded_int(source.filters.get("max_jobs"), 50, 1, 100),
                1,
                100,
            )
            # Hosted web search is intentionally partial. Asking for every role on
            # a large university board makes the provider request slow and brittle;
            # scheduled scans may opt into a larger explicit value.
            indexed_limit = _bounded_int(
                source.filters.get("indexed_search_max_results"),
                min(detail_limit, 25),
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
                raise RobotsDeniedError(
                    f"{denied}. The compliant indexed fallback was unavailable: {exc}"
                ) from exc

            if not hits:
                raise RobotsDeniedError(
                    f"{denied}. The compliant indexed fallback found no current official posting URLs."
                )

            active_hits = [hit for hit in hits if hit.is_active is not False]
            if not active_hits:
                raise RobotsDeniedError(
                    f"{denied}. Indexed results were found, but none were confirmed as active."
                )

            records_by_url = {
                hit.url: {
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
                for hit in active_hits
            }
            # Preserve indexed metadata before attempting any allowed detail-page
            # enrichment. A temporary detail failure must not discard an otherwise
            # valid official posting discovered through the compliant index.
            jobs = self.portal.fetch_known_job_urls(
                source,
                target,
                [hit.url for hit in active_hits],
                records_by_url=records_by_url,
            )
            if not jobs:
                raise RobotsDeniedError(
                    f"{denied}. Indexed posting metadata could not be converted into job records."
                )
            return [
                replace(
                    job,
                    metadata={
                        **dict(job.metadata),
                        "discovery_mode": "indexed_metadata_fallback",
                        "listing_route_blocked_by_robots": True,
                        "scan_completeness": "partial",
                    },
                )
                for job in jobs
            ]

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        return self.portal.fetch_job_description(
            job, parse_peopleadmin_careers_url(job.canonical_url)
        )

    @staticmethod
    def scan_is_complete(
        source: CompanySource, jobs: list[DiscoveredJob]
    ) -> bool:
        del source
        return not any(
            job.metadata.get("listing_route_blocked_by_robots") is True
            or job.metadata.get("discovery_mode") in {
                "indexed_fallback",
                "indexed_metadata_fallback",
            }
            for job in jobs
        )


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


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
