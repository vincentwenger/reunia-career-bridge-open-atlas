from __future__ import annotations

from .common import (
    bounded_int as _bounded_int,
    bounded_float as _bounded_float,
    walk_json as _walk_json,
    jsonld_address_locations as _jsonld_locations,
)

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import parse_qsl, parse_qs, urlencode, urljoin, urlsplit, urlunsplit

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
    CompanyRateLimiter,
    HttpClient,
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


_JOB_PATH = re.compile(r"(?:^|/)job(?:/|$)", re.IGNORECASE)
_TOTAL_RESULTS = re.compile(r"results?\s+\d+\s*[–—-]\s*\d+\s+of\s+(\d+)", re.IGNORECASE)
_POSTING_DATE = re.compile(
    r"(?:posting\s+(?:start\s+)?date|date\s+posted|posted\s+on)\s*:?\s*"
    r"([0-9]{1,4}[-/.][0-9]{1,2}[-/.][0-9]{1,4})",
    re.IGNORECASE,
)
_CONTAINER_TAGS = {"tr", "li", "article"}
_CONTAINER_CLASS_HINT = re.compile(r"(?:job|search)[-_ ]?(?:result|item|row|card)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _Anchor:
    href: str
    text: str
    context: str
    rel: str = ""


class _SuccessFactorsHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self.documents: list[Any] = []
        self.text_parts: list[str] = []
        self._containers: list[dict[str, Any]] = []
        self._anchor: dict[str, Any] | None = None
        self._in_jsonld = False
        self._json_parts: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if name in {"script", "style", "noscript", "template", "svg"}:
            if name == "script" and "ld+json" in attributes.get("type", "").casefold():
                self._in_jsonld = True
                self._json_parts = []
            else:
                self._blocked_depth += 1
            return
        is_container = name in _CONTAINER_TAGS or (
            name == "div" and _CONTAINER_CLASS_HINT.search(attributes.get("class", ""))
        )
        if is_container:
            self._containers.append({"tag": name, "parts": []})
        if name == "a" and attributes.get("href"):
            self._anchor = {
                "href": attributes["href"],
                "rel": attributes.get("rel", ""),
                "parts": [],
            }

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._json_parts.append(data)
            return
        if self._blocked_depth:
            return
        self.text_parts.append(data)
        for container in self._containers:
            container["parts"].append(data)
        if self._anchor is not None:
            self._anchor["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name == "script" and self._in_jsonld:
            raw = "".join(self._json_parts).strip()
            self._in_jsonld = False
            self._json_parts = []
            if raw:
                try:
                    self.documents.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
            return
        if name in {"script", "style", "noscript", "template", "svg"}:
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
        if self._containers and self._containers[-1]["tag"] == name:
            self._containers.pop()

    @property
    def page_text(self) -> str:
        return normalize_whitespace(" ".join(self.text_parts))


class SuccessFactorsJobSource:
    """Collect public postings from SAP SuccessFactors Career Site Builder pages.

    SuccessFactors career sites can use SAP-hosted or employer-owned domains, so
    this connector is URL-driven. It reads the public search-result HTML, follows
    bounded pagination, and enriches a configurable number of postings from their
    public detail pages. No tenant credentials or authenticated APIs are used.
    """

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
        self._robots: dict[str, _RobotsPolicy] = {}

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        validate_source_policy(source, expected_type=JobSourceType.SUCCESSFACTORS)
        listing_url = successfactors_search_url(source.careers_url)
        parsed = urlsplit(listing_url)
        host = (parsed.hostname or "").casefold()
        allowed_domains = (host,)
        validate_fetch_url(listing_url, allowed_domains=allowed_domains)

        max_pages = _bounded_int(source.filters.get("max_pages"), 20, 1, 50)
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)
        page_size = _bounded_int(source.filters.get("page_size"), 25, 10, 100)
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"), max_jobs, 0, max_jobs
        )
        budget = _bounded_float(source.filters.get("fetch_budget_seconds"), 0.0, 0.0, 300.0)
        deadline = self.clock() + budget if budget > 0 else None

        pending = [listing_url]
        visited: set[str] = set()
        postings: list[tuple[str, str, str]] = []
        seen_urls: set[str] = set()
        inferred_total: int | None = None

        while pending and len(visited) < max_pages and len(postings) < max_jobs:
            if deadline is not None and self.clock() >= deadline:
                break
            page_url = pending.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            html = self._fetch_html(source, page_url, allowed_domains)
            parser = _SuccessFactorsHtmlParser()
            parser.feed(html)
            parser.close()

            total_match = _TOTAL_RESULTS.search(parser.page_text)
            if total_match:
                inferred_total = int(total_match.group(1))

            added_on_page = 0
            for anchor in parser.anchors:
                candidate = canonicalize_url(urljoin(page_url, anchor.href))
                if not candidate or not _is_job_url(candidate, host) or candidate in seen_urls:
                    continue
                title = normalize_whitespace(anchor.text)
                if not title or title.casefold() in {"apply", "apply now", "view job", "details"}:
                    title = _title_from_job_url(candidate)
                if not title:
                    continue
                context = normalize_whitespace(anchor.context)
                postings.append((candidate, title, context))
                seen_urls.add(candidate)
                added_on_page += 1
                if len(postings) >= max_jobs:
                    break

            for anchor in parser.anchors:
                candidate = canonicalize_url(urljoin(page_url, anchor.href))
                if not candidate or candidate in visited or candidate in pending:
                    continue
                if _is_pagination_link(anchor, candidate, host):
                    pending.append(candidate)
                    if len(visited) + len(pending) >= max_pages:
                        break

            if not pending and added_on_page >= page_size:
                next_start = len(visited) * page_size
                if inferred_total is None or next_start < inferred_total:
                    generated = _with_startrow(listing_url, next_start)
                    if generated not in visited:
                        pending.append(generated)
            if added_on_page == 0 and len(visited) > 1:
                break

        jobs: list[DiscoveredJob] = []
        seen_at = utc_now_iso()
        for index, (job_url, listing_title, context) in enumerate(postings):
            detail: dict[str, Any] = {}
            deferred = index >= detail_limit or (deadline is not None and self.clock() >= deadline)
            if not deferred:
                try:
                    detail = self._fetch_detail(source, job_url, allowed_domains)
                except SourceFetchError as exc:
                    detail = {"detail_error": str(exc)}

            title = normalize_whitespace(detail.get("title") or listing_title)
            location = normalize_whitespace(detail.get("location") or _location_from_context(context, title))
            description = html_to_text(detail.get("description"))
            if not description:
                description = normalize_whitespace(
                    f"{title}. {location}. Public SAP SuccessFactors job posting."
                )
            external_id = normalize_whitespace(detail.get("external_id")) or _external_id(job_url)
            metadata = {
                "detail_status": "deferred" if deferred else ("complete" if detail else "failed"),
                "source_platform": "SAP SuccessFactors",
            }
            if detail.get("detail_error"):
                metadata["detail_error"] = detail["detail_error"]
            metadata.update(dict(detail.get("metadata") or {}))
            salary_min = parse_number(detail.get("salary_min"))
            salary_max = parse_number(detail.get("salary_max"))
            currency = normalize_whitespace(detail.get("salary_currency"))
            interval = normalize_whitespace(detail.get("salary_interval"))
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
                    employment_type=normalize_employment_type(detail.get("employment_type")),
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
                    canonical_url=job_url,
                    apply_url=canonicalize_url(detail.get("apply_url") or job_url),
                    posted_at=normalize_iso_timestamp(_parse_sf_date(detail.get("posted_at"))),
                    valid_through=normalize_iso_timestamp(_parse_sf_date(detail.get("valid_through"))),
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_type=source.source_type,
                    department=normalize_whitespace(detail.get("department")),
                    skills=normalize_string_list(detail.get("skills")),
                    metadata=metadata,
                )
            )
        return jobs

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        if job.source_type is not JobSourceType.SUCCESSFACTORS:
            raise ValueError("fetch_job_description requires a SuccessFactors job")
        host = (urlsplit(job.canonical_url).hostname or "").casefold()
        source = CompanySource(
            id=job.source_id,
            owner_id=job.owner_id,
            company_name=job.company,
            careers_url=job.canonical_url,
            source_type=JobSourceType.SUCCESSFACTORS,
            source_identifier="",
            filters={"min_request_interval_seconds": 0.0, "timeout_seconds": 8.0},
        )
        detail = self._fetch_detail(source, job.canonical_url, (host,))
        return html_to_text(detail.get("description"))

    def _fetch_detail(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: tuple[str, ...],
    ) -> dict[str, Any]:
        html = self._fetch_html(source, url, allowed_domains)
        parser = _SuccessFactorsHtmlParser()
        parser.feed(html)
        parser.close()
        for document in parser.documents:
            for item in _walk_json(document):
                if _is_jobposting(item):
                    return _detail_from_jsonld(item, page_url=url)
        text = parser.page_text
        date_match = _POSTING_DATE.search(text)
        return {
            "title": "",
            "description": text,
            "posted_at": date_match.group(1) if date_match else "",
            "external_id": _external_id(url),
            "apply_url": url,
        }

    def _fetch_html(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: tuple[str, ...],
    ) -> str:
        validate_fetch_url(url, allowed_domains=allowed_domains)
        if not self._robots_allowed(source, url, allowed_domains):
            raise SourceFetchError(f"robots.txt disallows crawling {url}")
        self.rate_limiter.wait(
            company_rate_limit_key(source),
            source_min_request_interval(source, default=0.5),
        )
        response = self.http.get(
            url,
            headers={"Accept": "text/html, application/xhtml+xml"},
            timeout=source_timeout(source),
            max_bytes=source_response_limit(source, default=DEFAULT_HTML_MAX_BYTES),
            max_redirects=source_redirect_limit(source),
            allowed_domains=allowed_domains,
        )
        validate_fetch_url(response.url, allowed_domains=allowed_domains)
        if not 200 <= response.status < 300:
            raise SourceFetchError(f"GET {url} returned HTTP {response.status}")
        content_type = response.headers.get("content-type", "").casefold()
        if content_type and not any(
            token in content_type for token in ("text/html", "application/xhtml+xml")
        ):
            raise SourceFetchError(f"GET {url} did not return HTML")
        return response.text()

    def _robots_allowed(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: tuple[str, ...],
    ) -> bool:
        parsed = urlsplit(url)
        key = f"{parsed.scheme}://{parsed.netloc}"
        policy = self._robots.get(key)
        if policy is None:
            robots_url = f"{key}/robots.txt"
            try:
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
            except (SourceFetchError, KeyError):
                policy = _RobotsPolicy.disallow_all()
            else:
                if 200 <= response.status < 300:
                    policy = _RobotsPolicy.parse(response.text(), ROBOTS_PRODUCT_TOKEN)
                elif 400 <= response.status < 500:
                    policy = _RobotsPolicy.allow_all()
                else:
                    policy = _RobotsPolicy.disallow_all()
            self._robots[key] = policy
        return policy.allowed(url)


def successfactors_search_url(careers_url: str) -> str:
    """Return the public job-search URL for a SuccessFactors career site."""

    url = canonicalize_url(careers_url)
    if not url:
        raise ValueError("SAP SuccessFactors requires a public career-page URL")
    parsed = urlsplit(url)
    path = parsed.path or "/"
    lowered = path.casefold()
    query = parse_qs(parsed.query)
    if "/search" in lowered or "/go/" in lowered or "company" in query:
        return url
    path = path.rstrip("/") + "/search/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _is_job_url(url: str, host: str) -> bool:
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != host:
        return False
    if _JOB_PATH.search(parsed.path):
        return True
    query = {key.casefold(): value for key, value in parse_qsl(parsed.query)}
    return any(key in query for key in ("jobid", "job_id", "job") ) or (
        "career_ns" in query and "job_listing" in query.get("career_ns", "").casefold()
    )


def _is_pagination_link(anchor: _Anchor, url: str, host: str) -> bool:
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != host or _is_job_url(url, host):
        return False
    text = anchor.text.casefold()
    rel = anchor.rel.casefold()
    query = parse_qs(parsed.query)
    return (
        "next" in rel
        or text in {"next", "next page", ">", "›", "»"}
        or "startrow" in {key.casefold() for key in query}
    )


def _with_startrow(url: str, startrow: int) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key.casefold() != "startrow"]
    query.append(("startrow", str(max(0, int(startrow)))))
    return canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    )


def _external_id(url: str) -> str:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    for key in ("jobId", "jobid", "job_id", "job"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    parts = [part for part in parsed.path.split("/") if part]
    if parts:
        match = re.match(r"([0-9]+)(?:-[A-Za-z_]+)?$", parts[-1])
        if match:
            return match.group(1)
        if len(parts) >= 2 and parts[-2]:
            return parts[-2]
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _title_from_job_url(url: str) -> str:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    if "job" in [part.casefold() for part in parts]:
        index = [part.casefold() for part in parts].index("job")
        if index + 1 < len(parts):
            return normalize_whitespace(parts[index + 1].replace("-", " ").replace("_", " "))
    return ""


def _location_from_context(context: str, title: str) -> str:
    text = normalize_whitespace(context)
    if title:
        text = re.sub(re.escape(title), " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:apply|view job|job details|save job)\b", " ", text, flags=re.IGNORECASE)
    text = normalize_whitespace(text)
    if not text or len(text) > 180:
        return ""
    return text


def _is_jobposting(item: Mapping[str, Any]) -> bool:
    value = item.get("@type")
    values = value if isinstance(value, list) else [value]
    return any(str(item_type or "").casefold() == "jobposting" for item_type in values)


def _detail_from_jsonld(item: Mapping[str, Any], *, page_url: str) -> dict[str, Any]:
    locations = _jsonld_locations(item.get("jobLocation"))
    location = ", ".join(locations)
    identifier = item.get("identifier")
    if isinstance(identifier, Mapping):
        external_id = identifier.get("value") or identifier.get("name") or ""
    else:
        external_id = identifier or ""
    salary = item.get("baseSalary") if isinstance(item.get("baseSalary"), Mapping) else {}
    value = salary.get("value") if isinstance(salary, Mapping) else {}
    if not isinstance(value, Mapping):
        value = {}
    return {
        "title": html_to_text(item.get("title")),
        "description": html_to_text(item.get("description")),
        "location": location,
        "locations": locations,
        "workplace_type": item.get("jobLocationType") or "",
        "employment_type": item.get("employmentType") or "",
        "posted_at": item.get("datePosted") or "",
        "valid_through": item.get("validThrough") or "",
        "external_id": normalize_whitespace(external_id) or _external_id(page_url),
        "apply_url": canonicalize_url(item.get("url") or page_url),
        "skills": item.get("skills") or item.get("qualifications") or (),
        "department": item.get("industry") or "",
        "salary_min": value.get("minValue") or value.get("value"),
        "salary_max": value.get("maxValue") or value.get("value"),
        "salary_currency": salary.get("currency") or "",
        "salary_interval": value.get("unitText") or "",
        "metadata": {"jsonld": True},
    }


def _parse_sf_date(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    text = normalize_whitespace(value)
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
