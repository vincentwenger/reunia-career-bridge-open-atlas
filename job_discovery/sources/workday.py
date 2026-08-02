from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote, unquote, urlsplit, urlunsplit

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
    normalize_workplace_type,
    parse_datetime,
)
from .base import (
    DEFAULT_JSON_MAX_BYTES,
    CompanyRateLimiter,
    HttpClient,
    SourceFetchError,
    UrllibHttpClient,
    company_rate_limit_key,
    fetch_json,
    fetch_json_post,
    source_min_request_interval,
    source_redirect_limit,
    source_response_limit,
    source_timeout,
    validate_source_policy,
)


_WORKDAY_HOST_SUFFIXES = (".myworkdayjobs.com", ".myworkdaysite.com")
_LOCALE_PATTERN = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)
_POSTED_DAYS_PATTERN = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.IGNORECASE)
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 20
DEFAULT_MAX_JOBS = 500
MAX_MAX_JOBS = 2_000
DEFAULT_MAX_PAGES = 50
MAX_MAX_PAGES = 100
DEFAULT_DETAIL_FETCH_LIMIT = 500
MAX_DETAIL_FETCH_LIMIT = 2_000
DEFAULT_FETCH_BUDGET_SECONDS = 0.0
MAX_FETCH_BUDGET_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class WorkdayTarget:
    origin: str
    host: str
    tenant: str
    site: str
    locale: str

    @property
    def listing_url(self) -> str:
        return (
            f"{self.origin}/wday/cxs/{quote(self.tenant, safe='')}/"
            f"{quote(self.site, safe='')}/jobs"
        )

    @property
    def api_base_url(self) -> str:
        return (
            f"{self.origin}/wday/cxs/{quote(self.tenant, safe='')}/"
            f"{quote(self.site, safe='')}"
        )

    @property
    def careers_url(self) -> str:
        return f"{self.origin}/{quote(self.locale, safe='-')}/{quote(self.site, safe='')}"


def parse_workday_careers_url(
    careers_url: str,
    *,
    site_identifier: str = "",
    locale: str = "",
) -> WorkdayTarget:
    """Parse a public Workday career-site URL into its CXS target values."""

    parsed = urlsplit(str(careers_url or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Workday requires a public career-site URL")
    host = parsed.hostname.casefold().rstrip(".")
    if not host.endswith(_WORKDAY_HOST_SUFFIXES):
        raise ValueError(
            "Workday URL must use a myworkdayjobs.com or myworkdaysite.com host"
        )
    tenant = host.split(".", 1)[0].strip()
    if not tenant:
        raise ValueError("Workday tenant could not be determined from the URL")

    path_parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    detected_locale = ""
    detected_site = ""
    if len(path_parts) >= 4 and path_parts[0].casefold() == "wday" and path_parts[1].casefold() == "cxs":
        tenant = path_parts[2] or tenant
        detected_site = path_parts[3]
    else:
        if path_parts and _LOCALE_PATTERN.fullmatch(path_parts[0]):
            detected_locale = path_parts.pop(0)
        if path_parts:
            detected_site = path_parts[0]

    explicit_site = str(site_identifier or "").strip().strip("/")
    if "/" in explicit_site:
        explicit_site = explicit_site.rsplit("/", 1)[-1]
    site = explicit_site or detected_site
    if not site or site.casefold() in {"job", "jobs", "page", "search"}:
        raise ValueError(
            "Workday career site could not be determined; use the board URL, such as /en-US/External"
        )
    selected_locale = str(locale or detected_locale or "en-US").strip()
    if not _LOCALE_PATTERN.fullmatch(selected_locale):
        raise ValueError("Workday locale must look like en-US")

    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    origin = urlunsplit((parsed.scheme.casefold(), netloc, "", "", "")).rstrip("/")
    return WorkdayTarget(
        origin=origin,
        host=host,
        tenant=tenant,
        site=site,
        locale=selected_locale,
    )


class WorkdayJobSource:
    """Collect public postings from Workday Candidate Experience (CXS) sites."""

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.http = http_client or UrllibHttpClient()
        self.rate_limiter = rate_limiter or CompanyRateLimiter()
        self.clock = clock or time.monotonic

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        """Fetch the complete description for one stored Workday posting.

        Bulk catalog refreshes may intentionally defer some Workday detail
        requests to keep the refresh bounded. Opening an application workspace
        is an explicit user action, so it is safe to make one targeted CXS
        detail request here instead of copying the abbreviated listing summary.
        """

        if job.source_type is not JobSourceType.WORKDAY:
            raise ValueError("fetch_job_description requires a Workday job")

        metadata = dict(job.metadata or {})
        target = parse_workday_careers_url(
            job.canonical_url,
            site_identifier=str(metadata.get("workday_site") or ""),
            locale=str(metadata.get("workday_locale") or ""),
        )
        external_path = _external_path(metadata.get("workday_external_path"))
        if not external_path:
            canonical_path = unquote(urlsplit(job.canonical_url).path)
            marker_index = canonical_path.find("/job/")
            if marker_index >= 0:
                external_path = _external_path(canonical_path[marker_index:])
        if not external_path:
            raise SourceFetchError(
                "The Workday detail path could not be determined from the posting URL."
            )

        source = CompanySource(
            id=job.source_id,
            owner_id=job.owner_id,
            company_name=job.company,
            careers_url=target.careers_url,
            source_type=JobSourceType.WORKDAY,
            source_identifier=target.site,
            filters={
                "timeout_seconds": 8.0,
                "max_response_bytes": DEFAULT_JSON_MAX_BYTES,
                "max_redirects": 3,
                "min_request_interval_seconds": 0.0,
            },
        )
        payload = self._fetch_detail(source, target, external_path)
        info = payload.get("jobPostingInfo") or {}
        if not isinstance(info, Mapping):
            return ""
        return html_to_text(info.get("jobDescription"))

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        validate_source_policy(source, expected_type=JobSourceType.WORKDAY)
        target = parse_workday_careers_url(
            source.careers_url,
            site_identifier=source.source_identifier,
            locale=str(source.filters.get("locale") or ""),
        )
        page_size = _bounded_int(
            source.filters.get("page_size"),
            default=DEFAULT_PAGE_SIZE,
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        )
        max_jobs = _bounded_int(
            source.filters.get("max_jobs"),
            default=DEFAULT_MAX_JOBS,
            minimum=1,
            maximum=MAX_MAX_JOBS,
        )
        max_pages = _bounded_int(
            source.filters.get("max_pages"),
            default=DEFAULT_MAX_PAGES,
            minimum=1,
            maximum=MAX_MAX_PAGES,
        )
        detail_fetch_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"),
            default=min(DEFAULT_DETAIL_FETCH_LIMIT, max_jobs),
            minimum=0,
            maximum=MAX_DETAIL_FETCH_LIMIT,
        )
        fetch_budget_seconds = _bounded_float(
            source.filters.get("fetch_budget_seconds"),
            default=DEFAULT_FETCH_BUDGET_SECONDS,
            minimum=0.0,
            maximum=MAX_FETCH_BUDGET_SECONDS,
        )
        deadline = (
            self.clock() + fetch_budget_seconds if fetch_budget_seconds > 0 else None
        )
        search_text = html_to_text(source.filters.get("search_text"), max_chars=500)
        applied_facets = source.filters.get("applied_facets")
        if not isinstance(applied_facets, Mapping):
            applied_facets = {}

        listings, total = self._fetch_listings(
            source,
            target,
            page_size=page_size,
            max_jobs=max_jobs,
            max_pages=max_pages,
            search_text=search_text,
            applied_facets=dict(applied_facets),
            deadline=deadline,
        )
        seen_at = utc_now_iso()
        jobs: list[DiscoveredJob] = []
        seen_external_ids: set[str] = set()
        for listing_index, listing in enumerate(listings):
            external_path = _external_path(listing.get("externalPath"))
            if not external_path:
                continue
            detail_error = ""
            detail_deferred = listing_index >= detail_fetch_limit or _deadline_reached(
                self.clock, deadline
            )
            if detail_deferred:
                detail_payload = {}
                detail_error = (
                    "Workday detail fetch deferred to keep the interactive refresh "
                    "within its request-time budget."
                )
            else:
                try:
                    detail_payload = self._fetch_detail(source, target, external_path)
                except SourceFetchError as exc:
                    detail_payload = {}
                    detail_error = str(exc)
            info = detail_payload.get("jobPostingInfo") or {}
            if not isinstance(info, Mapping):
                info = {}
            if info.get("canApply") is False:
                continue

            external_job_id = _external_job_id(info, listing, external_path)
            if external_job_id in seen_external_ids:
                continue
            seen_external_ids.add(external_job_id)

            title = html_to_text(info.get("title") or listing.get("title"))
            if not title:
                continue
            primary_location = html_to_text(
                info.get("location") or listing.get("locationsText")
            )
            additional_locations = normalize_string_list(
                info.get("additionalLocations") or info.get("additionalLocation")
            )
            locations = normalize_string_list((primary_location, *additional_locations))
            remote_type = info.get("remoteType") or listing.get("remoteType")
            employment_type = normalize_employment_type(
                info.get("timeType") or listing.get("timeType")
            )
            canonical_url = _canonical_job_url(target, external_path)
            if not canonical_url:
                continue
            posted_at = _posted_at(info, listing)
            valid_through = normalize_iso_timestamp(
                parse_datetime(
                    info.get("endDate")
                    or info.get("applicationCloseDate")
                    or info.get("jobEndDate")
                )
            )
            description = html_to_text(info.get("jobDescription")) or _listing_description(
                listing
            )
            metadata: dict[str, Any] = {
                "workday_tenant": target.tenant,
                "workday_site": target.site,
                "workday_locale": target.locale,
                "workday_external_path": external_path,
                "workday_total_jobs": total,
                "posted_on": html_to_text(listing.get("postedOn")),
                "can_apply": info.get("canApply"),
                "job_posting_id": html_to_text(info.get("jobPostingId")),
                "job_requisition_url": canonicalize_url(
                    str(info.get("jobRequisitionUrl") or "")
                ),
                "hiring_organization": html_to_text(
                    detail_payload.get("hiringOrganization")
                    or info.get("hiringOrganization")
                ),
                "detail_status": (
                    "deferred" if detail_deferred else "failed" if detail_error else "complete"
                ),
            }
            if detail_error:
                metadata["detail_error"] = detail_error

            jobs.append(
                DiscoveredJob(
                    id=discovered_job_id(source.owner_id, source.id, external_job_id),
                    owner_id=source.owner_id,
                    source_id=source.id,
                    external_job_id=external_job_id,
                    company=source.company_name,
                    title=title,
                    location=primary_location,
                    locations=locations,
                    workplace_type=normalize_workplace_type(
                        remote_type,
                        location="; ".join(locations) or primary_location,
                    ),
                    employment_type=employment_type,
                    description=description,
                    canonical_url=canonical_url,
                    apply_url=canonical_url,
                    posted_at=posted_at,
                    valid_through=valid_through,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_type=source.source_type,
                    department=html_to_text(
                        info.get("jobFamily")
                        or info.get("jobFamilyGroup")
                        or info.get("jobCategory")
                    ),
                    team=html_to_text(info.get("businessUnit") or info.get("team")),
                    metadata=metadata,
                )
            )
        return jobs

    def _fetch_listings(
        self,
        source: CompanySource,
        target: WorkdayTarget,
        *,
        page_size: int,
        max_jobs: int,
        max_pages: int,
        search_text: str,
        applied_facets: dict[str, object],
        deadline: float | None,
    ) -> tuple[list[dict[str, Any]], int]:
        listings: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        total = 0
        offset = 0
        for page_number in range(max_pages):
            if page_number > 0 and _deadline_reached(self.clock, deadline):
                break
            self.rate_limiter.wait(
                company_rate_limit_key(source), source_min_request_interval(source)
            )
            payload = fetch_json_post(
                self.http,
                target.listing_url,
                {
                    "appliedFacets": applied_facets,
                    "limit": page_size,
                    "offset": offset,
                    "searchText": search_text,
                },
                timeout=source_timeout(source),
                headers=_workday_headers(target),
                max_bytes=source_response_limit(source, default=DEFAULT_JSON_MAX_BYTES),
                max_redirects=source_redirect_limit(source),
                allowed_domains=(target.host,),
            )
            if not isinstance(payload, Mapping):
                raise SourceFetchError("Workday listing response must be a JSON object")
            try:
                total = max(total, int(payload.get("total") or 0))
            except (TypeError, ValueError):
                pass
            raw_postings = payload.get("jobPostings") or []
            if not isinstance(raw_postings, list):
                raise SourceFetchError("Workday listing response has invalid jobPostings")
            new_count = 0
            for raw in raw_postings:
                if not isinstance(raw, Mapping):
                    continue
                item = dict(raw)
                path = _external_path(item.get("externalPath"))
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                listings.append(item)
                new_count += 1
                if len(listings) >= max_jobs:
                    return listings, total
            if not raw_postings or new_count == 0:
                break
            offset += len(raw_postings)
            if total and offset >= total:
                break
        return listings, total

    def _fetch_detail(
        self,
        source: CompanySource,
        target: WorkdayTarget,
        external_path: str,
    ) -> dict[str, Any]:
        self.rate_limiter.wait(
            company_rate_limit_key(source), source_min_request_interval(source)
        )
        payload = fetch_json(
            self.http,
            f"{target.api_base_url}{external_path}",
            timeout=source_timeout(source),
            headers=_workday_headers(target),
            max_bytes=source_response_limit(source, default=DEFAULT_JSON_MAX_BYTES),
            max_redirects=source_redirect_limit(source),
            allowed_domains=(target.host,),
        )
        if not isinstance(payload, Mapping):
            raise SourceFetchError("Workday detail response must be a JSON object")
        return dict(payload)


def _deadline_reached(clock: Callable[[], float], deadline: float | None) -> bool:
    return deadline is not None and clock() >= deadline


def _listing_description(listing: Mapping[str, Any]) -> str:
    parts = [
        html_to_text(listing.get("title")),
        html_to_text(listing.get("locationsText")),
        html_to_text(listing.get("remoteType")),
        html_to_text(listing.get("timeType")),
    ]
    bullet_fields = listing.get("bulletFields") or []
    if isinstance(bullet_fields, list):
        parts.extend(html_to_text(value) for value in bullet_fields)
    return ". ".join(part for part in dict.fromkeys(parts) if part)


def _bounded_float(
    value: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _workday_headers(target: WorkdayTarget) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Accept-Language": target.locale,
        "Origin": target.origin,
        "Referer": target.careers_url,
    }


def _external_path(value: object) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    if "://" in path:
        path = urlsplit(path).path
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/job/"):
        return ""
    return path


def _external_job_id(
    info: Mapping[str, Any],
    listing: Mapping[str, Any],
    external_path: str,
) -> str:
    for value in (
        info.get("jobReqId"),
        info.get("jobPostingId"),
    ):
        text = html_to_text(value)
        if text:
            return text
    bullet_fields = listing.get("bulletFields") or []
    if isinstance(bullet_fields, list):
        for value in bullet_fields:
            text = html_to_text(value)
            if text and re.search(r"\d", text) and len(text) <= 100:
                return text
    return external_path


def _canonical_job_url(target: WorkdayTarget, external_path: str) -> str:
    return canonicalize_url(
        f"{target.origin}/{quote(target.locale, safe='-')}/{quote(target.site, safe='')}"
        f"{external_path}"
    )


def _posted_at(info: Mapping[str, Any], listing: Mapping[str, Any]) -> str:
    explicit = (
        info.get("postingDate")
        or info.get("postedDate")
        or info.get("startDate")
    )
    parsed = parse_datetime(explicit)
    if parsed is not None:
        return normalize_iso_timestamp(parsed)
    text = html_to_text(listing.get("postedOn")).casefold()
    if not text:
        return ""
    now = datetime.now(timezone.utc)
    if "today" in text:
        return normalize_iso_timestamp(now)
    if "yesterday" in text:
        return normalize_iso_timestamp(now - timedelta(days=1))
    match = _POSTED_DAYS_PATTERN.search(text)
    if match:
        return normalize_iso_timestamp(now - timedelta(days=int(match.group(1))))
    return ""


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)
