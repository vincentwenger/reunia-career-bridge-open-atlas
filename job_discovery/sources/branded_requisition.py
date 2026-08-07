from __future__ import annotations

import re
import time
from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import canonicalize_url
from .base import (
    CompanyRateLimiter,
    HttpClient,
    RobotsDeniedError,
    SourceFetchError,
)
from .public_portal import PortalProfile, PublicPortalJobSource, portal_target


_PROFILE = PortalProfile(
    source_type=JobSourceType.BRANDED_REQUISITION,
    platform_name="Branded Requisition Portal",
    job_url_patterns=(
        re.compile(r"/job/\d+/[^/?#]+/?(?:\?.*)?$", re.IGNORECASE),
    ),
)


def _search_selectors(parsed) -> str:
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() != "page"
    ]
    return urlencode(query, doseq=True)


def parse_branded_requisition_careers_url(value: str):
    """Normalize compatible branded portals to the public HTML job listing.

    The human-facing ``/search-jobs`` page is the primary source because it is
    more stable under load than the internal requisition endpoint. Equivalent
    root, API, search, and detail URLs still share one catalog identity.
    """

    target = portal_target(value)
    parsed = urlsplit(target.listing_url)
    source_path = parsed.path.rstrip("/").casefold()
    selectors = (
        _search_selectors(parsed)
        if source_path in {"/search-jobs", "/api/requisitions/search"}
        else ""
    )
    listing = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, "/search-jobs", selectors, ""))
    )
    return type(target)(listing, target.allowed_domains)


def branded_requisition_api_url(value: str):
    """Return the secondary public requisition endpoint for a compatible site."""

    target = portal_target(value)
    parsed = urlsplit(target.listing_url)
    source_path = parsed.path.rstrip("/").casefold()
    selectors = (
        _search_selectors(parsed)
        if source_path in {"/search-jobs", "/api/requisitions/search"}
        else ""
    )
    listing = canonicalize_url(
        urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/requisitions/search", selectors, "")
        )
    )
    return type(target)(listing, target.allowed_domains)


def _bounded_attempts(value: object, default: int = 3) -> int:
    try:
        attempts = int(value)
    except (TypeError, ValueError):
        attempts = default
    return min(max(attempts, 1), 3)


def _bounded_backoff(value: object, default: float = 2.0) -> float:
    try:
        delay = float(value)
    except (TypeError, ValueError):
        delay = default
    return min(max(delay, 0.0), 10.0)


def _is_transient_fetch_error(exc: SourceFetchError) -> bool:
    """Retry only failures that can plausibly recover without policy changes."""

    if isinstance(exc, RobotsDeniedError):
        return False
    message = str(exc).casefold()
    if "robots.txt disallows" in message:
        return False
    status_match = re.search(r"http\s+(\d{3})", message)
    if status_match:
        status = int(status_match.group(1))
        return status in {408, 429} or 500 <= status <= 599
    return any(
        marker in message
        for marker in (
            "timed out",
            "timeout",
            "temporarily unavailable",
            "temporary failure",
            "connection reset",
            "connection aborted",
            "unable to fetch",
        )
    )


class BrandedRequisitionJobSource:
    """Collect jobs from public branded requisition/search portals.

    The visible HTML listing is attempted first. Transient failures are retried
    with a short bounded backoff, after which the public requisition endpoint is
    used as a fallback. A failed scan raises without synchronizing an empty job
    set, so the shared catalog retains the previous successful results.
    """

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
        sleeper=time.sleep,
    ) -> None:
        self.portal = PublicPortalJobSource(
            _PROFILE, http_client=http_client, rate_limiter=rate_limiter
        )
        self.sleeper = sleeper

    def _source_with_resilient_defaults(
        self, source: CompanySource
    ) -> CompanySource:
        filters = dict(source.filters)
        filters.setdefault("timeout_seconds", 30.0)
        filters.setdefault("min_request_interval_seconds", 1.0)
        filters.setdefault("retry_attempts", 3)
        filters.setdefault("retry_backoff_seconds", 2.0)
        return replace(source, filters=filters)

    def _fetch_with_retry(self, source: CompanySource, target) -> list[DiscoveredJob]:
        attempts = _bounded_attempts(source.filters.get("retry_attempts"), 3)
        backoff = _bounded_backoff(
            source.filters.get("retry_backoff_seconds"), 2.0
        )
        last_error: SourceFetchError | None = None
        for attempt in range(attempts):
            try:
                return self.portal.fetch_jobs(source, target)
            except SourceFetchError as exc:
                last_error = exc
                if not _is_transient_fetch_error(exc):
                    break
                if attempt + 1 < attempts and backoff > 0:
                    self.sleeper(backoff * (attempt + 1))
        assert last_error is not None
        raise last_error

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        resilient = self._source_with_resilient_defaults(source)
        primary_error = ""
        try:
            jobs = self._fetch_with_retry(
                resilient,
                parse_branded_requisition_careers_url(source.careers_url),
            )
        except SourceFetchError as exc:
            primary_error = str(exc)
        else:
            if jobs:
                return jobs

        try:
            jobs = self._fetch_with_retry(
                resilient, branded_requisition_api_url(source.careers_url)
            )
        except SourceFetchError as exc:
            fallback_error = str(exc)
            if primary_error:
                raise SourceFetchError(
                    "Public HTML listing failed: "
                    f"{primary_error}; requisition fallback failed: {fallback_error}"
                ) from exc
            raise
        if jobs:
            return jobs
        if primary_error:
            raise SourceFetchError(
                f"Public HTML listing failed: {primary_error}; "
                "requisition fallback returned no jobs"
            )
        return []

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        return self.portal.fetch_job_description(
            job, parse_branded_requisition_careers_url(job.canonical_url)
        )
