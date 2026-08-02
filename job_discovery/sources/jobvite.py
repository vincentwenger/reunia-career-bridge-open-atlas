from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from ..models import CompanySource, DiscoveredJob, JobSourceType, discovered_job_id
from ..normalization import canonicalize_url
from .base import CompanyRateLimiter, HttpClient, SourceFetchError
from .public_portal import PortalProfile, PublicPortalJobSource, portal_target


_JOBVITE_PUBLIC_HOST = "jobs.jobvite.com"
_JOBVITE_SEARCH_HOST = "search.jobvite.com"
_JOBVITE_SHARED_HOST = "www.jobvite.com"
# Exact Jobvite-owned hosts observed in the hosted-careers redirect chain.  Do
# not replace this with a broad ``.jobvite.com`` suffix: customer-controlled
# subdomains must remain outside the crawler boundary.
_JOBVITE_FETCH_HOSTS = (
    _JOBVITE_PUBLIC_HOST,
    _JOBVITE_SEARCH_HOST,
    _JOBVITE_SHARED_HOST,
)


_PROFILE = PortalProfile(
    source_type=JobSourceType.JOBVITE,
    platform_name="Jobvite",
    job_url_patterns=(
        # Hosted Jobvite career sites expose public detail pages as
        # /<career-site>/job/<opaque-job-id>. Some boards also render an
        # /apply suffix, which is normalized back to the canonical job page.
        re.compile(
            r"/[^/?#]+/job/[A-Za-z0-9_-]+(?:/apply)?/?(?:\?.*)?$",
            re.IGNORECASE,
        ),
    ),
    allowed_host_suffixes=(_JOBVITE_PUBLIC_HOST,),
)


def _public_jobvite_url(value: str) -> str:
    """Return the stable public Jobvite URL for either sanctioned host.

    Jobvite sometimes redirects hosted-careers requests to the platform-owned
    ``search.jobvite.com`` host and shared policy/static requests to
    ``www.jobvite.com``. These exact hosts are safe fetch targets for this
    connector, but persisted source and posting URLs remain on
    ``jobs.jobvite.com`` so catalog identity does not change after a redirect.
    """

    canonical = canonicalize_url(value)
    parsed = urlsplit(canonical)
    host = (parsed.hostname or "").casefold()
    if host not in _JOBVITE_FETCH_HOSTS:
        expected = " or ".join(_JOBVITE_FETCH_HOSTS)
        raise ValueError(f"Jobvite URL must use {expected}")
    if host == _JOBVITE_PUBLIC_HOST:
        return canonical
    return canonicalize_url(
        urlunsplit(
            (parsed.scheme, _JOBVITE_PUBLIC_HOST, parsed.path, parsed.query, "")
        )
    )


def parse_jobvite_careers_url(value: str):
    """Normalize a hosted Jobvite board or job URL to its public search page."""

    target = portal_target(
        _public_jobvite_url(value),
        allowed_host_suffixes=_PROFILE.allowed_host_suffixes,
    )
    parsed = urlsplit(target.listing_url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        raise ValueError("Jobvite careers_url must include the career-site name")
    board = segments[0]
    if board.casefold() in {"job", "search", "apply"}:
        raise ValueError("Jobvite careers_url must include the career-site name")
    listing = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, f"/{board}/search", "", ""))
    )
    return type(target)(listing, _JOBVITE_FETCH_HOSTS)


def _jobvite_targets(value: str):
    """Return bounded public Jobvite listing variants in preferred order.

    Jobvite-hosted boards have used both the explicit ``/search`` route and
    the board root over time. Trying these sanctioned variants avoids coupling
    discovery to one presentation route while remaining on the exact Jobvite
    host and within the normal robots/rate-limit checks.
    """

    canonical = parse_jobvite_careers_url(value)
    parsed = urlsplit(canonical.listing_url)
    board = [segment for segment in parsed.path.split("/") if segment][0]
    candidates = (
        # Current Jobvite-hosted boards expose their complete public listing at
        # /<career-site>/jobs. Prefer that route because legacy /search and
        # board-root requests may be redirected to Jobvite's invalid-link
        # support page, which can itself enter a redirect loop.
        canonicalize_url(
            urlunsplit((parsed.scheme, parsed.netloc, f"/{board}/jobs", "", ""))
        ),
        canonical.listing_url,
        canonicalize_url(
            urlunsplit((parsed.scheme, parsed.netloc, f"/{board}/search", "p=0", ""))
        ),
        canonicalize_url(
            urlunsplit((parsed.scheme, parsed.netloc, f"/{board}", "", ""))
        ),
    )
    target_type = type(canonical)
    return tuple(
        target_type(url, canonical.allowed_domains)
        for url in dict.fromkeys(candidates)
    )


def _jobvite_job_id(url: str) -> str:
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    for index, segment in enumerate(segments):
        if segment.casefold() == "job" and index + 1 < len(segments):
            return segments[index + 1]
    return ""


def _canonical_job_url(url: str) -> str:
    parsed = urlsplit(_public_jobvite_url(url))
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments and segments[-1].casefold() == "apply":
        segments.pop()
    return canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, "/" + "/".join(segments), "", ""))
    )


class JobviteJobSource:
    """Collect public postings from Jobvite-hosted SEO-friendly career sites."""

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
    ) -> None:
        self.portal = PublicPortalJobSource(
            _PROFILE, http_client=http_client, rate_limiter=rate_limiter
        )

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        last_error: SourceFetchError | None = None
        had_successful_listing = False
        jobs: list[DiscoveredJob] = []
        for target in _jobvite_targets(source.careers_url):
            try:
                jobs = self.portal.fetch_jobs(source, target)
            except SourceFetchError as exc:
                last_error = exc
                continue
            had_successful_listing = True
            if jobs:
                break

        if not jobs and not had_successful_listing and last_error is not None:
            raise last_error

        normalized_by_id: dict[str, DiscoveredJob] = {}
        for job in jobs:
            canonical_url = _canonical_job_url(job.canonical_url)
            external_id = _jobvite_job_id(canonical_url) or job.external_job_id
            normalized = replace(
                job,
                canonical_url=canonical_url,
                apply_url=(
                    _public_jobvite_url(job.apply_url)
                    if "/apply" in urlsplit(job.apply_url).path.casefold()
                    else canonical_url
                ),
                external_job_id=external_id,
                id=discovered_job_id(source.owner_id, source.id, external_id),
            )
            existing = normalized_by_id.get(external_id)
            if existing is None or len(normalized.description) > len(existing.description):
                normalized_by_id[external_id] = normalized
        return list(normalized_by_id.values())

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        return self.portal.fetch_job_description(
            job, parse_jobvite_careers_url(job.canonical_url)
        )
