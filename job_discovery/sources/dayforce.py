from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import canonicalize_url
from .base import CompanyRateLimiter, HttpClient
from .public_portal import PortalProfile, PublicPortalJobSource, portal_target


_PROFILE = PortalProfile(
    source_type=JobSourceType.DAYFORCE,
    platform_name="Dayforce",
    job_url_patterns=(
        re.compile(r"/(?:jobs?|jobdetails?)/[^/?#]*\d+", re.IGNORECASE),
        re.compile(r"[?&](?:jobid|postingid|requisitionid)=", re.IGNORECASE),
    ),
    allowed_host_suffixes=(".dayforcehcm.com",),
)


def parse_dayforce_careers_url(value: str):
    target = portal_target(value, allowed_host_suffixes=_PROFILE.allowed_host_suffixes)
    parsed = urlsplit(target.listing_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for name in ("jobId", "jobid", "postingId", "postingid", "requisitionId"):
        query.pop(name, None)
    listing = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(query), ""))
    )
    return type(target)(listing, target.allowed_domains)


class DayforceJobSource:
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
        return self.portal.fetch_jobs(source, parse_dayforce_careers_url(source.careers_url))

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        return self.portal.fetch_job_description(job, parse_dayforce_careers_url(job.canonical_url))
