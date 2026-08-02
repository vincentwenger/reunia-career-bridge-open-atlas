from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import canonicalize_url
from .base import CompanyRateLimiter, HttpClient
from .public_portal import PortalProfile, PublicPortalJobSource, portal_target


_PROFILE = PortalProfile(
    source_type=JobSourceType.TALEO,
    platform_name="Oracle Taleo",
    job_url_patterns=(
        re.compile(r"/careersection/.*/jobdetail\.ftl(?:\?|$)", re.IGNORECASE),
        re.compile(r"/careersection/.*/jobapply\.ftl\?.*\bjob=", re.IGNORECASE),
    ),
    allowed_host_suffixes=(".taleo.net",),
)


def parse_taleo_careers_url(value: str):
    target = portal_target(value, allowed_host_suffixes=_PROFILE.allowed_host_suffixes)
    parsed = urlsplit(target.listing_url)
    path = parsed.path or "/"
    lower = path.casefold()
    match = re.search(r"(/careersection/[^/]+/)", path, re.IGNORECASE)
    if not match:
        raise ValueError("Taleo URL must contain /careersection/<career-section>/")
    root = match.group(1)
    if lower.endswith("jobsearch.ftl") or lower.endswith("joblist.ftl"):
        listing_path = path
    else:
        listing_path = root + "jobsearch.ftl"
    listing = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, listing_path, "", ""))
    )
    return type(target)(listing, target.allowed_domains)


class TaleoJobSource:
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
        return self.portal.fetch_jobs(source, parse_taleo_careers_url(source.careers_url))

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        return self.portal.fetch_job_description(job, parse_taleo_careers_url(job.canonical_url))
