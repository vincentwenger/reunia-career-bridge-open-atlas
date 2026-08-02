from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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
    format_salary_text,
    html_to_text,
    normalize_employment_type,
    normalize_string_list,
    normalize_whitespace,
    normalize_workplace_type,
    parse_datetime,
    parse_number,
)
from .base import (
    DEFAULT_HTML_MAX_BYTES,
    DEFAULT_JSON_MAX_BYTES,
    CompanyRateLimiter,
    HttpClient,
    RobotsDeniedError,
    SourceFetchError,
    UrllibHttpClient,
    company_rate_limit_key,
    source_min_request_interval,
    source_redirect_limit,
    source_response_limit,
    source_timeout,
    validate_fetch_url,
    validate_source_policy,
)
from .generic_jsonld import ROBOTS_MAX_BYTES, ROBOTS_PRODUCT_TOKEN, _RobotsPolicy


@dataclass(frozen=True, slots=True)
class PortalProfile:
    source_type: JobSourceType
    platform_name: str
    job_url_patterns: tuple[re.Pattern[str], ...]
    listing_url_patterns: tuple[re.Pattern[str], ...] = ()
    allowed_host_suffixes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PortalTarget:
    listing_url: str
    allowed_domains: tuple[str, ...]


@dataclass(slots=True)
class _Anchor:
    href: str
    text: str
    context: str
    rel: str = ""


class _PortalParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[Any] = []
        self.anchors: list[_Anchor] = []
        self.title_candidates: list[str] = []
        self.text_parts: list[str] = []
        self._containers: list[dict[str, Any]] = []
        self._anchor: dict[str, Any] | None = None
        self._heading: dict[str, Any] | None = None
        self._script_parts: list[str] = []
        self._in_script = False
        self._script_json = False
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if name == "script":
            script_type = attributes.get("type", "").casefold()
            script_id = attributes.get("id", "").casefold()
            self._in_script = True
            self._script_json = True
            self._script_parts = []
            return
        if name in {"style", "noscript", "template", "svg"}:
            self._blocked_depth += 1
            return
        if name in {"article", "li", "tr", "section"} or (
            name == "div"
            and re.search(
                r"(?:job|position|posting|result|vacan|career)",
                attributes.get("class", ""),
                re.IGNORECASE,
            )
        ):
            self._containers.append({"tag": name, "parts": []})
        if name == "a" and attributes.get("href"):
            self._anchor = {
                "href": attributes["href"],
                "rel": attributes.get("rel", ""),
                "parts": [],
            }
        if name in {"h1", "h2"}:
            self._heading = {"tag": name, "parts": []}

    def handle_data(self, data: str) -> None:
        if self._in_script:
            if self._script_json:
                self._script_parts.append(data)
            return
        if self._blocked_depth:
            return
        self.text_parts.append(data)
        for container in self._containers:
            container["parts"].append(data)
        if self._anchor is not None:
            self._anchor["parts"].append(data)
        if self._heading is not None:
            self._heading["parts"].append(data)
        if self._script_json:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in {"td", "th"}:
            # Preserve table-cell boundaries in the enclosing row context so
            # action-only links can distinguish title, category, and location.
            for container in self._containers:
                container["parts"].append(" | ")
        if name == "script":
            raw = "".join(self._script_parts).strip()
            if raw:
                self.documents.extend(_parse_json_documents(raw))
            self._script_parts = []
            self._script_json = False
            self._in_script = False
            return
        if name in {"style", "noscript", "template", "svg"}:
            if self._blocked_depth:
                self._blocked_depth -= 1
            return
        if name == "a" and self._anchor is not None:
            context = ""
            if self._containers:
                context = normalize_whitespace(" ".join(self._containers[-1]["parts"]))
            self.anchors.append(
                _Anchor(
                    href=self._anchor["href"],
                    text=normalize_whitespace(" ".join(self._anchor["parts"])),
                    context=context,
                    rel=normalize_whitespace(self._anchor.get("rel", "")),
                )
            )
            self._anchor = None
        if self._heading is not None and self._heading["tag"] == name:
            value = normalize_whitespace(" ".join(self._heading["parts"]))
            if value:
                self.title_candidates.append(value)
            self._heading = None
        if self._containers and self._containers[-1]["tag"] == name:
            self._containers.pop()

    @property
    def page_text(self) -> str:
        return normalize_whitespace(" ".join(self.text_parts))


class PublicPortalJobSource:
    """Bounded public-career-portal connector shared by URL-driven ATS types."""

    def __init__(
        self,
        profile: PortalProfile,
        http_client: HttpClient | None = None,
        *,
        rate_limiter: CompanyRateLimiter | None = None,
        clock=time.monotonic,
    ) -> None:
        self.profile = profile
        self.http = http_client or UrllibHttpClient()
        self.rate_limiter = rate_limiter or CompanyRateLimiter()
        self.clock = clock
        self._robots: dict[str, _RobotsPolicy] = {}

    def fetch_jobs(self, source: CompanySource, target: PortalTarget) -> list[DiscoveredJob]:
        validate_source_policy(source, expected_type=self.profile.source_type)
        listing_url = canonicalize_url(target.listing_url)
        allowed_domains = tuple(dict.fromkeys(target.allowed_domains))
        validate_fetch_url(listing_url, allowed_domains=allowed_domains)

        max_pages = _bounded_int(source.filters.get("max_pages"), 10, 1, 50)
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"), max_jobs, 0, max_jobs
        )
        budget = _bounded_float(
            source.filters.get("fetch_budget_seconds"), 0.0, 0.0, 300.0
        )
        deadline = self.clock() + budget if budget > 0 else None

        queue = [listing_url]
        visited: set[str] = set()
        candidates: dict[str, dict[str, Any]] = {}
        while queue and len(visited) < max_pages and len(candidates) < max_jobs:
            if deadline is not None and self.clock() >= deadline:
                break
            page_url = queue.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            html = self._fetch_html(source, page_url, allowed_domains)
            parser = _PortalParser()
            parser.feed(html)
            parser.close()
            _merge_candidates(
                candidates,
                _document_candidates(
                    parser.documents,
                    page_url=page_url,
                    profile=self.profile,
                ),
            )
            _merge_candidates(
                candidates,
                _anchor_candidates(
                    parser.anchors,
                    page_url=page_url,
                    profile=self.profile,
                    allowed_domains=allowed_domains,
                ),
            )
            for next_url in _pagination_urls(
                parser.anchors,
                page_url=page_url,
                profile=self.profile,
                allowed_domains=allowed_domains,
            ):
                if next_url not in visited and next_url not in queue:
                    queue.append(next_url)
                    if len(visited) + len(queue) >= max_pages:
                        break

        return self._build_jobs_from_candidates(
            source,
            candidates,
            allowed_domains=allowed_domains,
            max_jobs=max_jobs,
            detail_limit=detail_limit,
            deadline=deadline,
        )

    def fetch_json_document(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: Sequence[str],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Fetch one robots-aware JSON document from the configured portal host."""

        validate_source_policy(source, expected_type=self.profile.source_type)
        validate_fetch_url(url, allowed_domains=allowed_domains)
        if not self._allowed(source, url, allowed_domains):
            raise RobotsDeniedError(f"robots.txt disallows crawling {url}")
        self.rate_limiter.wait(
            company_rate_limit_key(source),
            source_min_request_interval(source, default=0.5),
        )
        request_headers = {"Accept": "application/json"}
        request_headers.update(headers or {})
        response = self.http.get(
            url,
            headers=request_headers,
            timeout=source_timeout(source),
            max_bytes=source_response_limit(source, default=DEFAULT_JSON_MAX_BYTES),
            max_redirects=source_redirect_limit(source),
            allowed_domains=allowed_domains,
        )
        validate_fetch_url(response.url, allowed_domains=allowed_domains)
        if response.status < 200 or response.status >= 300:
            raise SourceFetchError(f"GET {url} returned HTTP {response.status}")
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise SourceFetchError(f"GET {url} did not return valid JSON") from exc

    def fetch_known_job_urls(
        self,
        source: CompanySource,
        target: PortalTarget,
        urls: Sequence[str],
        *,
        titles_by_url: Mapping[str, str] | None = None,
        records_by_url: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[DiscoveredJob]:
        """Fetch exact ATS detail URLs discovered by an authorized index/feed.

        This method never touches the listing route. Every supplied URL is
        constrained to the configured host and each detail request still passes
        through the normal robots-policy, rate, timeout, redirect, and size checks.
        """

        validate_source_policy(source, expected_type=self.profile.source_type)
        allowed_domains = tuple(dict.fromkeys(target.allowed_domains))
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"), max_jobs, 0, max_jobs
        )
        budget = _bounded_float(
            source.filters.get("fetch_budget_seconds"), 0.0, 0.0, 300.0
        )
        deadline = self.clock() + budget if budget > 0 else None
        candidates: dict[str, dict[str, Any]] = {}
        normalized_titles = {
            canonicalize_url(raw_url): normalize_whitespace(title)
            for raw_url, title in (titles_by_url or {}).items()
            if canonicalize_url(raw_url)
        }
        normalized_records = {
            canonicalize_url(raw_url): dict(record)
            for raw_url, record in (records_by_url or {}).items()
            if canonicalize_url(raw_url) and isinstance(record, Mapping)
        }
        for raw_url in urls:
            url = canonicalize_url(raw_url)
            if not url or url in candidates:
                continue
            validate_fetch_url(url, allowed_domains=allowed_domains)
            if not _matches_job_url(url, self.profile):
                continue
            candidate = dict(normalized_records.get(url, {}))
            candidate["canonical_url"] = url
            candidate.setdefault("external_id", _external_id_from_url(url))
            if normalized_titles.get(url) and not normalize_whitespace(candidate.get("title")):
                candidate["title"] = normalized_titles[url]
            candidates[url] = candidate
            if len(candidates) >= max_jobs:
                break
        return self._build_jobs_from_candidates(
            source,
            candidates,
            allowed_domains=allowed_domains,
            max_jobs=max_jobs,
            detail_limit=detail_limit,
            deadline=deadline,
        )

    def _build_jobs_from_candidates(
        self,
        source: CompanySource,
        candidates: Mapping[str, Mapping[str, Any]],
        *,
        allowed_domains: Sequence[str],
        max_jobs: int,
        detail_limit: int,
        deadline: float | None,
    ) -> list[DiscoveredJob]:
        jobs: list[DiscoveredJob] = []
        seen_at = utc_now_iso()
        for index, record in enumerate(candidates.values()):
            if len(jobs) >= max_jobs:
                break
            detail = dict(record)
            detail_url = canonicalize_url(detail.get("canonical_url"))
            deferred = index >= detail_limit or (
                deadline is not None and self.clock() >= deadline
            )
            if detail_url and not deferred and not _has_complete_detail(detail):
                try:
                    fetched = self._fetch_detail(
                        source, detail_url, allowed_domains
                    )
                except SourceFetchError as exc:
                    detail["detail_error"] = str(exc)
                else:
                    detail = _merge_record(detail, fetched)

            external_id = normalize_whitespace(detail.get("external_id"))
            if not external_id and detail_url:
                external_id = _external_id_from_url(detail_url)
            title = normalize_whitespace(detail.get("title"))
            if not title or not detail_url:
                continue
            description = html_to_text(detail.get("description"))
            if not description:
                description = normalize_whitespace(detail.get("listing_context"))
            location = normalize_whitespace(detail.get("location"))
            salary_min = parse_number(detail.get("salary_min"))
            salary_max = parse_number(detail.get("salary_max"))
            currency = normalize_whitespace(detail.get("salary_currency"))
            interval = normalize_whitespace(detail.get("salary_interval"))
            metadata = {
                "portal_platform": self.profile.platform_name,
                "detail_status": (
                    "deferred"
                    if deferred and not _has_complete_detail(detail)
                    else "failed"
                    if detail.get("detail_error")
                    else "fetched"
                    if not deferred
                    else "embedded"
                ),
            }
            metadata.update(
                detail.get("metadata")
                if isinstance(detail.get("metadata"), Mapping)
                else {}
            )
            if detail.get("detail_error"):
                metadata["detail_error"] = normalize_whitespace(
                    detail["detail_error"]
                )[:1000]
            jobs.append(
                DiscoveredJob(
                    id=discovered_job_id(source.owner_id, source.id, external_id),
                    owner_id=source.owner_id,
                    source_id=source.id,
                    external_job_id=external_id,
                    company=source.company_name,
                    title=title,
                    location=location,
                    locations=normalize_string_list(detail.get("locations") or location),
                    workplace_type=normalize_workplace_type(
                        detail.get("workplace_type"), location=location
                    ),
                    employment_type=normalize_employment_type(
                        detail.get("employment_type")
                    ),
                    salary_text=format_salary_text(
                        salary_min,
                        salary_max,
                        currency,
                        interval,
                        summary=detail.get("salary_text", ""),
                    ),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=currency,
                    salary_interval=interval,
                    description=description,
                    canonical_url=detail_url,
                    apply_url=canonicalize_url(detail.get("apply_url") or detail_url),
                    posted_at=normalize_iso_timestamp(parse_datetime(detail.get("posted_at"))),
                    valid_through=normalize_iso_timestamp(
                        parse_datetime(detail.get("valid_through"))
                    ),
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_type=source.source_type,
                    department=normalize_whitespace(detail.get("department")),
                    skills=normalize_string_list(detail.get("skills")),
                    metadata=metadata,
                )
            )
        return jobs

    def fetch_job_description(
        self,
        job: DiscoveredJob,
        target: PortalTarget,
    ) -> str:
        if job.source_type is not self.profile.source_type:
            raise ValueError(
                f"{self.profile.platform_name} detail lookup requires a "
                f"{self.profile.source_type.value} job"
            )
        source = CompanySource(
            id=f"detail-{job.source_id}",
            owner_id=job.owner_id,
            company_name=job.company,
            careers_url=target.listing_url,
            source_type=self.profile.source_type,
            source_identifier="",
            filters={
                "timeout_seconds": 8.0,
                "max_redirects": 3,
                "min_request_interval_seconds": 0.0,
            },
        )
        detail = self._fetch_detail(source, job.canonical_url, target.allowed_domains)
        return html_to_text(detail.get("description"))

    def _fetch_detail(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: Sequence[str],
    ) -> dict[str, Any]:
        html = self._fetch_html(source, url, allowed_domains)
        parser = _PortalParser()
        parser.feed(html)
        parser.close()
        records = _document_candidates(
            parser.documents,
            page_url=url,
            profile=self.profile,
            accept_page_url=True,
        )
        record = next(iter(records.values()), {})
        if not record:
            title = next((value for value in parser.title_candidates if value), "")
            record = {
                "canonical_url": url,
                "external_id": _external_id_from_url(url),
                "title": title,
                "description": parser.page_text,
            }
        elif not record.get("description"):
            record["description"] = parser.page_text
        return record

    def _fetch_html(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: Sequence[str],
    ) -> str:
        validate_fetch_url(url, allowed_domains=allowed_domains)
        if not self._allowed(source, url, allowed_domains):
            raise RobotsDeniedError(f"robots.txt disallows crawling {url}")
        self.rate_limiter.wait(
            company_rate_limit_key(source),
            source_min_request_interval(source, default=0.5),
        )
        response = self.http.get(
            url,
            headers={"Accept": "text/html, application/xhtml+xml, application/json;q=0.9"},
            timeout=source_timeout(source),
            max_bytes=source_response_limit(source, default=DEFAULT_HTML_MAX_BYTES),
            max_redirects=source_redirect_limit(source),
            allowed_domains=allowed_domains,
        )
        validate_fetch_url(response.url, allowed_domains=allowed_domains)
        if response.status < 200 or response.status >= 300:
            raise SourceFetchError(f"GET {url} returned HTTP {response.status}")
        return response.text()

    def _allowed(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: Sequence[str],
    ) -> bool:
        parsed = urlsplit(url)
        robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        if robots_url in self._robots:
            return self._robots[robots_url].allowed(url)
        try:
            validate_fetch_url(robots_url, allowed_domains=allowed_domains)
            self.rate_limiter.wait(
                company_rate_limit_key(source),
                source_min_request_interval(source, default=0.5),
            )
            response = self.http.get(
                robots_url,
                headers={"Accept": "text/plain"},
                timeout=source_timeout(source),
                max_bytes=ROBOTS_MAX_BYTES,
                max_redirects=source_redirect_limit(source),
                allowed_domains=allowed_domains,
            )
        except SourceFetchError:
            policy = _RobotsPolicy.disallow_all()
        else:
            if 200 <= response.status < 300:
                policy = _RobotsPolicy.parse(response.text(), ROBOTS_PRODUCT_TOKEN)
            elif 400 <= response.status < 500:
                policy = _RobotsPolicy.allow_all()
            else:
                policy = _RobotsPolicy.disallow_all()
        self._robots[robots_url] = policy
        return policy.allowed(url)


def portal_target(url: str, *, allowed_host_suffixes: Sequence[str] = ()) -> PortalTarget:
    canonical = canonicalize_url(url)
    parsed = urlsplit(canonical)
    host = (parsed.hostname or "").casefold()
    if not host:
        raise ValueError("A public career-site URL is required")
    if allowed_host_suffixes and not any(
        host == suffix.lstrip(".") or host.endswith(suffix)
        for suffix in allowed_host_suffixes
    ):
        expected = " or ".join(allowed_host_suffixes)
        raise ValueError(f"Career-site URL must use {expected}")
    return PortalTarget(canonical, (host,))


def _parse_json_documents(raw: str) -> list[Any]:
    text = raw.strip()
    if not text:
        return []
    candidates = [text]
    assignment = re.match(r"^[A-Za-z_$][\w$.[\]'\"]*\s*=\s*(.+?);?\s*$", text, re.DOTALL)
    if assignment:
        candidates.append(assignment.group(1).strip().rstrip(";"))
    documents: list[Any] = []
    for candidate in candidates:
        try:
            documents.append(json.loads(candidate))
        except (json.JSONDecodeError, TypeError):
            continue
    return documents


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _document_candidates(
    documents: Sequence[Any],
    *,
    page_url: str,
    profile: PortalProfile,
    accept_page_url: bool = False,
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for document in documents:
        for item in _walk_json(document):
            record = _record_from_mapping(
                item,
                page_url=page_url,
                allow_without_url=profile.source_type is JobSourceType.EIGHTFOLD,
            )
            if not record:
                continue
            job_url = canonicalize_url(record.get("canonical_url") or page_url)
            if not _matches_job_url(job_url, profile) and record.get("external_id"):
                parsed = urlsplit(page_url)
                external_id = normalize_whitespace(record.get("external_id"))
                is_eightfold_vanity = (
                    profile.source_type is JobSourceType.EIGHTFOLD
                    and not (
                        (parsed.hostname or "").casefold() == "eightfold.ai"
                        or (parsed.hostname or "").casefold().endswith(".eightfold.ai")
                    )
                )
                if is_eightfold_vanity:
                    segments = [segment for segment in parsed.path.split("/") if segment]
                    route_index = next(
                        (
                            index
                            for index, segment in enumerate(segments)
                            if segment.casefold() in {"jobs", "careers"}
                        ),
                        None,
                    )
                    if route_index is not None:
                        base_segments = segments[: route_index + 1]
                        if base_segments[-1].casefold() == "careers":
                            base_segments.append("job")
                        base_segments.append(external_id)
                        job_url = canonicalize_url(
                            urlunsplit(
                                (
                                    parsed.scheme,
                                    parsed.netloc,
                                    "/" + "/".join(base_segments),
                                    parsed.query,
                                    "",
                                )
                            )
                        )
                else:
                    parameter = {
                        JobSourceType.EIGHTFOLD: "pid",
                        JobSourceType.DAYFORCE: "jobId",
                        JobSourceType.TALEO: "job",
                        JobSourceType.AVATURE: "jobId",
                    }.get(profile.source_type)
                    if parameter:
                        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                        query[parameter] = external_id
                        job_url = canonicalize_url(
                            urlunsplit(
                                (
                                    parsed.scheme,
                                    parsed.netloc,
                                    parsed.path,
                                    urlencode(query),
                                    "",
                                )
                            )
                        )
            if not accept_page_url and not _matches_job_url(job_url, profile):
                continue
            record["canonical_url"] = job_url
            key = normalize_whitespace(record.get("external_id")) or job_url
            candidates[key] = _merge_record(candidates.get(key, {}), record)
    return candidates


def _record_from_mapping(
    item: Mapping[str, Any],
    *,
    page_url: str,
    allow_without_url: bool = False,
) -> dict[str, Any]:
    type_value = item.get("@type")
    types = (
        {str(value).casefold() for value in type_value}
        if isinstance(type_value, list)
        else {str(type_value).casefold()}
    )
    is_jsonld = "jobposting" in types
    title = _first_text(
        item,
        "title",
        "name",
        "jobTitle",
        "positionTitle",
        "hiringTitle",
        "displayTitle",
    )
    external_id = _first_text(
        item,
        "jobId",
        "jobID",
        "positionId",
        "positionID",
        "requisitionId",
        "requisitionID",
        "reqId",
        "id",
        "display_job_id",
    )
    identifier = item.get("identifier")
    if isinstance(identifier, Mapping):
        external_id = external_id or normalize_whitespace(
            identifier.get("value") or identifier.get("name")
        )
    url = _first_text(
        item,
        "url",
        "jobUrl",
        "jobURL",
        "canonicalUrl",
        "externalUrl",
        "applyUrl",
        "applyURL",
    )
    if url:
        url = urljoin(page_url, url)
    if not title or (
        not url and not is_jsonld and not (allow_without_url and external_id)
    ):
        return {}
    location = _location_from_mapping(item)
    description = _first_text(
        item,
        "description",
        "jobDescription",
        "job_description",
        "content",
        "summary",
    )
    record: dict[str, Any] = {
        "external_id": external_id,
        "title": title,
        "canonical_url": url or page_url,
        "apply_url": _first_text(item, "applyUrl", "applyURL", "applicationUrl"),
        "description": description,
        "location": location,
        "locations": (location,) if location else (),
        "employment_type": _first_text(
            item, "employmentType", "employment_type", "timeType", "jobType"
        ),
        "workplace_type": _first_text(
            item, "jobLocationType", "workplaceType", "remoteType", "workLocationOption"
        ),
        "posted_at": _first_text(
            item,
            "datePosted",
            "postedAt",
            "posted_at",
            "externally_posted_ts",
            "publicationDate",
        ),
        "valid_through": _first_text(item, "validThrough", "expirationDate"),
        "department": _first_text(item, "department", "jobFunction", "category"),
        "skills": item.get("skills") or (),
    }
    salary = item.get("baseSalary") or item.get("salary") or {}
    if isinstance(salary, Mapping):
        value = salary.get("value") if isinstance(salary.get("value"), Mapping) else salary
        if isinstance(value, Mapping):
            record.update(
                {
                    "salary_min": value.get("minValue") or value.get("minimum"),
                    "salary_max": value.get("maxValue") or value.get("maximum"),
                    "salary_currency": salary.get("currency") or value.get("currency"),
                    "salary_interval": value.get("unitText") or value.get("interval"),
                }
            )
    return record


def _first_text(item: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = item.get(name)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (str, int, float)):
            text = html_to_text(value)
            if text:
                return text
    return ""


def _location_from_mapping(item: Mapping[str, Any]) -> str:
    for name in ("location", "locations", "jobLocation", "positionLocation"):
        value = item.get(name)
        result = _format_location(value)
        if result:
            return result
    return _first_text(item, "locationName", "locationText", "primaryLocation")


def _format_location(value: Any) -> str:
    if isinstance(value, str):
        return normalize_whitespace(value)
    if isinstance(value, list):
        return "; ".join(filter(None, (_format_location(item) for item in value)))
    if not isinstance(value, Mapping):
        return ""
    address = value.get("address") if isinstance(value.get("address"), Mapping) else value
    if isinstance(address, Mapping):
        parts = [
            address.get("addressLocality") or address.get("city"),
            address.get("addressRegion") or address.get("state"),
            address.get("addressCountry") or address.get("country"),
        ]
        return ", ".join(
            normalize_whitespace(part) for part in parts if normalize_whitespace(part)
        )
    return ""


def _anchor_candidates(
    anchors: Sequence[_Anchor],
    *,
    page_url: str,
    profile: PortalProfile,
    allowed_domains: Sequence[str],
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for anchor in anchors:
        url = canonicalize_url(urljoin(page_url, anchor.href))
        try:
            validate_fetch_url(url, allowed_domains=allowed_domains)
        except SourceFetchError:
            continue
        if not _matches_job_url(url, profile):
            continue
        title = normalize_whitespace(anchor.text)
        action_link = not title or title.casefold() in {
            "view more",
            "view job",
            "view details",
            "learn more",
            "apply",
            "apply now",
            "details",
        }
        if action_link:
            title = _title_from_context(anchor.context)
        if not title:
            continue
        external_id = _external_id_from_url(url)
        key = external_id or url
        record = {
            "external_id": external_id,
            "title": title,
            "canonical_url": url,
            "listing_context": anchor.context,
            "location": _location_from_context(anchor.context, title),
        }
        existing = candidates.get(key)
        if existing and action_link and existing.get("title"):
            # PeopleAdmin and several branded portals include both a title link
            # and a later "View Details" link to the same posting. Keep the
            # explicit title instead of replacing it with the full card text.
            record["title"] = existing["title"]
        candidates[key] = _merge_record(existing or {}, record)
    return candidates


def _pagination_urls(
    anchors: Sequence[_Anchor],
    *,
    page_url: str,
    profile: PortalProfile,
    allowed_domains: Sequence[str],
) -> tuple[str, ...]:
    values: list[str] = []
    for anchor in anchors:
        text = anchor.text.casefold()
        rel = anchor.rel.casefold()
        candidate = canonicalize_url(urljoin(page_url, anchor.href))
        if _matches_job_url(candidate, profile):
            continue
        parsed = urlsplit(candidate)
        query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
        looks_next = (
            "next" in rel
            or text in {"next", "next >", "next >>", ">", ">>"}
            or bool(query_keys & {"page", "p", "start", "offset", "joboffset", "pr"})
        )
        if not looks_next:
            continue
        try:
            validate_fetch_url(candidate, allowed_domains=allowed_domains)
        except SourceFetchError:
            continue
        if candidate != page_url and candidate not in values:
            values.append(candidate)
    return tuple(values)


def _matches_job_url(url: str, profile: PortalProfile) -> bool:
    parsed = urlsplit(url)
    target = parsed.path + ("?" + parsed.query if parsed.query else "")
    return any(pattern.search(target) for pattern in profile.job_url_patterns)


def _external_id_from_url(url: str) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query))
    for name in ("jobId", "job", "pid", "positionId", "opportunityId", "id"):
        if query.get(name):
            return normalize_whitespace(query[name])
    numbers = re.findall(r"(?:^|[/_-])(\d{3,})(?=$|[/_.?-])", parsed.path)
    if numbers:
        return numbers[-1]
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()[:24]


def _title_from_context(context: str) -> str:
    text = normalize_whitespace(context)
    if not text:
        return ""
    requisition_title = re.match(r"(.{3,180}?#\d{2,})\b", text)
    if requisition_title:
        return normalize_whitespace(requisition_title.group(1))
    for separator in (" | ", " · ", " — ", " - "):
        first = text.split(separator, 1)[0].strip()
        if 3 <= len(first) <= 180:
            return first
    return text[:180]


def _location_from_context(context: str, title: str) -> str:
    text = normalize_whitespace(context)
    if title and text.startswith(title):
        text = normalize_whitespace(text[len(title) :])
    match = re.search(
        r"(?:location|locations?|based in)\s*[:\-]?\s*([^|·]{2,120})",
        text,
        re.IGNORECASE,
    )
    if match:
        return normalize_whitespace(match.group(1))
    # Table-style branded portals often omit a literal "Location:" label.
    # Capture one or more city/state/country values from the row context.
    values = re.findall(
        r"([A-Za-z][A-Za-z .'-]{1,70},\s*[A-Z]{2}(?:,\s*(?:US|USA))?)",
        text,
    )
    return "; ".join(
        dict.fromkeys(normalize_whitespace(value) for value in values)
    )


def _merge_candidates(
    destination: dict[str, dict[str, Any]],
    incoming: Mapping[str, dict[str, Any]],
) -> None:
    for key, record in incoming.items():
        destination[key] = _merge_record(destination.get(key, {}), record)


def _merge_record(base: Mapping[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if value not in (None, "", [], {}, ()):
            if key == "description":
                current = html_to_text(merged.get(key))
                candidate = html_to_text(value)
                if len(candidate) >= len(current):
                    merged[key] = value
            else:
                merged[key] = value
    return merged


def _has_complete_detail(record: Mapping[str, Any]) -> bool:
    description = html_to_text(record.get("description"))
    return len(description) >= 500 and len(description.split()) >= 75


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)
