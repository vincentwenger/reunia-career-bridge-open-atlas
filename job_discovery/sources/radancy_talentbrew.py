from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import canonicalize_url
from .base import CompanyRateLimiter, HttpClient
from .public_portal import PortalProfile, PublicPortalJobSource, portal_target


_PROFILE = PortalProfile(
    source_type=JobSourceType.RADANCY_TALENTBREW,
    platform_name="Radancy / TalentBrew",
    job_url_patterns=(
        # TalentBrew/Radancy branded career sites conventionally expose public
        # detail pages as /job/<location>/<slug>/<site-id>/<job-id>. A locale
        # prefix such as /en/job/... is common on multilingual sites.
        re.compile(
            r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?job/.+/\d+/\d+/?(?:\?.*)?$",
            re.IGNORECASE,
        ),
    ),
)

_LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)
_TALENTBREW_ROUTES = {"job", "search-jobs", "location", "category", "business"}


def parse_radancy_talentbrew_careers_url(value: str):
    """Normalize a branded Radancy/TalentBrew URL to its public job search.

    Radancy career sites normally use an employer-owned hostname. Therefore the
    connector cannot validate by a shared vendor suffix; instead all requests
    stay restricted to the exact public hostname supplied by the administrator.
    Root pages, search pages, filtered location/category pages, and job-detail
    URLs normalize to ``/search-jobs`` while preserving a visible locale prefix.
    """

    target = portal_target(value)
    parsed = urlsplit(target.listing_url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    locale = (
        segments[0]
        if segments and _LOCALE_RE.fullmatch(segments[0])
        else ""
    )
    for index, segment in enumerate(segments):
        if segment.casefold() not in _TALENTBREW_ROUTES:
            continue
        if index > 0 and _LOCALE_RE.fullmatch(segments[index - 1]):
            locale = segments[index - 1]
        break
    listing_path = f"/{locale}/search-jobs" if locale else "/search-jobs"
    listing = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, listing_path, "", ""))
    )
    return type(target)(listing, target.allowed_domains)


class RadancyTalentBrewJobSource:
    """Collect public jobs from branded Radancy/TalentBrew career sites."""

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
        return self.portal.fetch_jobs(
            source, parse_radancy_talentbrew_careers_url(source.careers_url)
        )

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        return self.portal.fetch_job_description(
            job, parse_radancy_talentbrew_careers_url(job.canonical_url)
        )
