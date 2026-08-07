from __future__ import annotations

from .common import (
    bounded_int as _bounded_int,
    bounded_float as _bounded_float,
)

import re
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

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
from .base import (
    DEFAULT_JSON_MAX_BYTES,
    CompanyRateLimiter,
    HttpClient,
    RobotsDeniedError,
    SourceFetchError,
    UrllibHttpClient,
    company_rate_limit_key,
    fetch_json_post,
    source_min_request_interval,
    source_redirect_limit,
    source_response_limit,
    source_timeout,
    validate_fetch_url,
    validate_source_policy,
)
from .public_portal import PortalProfile, PortalTarget, PublicPortalJobSource


_UKG_HOST_PATTERN = re.compile(r"^recruiting\d*\.ultipro\.(?:com|ca)$", re.IGNORECASE)
_TENANT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,80}$")
_BOARD_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_OPPORTUNITY_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_PROFILE = PortalProfile(
    source_type=JobSourceType.UKG_PRO,
    platform_name="UKG Pro / UltiPro",
    job_url_patterns=(
        re.compile(r"/OpportunityDetail(?:\?|/).*opportunityId=", re.IGNORECASE),
        re.compile(r"[?&]opportunityId=", re.IGNORECASE),
    ),
    allowed_host_suffixes=(".ultipro.com", ".ultipro.ca"),
)


@dataclass(frozen=True, slots=True)
class UkgProTarget:
    origin: str
    host: str
    tenant: str
    board_id: str

    @property
    def listing_url(self) -> str:
        return f"{self.origin}/{quote(self.tenant, safe='_-')}/JobBoard/{self.board_id}"

    @property
    def search_url(self) -> str:
        return f"{self.listing_url}/JobBoardView/LoadSearchResults"

    @property
    def allowed_domains(self) -> tuple[str, ...]:
        return (self.host,)

    def detail_url(self, opportunity_id: str) -> str:
        query = urlencode({"opportunityId": opportunity_id})
        return f"{self.listing_url}/OpportunityDetail?{query}"

    def portal_target(self) -> PortalTarget:
        return PortalTarget(self.listing_url, self.allowed_domains)


def parse_ukg_pro_careers_url(value: str) -> UkgProTarget:
    """Parse a public UKG Pro Recruiting / UltiPro JobBoard URL.

    Supported URLs include a board root and an individual OpportunityDetail URL:
    ``https://recruiting2.ultipro.com/TENANT/JobBoard/<board-guid>``.
    """

    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("UKG Pro / UltiPro requires a public JobBoard URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("UKG Pro / UltiPro URLs cannot contain credentials")
    host = parsed.hostname.casefold().rstrip(".")
    if not _UKG_HOST_PATTERN.fullmatch(host):
        raise ValueError(
            "UKG Pro / UltiPro URL must use recruiting.ultipro.com, "
            "recruiting2.ultipro.com, or another recruiting<number>.ultipro.com/.ca host"
        )

    parts = [part for part in parsed.path.split("/") if part]
    try:
        board_index = next(
            index for index, part in enumerate(parts) if part.casefold() == "jobboard"
        )
    except StopIteration as exc:
        raise ValueError(
            "UKG Pro / UltiPro URL must include /<tenant>/JobBoard/<board UUID>"
        ) from exc
    if board_index < 1 or board_index + 1 >= len(parts):
        raise ValueError(
            "UKG Pro / UltiPro URL must include /<tenant>/JobBoard/<board UUID>"
        )
    tenant = parts[board_index - 1]
    board_id = parts[board_index + 1]
    if not _TENANT_PATTERN.fullmatch(tenant):
        raise ValueError("UKG Pro / UltiPro tenant code is invalid")
    if not _BOARD_PATTERN.fullmatch(board_id):
        raise ValueError("UKG Pro / UltiPro JobBoard identifier must be a UUID")

    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    origin = urlunsplit((parsed.scheme.casefold(), netloc, "", "", "")).rstrip("/")
    return UkgProTarget(
        origin=origin,
        host=host,
        tenant=tenant,
        board_id=board_id.casefold(),
    )


class UkgProJobSource:
    """Collect public postings from UKG Pro Recruiting (formerly UltiPro)."""

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
        clock=time.monotonic,
    ) -> None:
        self.http = http_client or UrllibHttpClient()
        self.rate_limiter = rate_limiter or CompanyRateLimiter()
        self.clock = clock
        self.portal = PublicPortalJobSource(
            _PROFILE,
            http_client=self.http,
            rate_limiter=self.rate_limiter,
            clock=clock,
        )

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        validate_source_policy(source, expected_type=JobSourceType.UKG_PRO)
        target = parse_ukg_pro_careers_url(source.careers_url)

        # Respect the public board's robots policy for both the visible board and
        # the XHR listing path. If the XHR path is disallowed, use only the
        # robots-aware public HTML/JSON-LD fallback.
        if not self.portal._allowed(source, target.listing_url, target.allowed_domains):
            raise RobotsDeniedError(
                f"robots.txt disallows crawling {target.listing_url}"
            )
        if not self.portal._allowed(source, target.search_url, target.allowed_domains):
            return self.portal.fetch_jobs(source, target.portal_target())

        try:
            jobs = self._fetch_api_jobs(source, target)
        except SourceFetchError as api_error:
            # Some older or branded boards render usable public HTML while blocking
            # the XHR endpoint. Retain a robots-aware HTML/JSON-LD fallback.
            try:
                fallback = self.portal.fetch_jobs(source, target.portal_target())
            except SourceFetchError:
                raise api_error
            if fallback:
                return fallback
            raise api_error
        if jobs:
            return jobs

        # An empty API response is valid, but a page may still expose jobs through
        # server-rendered links if the board's internal API identifier differs.
        fallback = self.portal.fetch_jobs(source, target.portal_target())
        return fallback or jobs

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        if job.source_type is not JobSourceType.UKG_PRO:
            raise ValueError("fetch_job_description requires a UKG Pro / UltiPro job")
        target = parse_ukg_pro_careers_url(job.canonical_url)
        return self.portal.fetch_job_description(job, target.portal_target())

    def _fetch_api_jobs(
        self,
        source: CompanySource,
        target: UkgProTarget,
    ) -> list[DiscoveredJob]:
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)
        max_pages = _bounded_int(source.filters.get("max_pages"), 20, 1, 50)
        page_size = _bounded_int(source.filters.get("page_size"), 50, 1, 100)
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"), max_jobs, 0, max_jobs
        )
        budget = _bounded_float(
            source.filters.get("fetch_budget_seconds"), 0.0, 0.0, 300.0
        )
        deadline = self.clock() + budget if budget > 0 else None

        records: list[Mapping[str, Any]] = []
        skip = 0
        for _ in range(max_pages):
            if deadline is not None and self.clock() >= deadline:
                break
            payload = self._fetch_page(
                source,
                target,
                top=min(page_size, max_jobs - len(records)),
                skip=skip,
            )
            page_records = _opportunities(payload)
            if not page_records:
                break
            records.extend(page_records[: max_jobs - len(records)])
            returned = len(page_records)
            skip += returned
            total = _total_count(payload)
            if (
                len(records) >= max_jobs
                or returned < page_size
                or (total is not None and skip >= total)
            ):
                break

        seen_at = utc_now_iso()
        jobs: list[DiscoveredJob] = []
        for index, record in enumerate(records):
            opportunity_id = _text(record, "Id", "id", "OpportunityId", "opportunityId")
            if not _OPPORTUNITY_PATTERN.fullmatch(opportunity_id):
                continue
            title = _text(record, "Title", "title", "JobTitle", "jobTitle")
            if not title:
                continue
            detail_url = canonicalize_url(target.detail_url(opportunity_id))
            location_values = _locations(record)
            location = "; ".join(location_values)
            description = html_to_text(
                _value(
                    record,
                    "BriefDescription",
                    "briefDescription",
                    "Description",
                    "description",
                )
            )
            employment_value = _value(
                record,
                "EmploymentType",
                "employmentType",
                "JobType",
                "jobType",
            )
            full_time = _value(
                record, "FullTime", "fullTime", "IsFullTime", "isFullTime"
            )
            if employment_value in (None, "") and full_time not in (None, ""):
                if isinstance(full_time, bool):
                    employment_value = "Full time" if full_time else "Part time"
                else:
                    employment_value = full_time
            workplace_value = _value(
                record,
                "WorkplaceType",
                "workplaceType",
                "LocationType",
                "locationType",
                "RemoteType",
                "remoteType",
            )
            posted = _parse_ukg_date(
                _value(
                    record,
                    "PostedDate",
                    "postedDate",
                    "DatePosted",
                    "datePosted",
                    "ExternalPostedDate",
                    "externalPostedDate",
                    "PublishedDate",
                    "publishedDate",
                )
            )
            requisition = _text(
                record,
                "RequisitionNumber",
                "requisitionNumber",
                "RequisitionId",
                "requisitionId",
            )
            metadata = {
                "ukg_host": target.host,
                "ukg_tenant": target.tenant,
                "ukg_board_id": target.board_id,
                "ukg_opportunity_id": opportunity_id,
                "requisition_number": requisition,
                "detail_status": "deferred",
            }
            job = DiscoveredJob(
                id=discovered_job_id(source.owner_id, source.id, opportunity_id),
                owner_id=source.owner_id,
                source_id=source.id,
                external_job_id=opportunity_id,
                company=source.company_name,
                title=title,
                location=location,
                locations=location_values,
                workplace_type=normalize_workplace_type(
                    workplace_value,
                    location=location,
                    is_remote=_value(
                        record, "Remote", "remote", "IsRemote", "isRemote"
                    ),
                ),
                employment_type=normalize_employment_type(employment_value),
                description=description,
                canonical_url=detail_url,
                apply_url=detail_url,
                posted_at=normalize_iso_timestamp(posted),
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                source_type=source.source_type,
                department=_text(
                    record,
                    "JobCategoryName",
                    "jobCategoryName",
                    "DepartmentName",
                    "departmentName",
                ),
                metadata=metadata,
            )
            if index < detail_limit and (deadline is None or self.clock() < deadline):
                try:
                    detail = self.portal.fetch_job_description(job, target.portal_target())
                except SourceFetchError as exc:
                    metadata = dict(job.metadata)
                    metadata.update(
                        {"detail_status": "failed", "detail_error": str(exc)[:500]}
                    )
                    job = replace(job, metadata=metadata)
                else:
                    if detail:
                        metadata = dict(job.metadata)
                        metadata["detail_status"] = "complete"
                        job = replace(job, description=detail, metadata=metadata)
            jobs.append(job)
        return jobs

    def _fetch_page(
        self,
        source: CompanySource,
        target: UkgProTarget,
        *,
        top: int,
        skip: int,
    ) -> Any:
        validate_fetch_url(target.search_url, allowed_domains=target.allowed_domains)
        self.rate_limiter.wait(
            company_rate_limit_key(source),
            source_min_request_interval(source, default=0.2),
        )
        return fetch_json_post(
            self.http,
            target.search_url,
            {"opportunitySearch": {"Top": top, "Skip": skip}},
            timeout=source_timeout(source),
            headers={"X-Requested-With": "XMLHttpRequest"},
            max_bytes=source_response_limit(source, default=DEFAULT_JSON_MAX_BYTES),
            max_redirects=source_redirect_limit(source),
            allowed_domains=target.allowed_domains,
        )


def _value(item: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item[name] not in (None, "", [], {}):
            return item[name]
    folded = {str(key).casefold(): value for key, value in item.items()}
    for name in names:
        value = folded.get(name.casefold())
        if value not in (None, "", [], {}):
            return value
    return None


def _text(item: Mapping[str, Any], *names: str) -> str:
    value = _value(item, *names)
    if isinstance(value, Mapping):
        value = _value(value, "Name", "name", "Value", "value", "Text", "text")
    return normalize_whitespace(value)


def _opportunities(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        raise SourceFetchError("UKG Pro / UltiPro search response was not a JSON object")
    for key in (
        "opportunities",
        "Opportunities",
        "results",
        "Results",
        "items",
        "Items",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    for nested_key in ("data", "Data", "result", "Result", "opportunitySearchResults"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            try:
                return _opportunities(nested)
            except SourceFetchError:
                pass
    raise SourceFetchError("UKG Pro / UltiPro search response did not contain opportunities")


def _total_count(payload: Any) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    for name in ("totalCount", "TotalCount", "total", "Total", "count", "Count"):
        value = payload.get(name)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _locations(record: Mapping[str, Any]) -> tuple[str, ...]:
    raw = _value(
        record,
        "Locations",
        "locations",
        "Location",
        "location",
        "LocationName",
        "locationName",
    )
    values: list[str] = []
    candidates: Sequence[Any]
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        candidates = raw
    else:
        candidates = (raw,)
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            direct = _text(candidate, "Name", "name", "DisplayName", "displayName")
            if direct:
                text = direct
            else:
                parts = [
                    _text(candidate, "City", "city"),
                    _text(candidate, "State", "state", "Region", "region"),
                    _text(candidate, "Country", "country"),
                ]
                text = ", ".join(part for part in parts if part)
        else:
            text = normalize_whitespace(candidate)
        if text and text.casefold() not in {value.casefold() for value in values}:
            values.append(text)
    return normalize_string_list(values)


def _parse_ukg_date(value: Any):
    if isinstance(value, str):
        match = re.fullmatch(r"/Date\(([-+]?\d+)(?:[-+]\d+)?\)/", value.strip())
        if match:
            try:
                return parse_datetime(int(match.group(1)))
            except (TypeError, ValueError):
                return None
    return parse_datetime(value)
