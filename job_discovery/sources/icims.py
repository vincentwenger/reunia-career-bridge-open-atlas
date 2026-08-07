from __future__ import annotations

from .common import (
    bounded_int as _bounded_int,
    bounded_float as _bounded_float,
    walk_json as _walk_json,
    jsonld_address_locations,
)

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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


_ICIMS_HOST_SUFFIX = ".icims.com"
_JOB_SEGMENT = re.compile(r"(?:^|/)jobs/([0-9]+)(?:/|$)", re.IGNORECASE)
_TOTAL_RESULTS = re.compile(
    r"(?:results?\s*)?(?:[0-9,]+)\s*[–—-]\s*[0-9,]+\s+of\s+([0-9,]+)",
    re.IGNORECASE,
)
_PAGE_OF = re.compile(r"page\s+([0-9]+)\s+of\s+([0-9]+)", re.IGNORECASE)
_POSTING_DATE = re.compile(
    r"(?:date\s+posted|posted\s+date|posting\s+date|posted\s+on)\s*:?\s*"
    r"([A-Za-z]{3,9}\s+[0-9]{1,2},?\s+[0-9]{4}|[0-9]{1,4}[-/.][0-9]{1,2}[-/.][0-9]{1,4})",
    re.IGNORECASE,
)
_REQUISITION_ID = re.compile(
    r"(?:requisition\s+id|req\s+id|job\s+id|\bid\b)\s*:?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,80})",
    re.IGNORECASE,
)
_LOCATION = re.compile(
    r"(?:job\s+locations?|location(?:\s+detail)?)\s*:?\s*(.+?)"
    r"(?=\s+(?:position\s+type|employment\s+type|requisition\s+id|req\s+id|category|categories|apply\s+now|read\s+more|overview|responsibilities)\b|$)",
    re.IGNORECASE,
)
_EMPLOYMENT = re.compile(
    r"(?:position\s+type|employment\s+type)\s*:?\s*(.+?)"
    r"(?=\s+(?:requisition\s+id|req\s+id|category|categories|job\s+locations?|location|apply\s+now|read\s+more|overview)\b|$)",
    re.IGNORECASE,
)
_CATEGORY = re.compile(
    r"(?:category|categories)\s*:?\s*(.+?)"
    r"(?=\s+(?:job\s+locations?|location|position\s+type|employment\s+type|requisition\s+id|req\s+id|apply\s+now|read\s+more|overview)\b|$)",
    re.IGNORECASE,
)
_CONTAINER_TAGS = {"article", "li", "tr", "section"}
_CONTAINER_CLASS_HINT = re.compile(
    r"(?:icims[_-]?(?:job|jobs|search)|job[_-]?(?:result|item|row|card|listing)|search[_-]?(?:result|item|row))",
    re.IGNORECASE,
)
_DETAIL_CLASS_HINT = re.compile(
    r"(?:icims[_-]?(?:jobcontent|jobdescription|joboverview)|job[_-]?(?:content|description|details|overview)|description)",
    re.IGNORECASE,
)
_GENERIC_ANCHOR_TEXT = {
    "apply",
    "apply now",
    "details",
    "job details",
    "read more",
    "view job",
    "view details",
    "english (us)",
    "english (uk)",
}


@dataclass(frozen=True, slots=True)
class IcmsCareerTarget:
    listing_url: str
    host: str


@dataclass(frozen=True, slots=True)
class _Anchor:
    href: str
    text: str
    context: str
    rel: str = ""


class _IcmsHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self.documents: list[Any] = []
        self.text_parts: list[str] = []
        self.title_candidates: list[str] = []
        self.detail_sections: list[str] = []
        self.meta: dict[str, str] = {}
        self._containers: list[dict[str, Any]] = []
        self._anchor: dict[str, Any] | None = None
        self._heading: dict[str, Any] | None = None
        self._script_mode = ""
        self._script_parts: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if name == "meta":
            key = normalize_whitespace(
                attributes.get("property") or attributes.get("name")
            ).casefold()
            value = normalize_whitespace(attributes.get("content"))
            if key and value and key not in self.meta:
                self.meta[key] = value
            return
        if name == "script":
            script_type = attributes.get("type", "").casefold()
            if "ld+json" in script_type:
                self._script_mode = "json"
                self._script_parts = []
            else:
                self._blocked_depth += 1
            return
        if name in {"style", "noscript", "template", "svg", "iframe", "object"}:
            self._blocked_depth += 1
            return
        class_id = f"{attributes.get('class', '')} {attributes.get('id', '')}"
        is_container = name in _CONTAINER_TAGS or (
            name == "div" and _CONTAINER_CLASS_HINT.search(class_id)
        )
        if is_container:
            self._containers.append(
                {
                    "tag": name,
                    "parts": [],
                    "detail": bool(_DETAIL_CLASS_HINT.search(class_id)),
                    "anchor_indices": [],
                }
            )
        if name == "a" and attributes.get("href"):
            self._anchor = {
                "href": attributes["href"],
                "rel": attributes.get("rel", ""),
                "parts": [],
            }
        if name in {"h1", "h2"}:
            self._heading = {"tag": name, "parts": []}

    def handle_data(self, data: str) -> None:
        if self._script_mode == "json":
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

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name == "script" and self._script_mode == "json":
            raw = "".join(self._script_parts).strip()
            self._script_mode = ""
            self._script_parts = []
            if raw:
                try:
                    self.documents.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
            return
        if name in {"script", "style", "noscript", "template", "svg", "iframe", "object"}:
            if self._blocked_depth:
                self._blocked_depth -= 1
            return
        if name == "a" and self._anchor is not None:
            context = ""
            if self._containers:
                context = normalize_whitespace(" ".join(self._containers[-1]["parts"]))
            anchor_index = len(self.anchors)
            self.anchors.append(
                _Anchor(
                    href=self._anchor["href"],
                    text=normalize_whitespace(" ".join(self._anchor["parts"])),
                    context=context,
                    rel=normalize_whitespace(self._anchor.get("rel", "")),
                )
            )
            for container in self._containers:
                container["anchor_indices"].append(anchor_index)
            self._anchor = None
        if self._heading is not None and self._heading["tag"] == name:
            value = normalize_whitespace(" ".join(self._heading["parts"]))
            if value:
                self.title_candidates.append(value)
            self._heading = None
        if self._containers and self._containers[-1]["tag"] == name:
            container = self._containers.pop()
            value = normalize_whitespace(" ".join(container["parts"]))
            if value:
                for anchor_index in container.get("anchor_indices", ()):
                    anchor = self.anchors[anchor_index]
                    if len(value) > len(anchor.context):
                        self.anchors[anchor_index] = _Anchor(
                            href=anchor.href,
                            text=anchor.text,
                            context=value,
                            rel=anchor.rel,
                        )
            if container.get("detail") and value:
                self.detail_sections.append(value)

    @property
    def page_text(self) -> str:
        return normalize_whitespace(" ".join(self.text_parts))


class IcmsJobSource:
    """Collect public job postings from iCIMS career portals.

    The connector reads only unauthenticated, public career-site HTML. It supports
    classic iCIMS portals under ``/jobs/search`` and newer branded paths whose job
    pages contain a numeric ``/jobs/<id>`` segment. It never uses customer API
    credentials or candidate/application endpoints.
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
        validate_source_policy(source, expected_type=JobSourceType.ICIMS)
        target = parse_icims_careers_url(source.careers_url)
        allowed_domains = (target.host,)
        validate_fetch_url(target.listing_url, allowed_domains=allowed_domains)

        max_pages = _bounded_int(source.filters.get("max_pages"), 20, 1, 50)
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)
        page_size = _bounded_int(source.filters.get("page_size"), 10, 1, 100)
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"), max_jobs, 0, max_jobs
        )
        budget = _bounded_float(
            source.filters.get("fetch_budget_seconds"), 0.0, 0.0, 300.0
        )
        deadline = self.clock() + budget if budget > 0 else None

        pending = [target.listing_url]
        visited: set[str] = set()
        postings: dict[str, dict[str, str]] = {}
        inferred_total: int | None = None

        while pending and len(visited) < max_pages and len(postings) < max_jobs:
            if deadline is not None and self.clock() >= deadline:
                break
            page_url = pending.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            html = self._fetch_html(source, page_url, allowed_domains)
            parser = _IcmsHtmlParser()
            parser.feed(html)
            parser.close()

            total_match = _TOTAL_RESULTS.search(parser.page_text)
            if total_match:
                inferred_total = int(total_match.group(1).replace(",", ""))

            before = len(postings)
            for anchor in parser.anchors:
                candidate = canonicalize_url(urljoin(page_url, anchor.href))
                if not candidate or not _is_job_url(candidate, target.host):
                    continue
                title = _usable_title(anchor.text, candidate)
                if not title:
                    continue
                existing = postings.get(candidate)
                context = normalize_whitespace(anchor.context)
                if existing is None:
                    postings[candidate] = {"title": title, "context": context}
                else:
                    if _title_quality(title) > _title_quality(existing["title"]):
                        existing["title"] = title
                    if len(context) > len(existing["context"]):
                        existing["context"] = context
                if len(postings) >= max_jobs:
                    break

            for anchor in parser.anchors:
                candidate = canonicalize_url(urljoin(page_url, anchor.href))
                if not candidate or candidate in visited or candidate in pending:
                    continue
                if _is_pagination_link(anchor, candidate, target):
                    pending.append(candidate)
                    if len(visited) + len(pending) >= max_pages:
                        break

            added_on_page = len(postings) - before
            if not pending and added_on_page >= page_size:
                current_index = _page_index(page_url)
                next_index = current_index + 1
                if inferred_total is None or len(postings) < inferred_total:
                    generated = _with_page_index(target.listing_url, next_index)
                    if generated not in visited:
                        pending.append(generated)
            if added_on_page == 0 and len(visited) > 1:
                break
            page_match = _PAGE_OF.search(parser.page_text)
            if page_match and int(page_match.group(1)) >= int(page_match.group(2)):
                pending.clear()

        jobs: list[DiscoveredJob] = []
        seen_at = utc_now_iso()
        for index, (job_url, listing) in enumerate(postings.items()):
            if len(jobs) >= max_jobs:
                break
            context_detail = _detail_from_context(listing.get("context", ""))
            detail: dict[str, Any] = dict(context_detail)
            deferred = index >= detail_limit or (
                deadline is not None and self.clock() >= deadline
            )
            if not deferred:
                try:
                    fetched = self._fetch_detail(source, job_url, allowed_domains)
                except SourceFetchError as exc:
                    detail["detail_error"] = str(exc)
                else:
                    detail = _merge_detail(detail, fetched)

            title = normalize_whitespace(detail.get("title") or listing.get("title"))
            location = normalize_whitespace(detail.get("location"))
            description = html_to_text(detail.get("description"))
            if not description:
                description = normalize_whitespace(
                    f"{title}. {location}. Public iCIMS job posting."
                )
            external_id = (
                normalize_whitespace(detail.get("external_id")) or _external_id(job_url)
            )
            salary_min = parse_number(detail.get("salary_min"))
            salary_max = parse_number(detail.get("salary_max"))
            currency = normalize_whitespace(detail.get("salary_currency"))
            interval = normalize_whitespace(detail.get("salary_interval"))
            metadata: dict[str, Any] = {
                "source_platform": "iCIMS",
                "icims_host": target.host,
                "detail_status": (
                    "deferred"
                    if deferred
                    else "failed"
                    if detail.get("detail_error")
                    else "complete"
                ),
            }
            metadata.update(
                dict(detail.get("metadata"))
                if isinstance(detail.get("metadata"), Mapping)
                else {}
            )
            if detail.get("detail_error"):
                metadata["detail_error"] = normalize_whitespace(detail["detail_error"])[
                    :1000
                ]
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
                    canonical_url=job_url,
                    apply_url=canonicalize_url(detail.get("apply_url") or job_url),
                    posted_at=normalize_iso_timestamp(
                        _parse_icims_date(detail.get("posted_at"))
                    ),
                    valid_through=normalize_iso_timestamp(
                        _parse_icims_date(detail.get("valid_through"))
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

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        if job.source_type is not JobSourceType.ICIMS:
            raise ValueError("fetch_job_description requires an iCIMS job")
        host = (urlsplit(job.canonical_url).hostname or "").casefold()
        source = CompanySource(
            id=job.source_id,
            owner_id=job.owner_id,
            company_name=job.company,
            careers_url=job.canonical_url,
            source_type=JobSourceType.ICIMS,
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
        parser = _IcmsHtmlParser()
        parser.feed(html)
        parser.close()
        for document in parser.documents:
            for item in _walk_json(document):
                if _is_jobposting(item):
                    return _detail_from_jsonld(item, page_url=url)

        page_text = parser.page_text
        context_text = page_text
        for section in parser.detail_sections:
            context_text = context_text.replace(section, " ")
        context = _detail_from_context(context_text)
        title = _detail_title(parser, url)
        description = _best_description(parser, title)
        date_match = _POSTING_DATE.search(page_text)
        context.update(
            {
                "title": title,
                "description": description,
                "posted_at": date_match.group(1) if date_match else "",
                "external_id": context.get("external_id") or _external_id(url),
                "apply_url": url,
                "metadata": {"html_fallback": True},
            }
        )
        return context

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


def parse_icims_careers_url(careers_url: str) -> IcmsCareerTarget:
    """Normalize a public iCIMS career-page or job URL to its listing URL."""

    url = canonicalize_url(careers_url)
    if not url:
        raise ValueError("iCIMS requires a public career-page URL")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    if host != "icims.com" and not host.endswith(_ICIMS_HOST_SUFFIX):
        raise ValueError("iCIMS career URLs must use an icims.com hostname")

    parts = [part for part in parsed.path.split("/") if part]
    lowered = [part.casefold() for part in parts]
    listing_parts: list[str]
    if "jobs" in lowered:
        index = lowered.index("jobs")
        after = lowered[index + 1 :]
        if after and after[0] == "search":
            listing_parts = parts[: index + 2]
        elif after and after[0].isdigit():
            is_classic_detail = len(after) >= 2 and after[-1] == "job"
            listing_parts = parts[: index + 1] + (["search"] if is_classic_detail else [])
        else:
            listing_parts = parts[: index + 1]
    elif parts:
        listing_parts = parts + ["jobs"]
    else:
        listing_parts = ["jobs", "search"]

    path = "/" + "/".join(listing_parts)
    listing_url = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    )
    return IcmsCareerTarget(listing_url=listing_url, host=host)



def _jsonld_locations(value: Any) -> tuple[str, ...]:
    return jsonld_address_locations(
        value,
        address_fields=(
            "streetAddress",
            "addressLocality",
            "addressRegion",
            "postalCode",
            "addressCountry",
        ),
    )

def _is_job_url(url: str, host: str) -> bool:
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != host:
        return False
    return _JOB_SEGMENT.search(parsed.path) is not None


def _is_pagination_link(anchor: _Anchor, url: str, target: IcmsCareerTarget) -> bool:
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != target.host or _is_job_url(url, target.host):
        return False
    target_path = urlsplit(target.listing_url).path.rstrip("/").casefold()
    candidate_path = parsed.path.rstrip("/").casefold()
    if candidate_path != target_path:
        return False
    text = anchor.text.casefold()
    rel = anchor.rel.casefold()
    query = {key.casefold(): value for key, value in parse_qsl(parsed.query)}
    return (
        "next" in rel
        or text in {"next", "next page", ">", "›", "»"}
        or "pr" in query
        or "page" in query
    )


def _page_index(url: str) -> int:
    query = parse_qs(urlsplit(url).query)
    for key in ("pr", "page"):
        values = query.get(key)
        if values:
            try:
                return max(0, int(values[0]))
            except (TypeError, ValueError):
                pass
    return 0


def _with_page_index(url: str, page_index: int) -> str:
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in {"pr", "page"}
    ]
    query.append(("pr", str(max(0, int(page_index)))))
    return canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    )


def _usable_title(value: str, url: str) -> str:
    title = normalize_whitespace(value)
    if title.casefold() in _GENERIC_ANCHOR_TEXT or _looks_like_locale(title):
        title = ""
    if not title:
        title = _title_from_job_url(url)
    return title


def _title_quality(value: str) -> tuple[int, int]:
    title = normalize_whitespace(value)
    return (0 if title.casefold() in _GENERIC_ANCHOR_TEXT else 1, len(title))


def _looks_like_locale(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]{2,20}\s*\([A-Za-z]{2,20}\)", value))


def _title_from_job_url(url: str) -> str:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    lowered = [part.casefold() for part in parts]
    if "jobs" not in lowered:
        return ""
    index = lowered.index("jobs")
    if index + 2 < len(parts) and parts[index + 1].isdigit():
        slug = parts[index + 2]
        if slug.casefold() != "job":
            return normalize_whitespace(slug.replace("-", " ").replace("_", " "))
    return ""


def _external_id(url: str) -> str:
    match = _JOB_SEGMENT.search(urlsplit(url).path)
    if match:
        return match.group(1)
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _detail_from_context(context: str) -> dict[str, Any]:
    text = normalize_whitespace(context)
    result: dict[str, Any] = {}
    location = _match_group(_LOCATION, text)
    employment = _match_group(_EMPLOYMENT, text)
    category = _match_group(_CATEGORY, text)
    requisition = _match_group(_REQUISITION_ID, text)
    if location:
        result["location"] = _clean_field(location)
        result["locations"] = (result["location"],)
        result["workplace_type"] = _workplace_from_context(text, result["location"])
    if employment:
        employment_value = _clean_field(employment)
        employment_value = re.sub(
            r"^(?:regular|permanent)\s+", "", employment_value, flags=re.IGNORECASE
        )
        result["employment_type"] = normalize_whitespace(employment_value)
    if category:
        result["department"] = _clean_field(category)
    if requisition:
        result["external_id"] = _clean_field(requisition)
    return result


def _match_group(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return normalize_whitespace(match.group(1)) if match else ""


def _clean_field(value: str) -> str:
    text = normalize_whitespace(value)
    text = re.sub(
        r"\b(?:apply now|read more|view job|job details|save job)\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return normalize_whitespace(text)[:500]


def _workplace_from_context(context: str, location: str) -> str:
    text = f"{context} {location}".casefold()
    if "hybrid" in text:
        return "Hybrid"
    if "remote" in text or "telecommute" in text:
        return "Remote"
    if "on-site" in text or "onsite" in text or "on site" in text:
        return "Onsite"
    return ""


def _detail_title(parser: _IcmsHtmlParser, url: str) -> str:
    candidates = list(parser.title_candidates)
    for key in ("og:title", "twitter:title"):
        if parser.meta.get(key):
            candidates.append(parser.meta[key])
    for candidate in candidates:
        title = normalize_whitespace(candidate)
        title = re.sub(r"\s+in\s+.+?\s*\|\s*iCIMS.*$", "", title, flags=re.IGNORECASE)
        title = re.sub(r"\s*\|\s*.+$", "", title)
        if title and title.casefold() not in {"job details", "job description"}:
            return title
    return _title_from_job_url(url)


def _best_description(parser: _IcmsHtmlParser, title: str) -> str:
    candidates = [normalize_whitespace(value) for value in parser.detail_sections]
    meta_description = parser.meta.get("description") or parser.meta.get("og:description")
    if meta_description:
        candidates.append(normalize_whitespace(meta_description))
    candidates = [value for value in candidates if value and value != title]
    if candidates:
        return max(candidates, key=lambda value: (len(value.split()), len(value)))
    return parser.page_text


def _merge_detail(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if value not in (None, "", (), [], {}):
            merged[key] = value
    return merged


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
        "department": item.get("industry") or item.get("occupationalCategory") or "",
        "salary_min": value.get("minValue") or value.get("value"),
        "salary_max": value.get("maxValue") or value.get("value"),
        "salary_currency": salary.get("currency") or "",
        "salary_interval": value.get("unitText") or "",
        "metadata": {"jsonld": True},
    }


def _parse_icims_date(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    text = normalize_whitespace(value)
    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
