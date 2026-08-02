from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import CompanySource, DiscoveredJob, JobSourceType
from ..normalization import canonicalize_url
from .base import CompanyRateLimiter, HttpClient
from .public_portal import PortalProfile, PublicPortalJobSource, portal_target


_STANDARD_HOST_SUFFIX = ".eightfold.ai"
_VANITY_LISTING_SEGMENTS = {"careers", "jobs"}
_JOB_QUERY_NAMES = {"pid", "positionid", "jobid", "job", "id"}
_PAGINATION_QUERY_NAMES = {"start", "offset", "page", "p"}

_PROFILE = PortalProfile(
    source_type=JobSourceType.EIGHTFOLD,
    platform_name="Eightfold",
    job_url_patterns=(
        re.compile(r"/careers/(?:job|jobs|position)/", re.IGNORECASE),
        re.compile(r"/careers\?.*(?:pid|positionid|jobid)=", re.IGNORECASE),
        # Eightfold vanity domains commonly expose public details as
        # /jobs/<numeric-or-mixed id>, optionally below a locale prefix.
        # Requiring at least one digit avoids treating /jobs/categories and
        # /jobs/locations as postings.
        re.compile(
            r"/(?:jobs?|positions?)/(?:[^/?#]+/)*[a-z0-9_-]*\d[a-z0-9_-]*/?(?:\?.*)?$",
            re.IGNORECASE,
        ),
    ),
)


def _is_standard_eightfold_host(host: str) -> bool:
    normalized = str(host or "").casefold().rstrip(".")
    return normalized == "eightfold.ai" or normalized.endswith(_STANDARD_HOST_SUFFIX)


def _vanity_listing_path(path: str) -> str:
    segments = [segment for segment in str(path or "").split("/") if segment]
    for index, segment in enumerate(segments):
        if segment.casefold() in _VANITY_LISTING_SEGMENTS:
            return "/" + "/".join(segments[: index + 1])
    raise ValueError(
        "Eightfold vanity-domain careers_url must include a public /jobs or /careers path"
    )


def parse_eightfold_careers_url(value: str):
    """Normalize standard and employer-owned Eightfold career-site URLs.

    Standard ``*.eightfold.ai`` URLs keep the tenant ``domain`` selector and
    normalize to ``/careers?start=0``. Employer-owned vanity sites, such as
    ``careers.costco.com/jobs``, keep their exact hostname and visible locale
    prefix while job, category, and location URLs normalize to the site's
    public ``/jobs`` or ``/careers`` catalog. All later requests remain limited
    to that exact configured hostname.
    """

    target = portal_target(value)
    parsed = urlsplit(target.listing_url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    query_items = parse_qsl(parsed.query, keep_blank_values=True)

    if _is_standard_eightfold_host(host):
        query = dict(query_items)
        path = parsed.path or "/careers"
        lower = path.casefold()
        if "/careers" in lower:
            path = path[: lower.index("/careers") + len("/careers")]
        else:
            path = "/careers"
        for name in tuple(query):
            if name.casefold() in _JOB_QUERY_NAMES:
                query.pop(name, None)
        query.setdefault("start", "0")
        listing = canonicalize_url(
            urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))
        )
        return type(target)(listing, target.allowed_domains)

    path = _vanity_listing_path(parsed.path)
    preserved_query = [
        (name, item)
        for name, item in query_items
        if name.casefold() not in _JOB_QUERY_NAMES | _PAGINATION_QUERY_NAMES
    ]
    listing = canonicalize_url(
        urlunsplit(
            (parsed.scheme, parsed.netloc, path, urlencode(preserved_query), "")
        )
    )
    return type(target)(listing, target.allowed_domains)


class EightfoldJobSource:
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
            source, parse_eightfold_careers_url(source.careers_url)
        )

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        return self.portal.fetch_job_description(
            job, parse_eightfold_careers_url(job.canonical_url)
        )
