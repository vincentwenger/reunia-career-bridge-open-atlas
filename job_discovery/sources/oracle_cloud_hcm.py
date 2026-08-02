from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping
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
    DEFAULT_JSON_MAX_BYTES,
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


_SITE_PATH = re.compile(
    r"^(?P<prefix>.*?)/(?P<language>[A-Za-z]{2}(?:-[A-Za-z]{2})?)/sites/"
    r"(?P<site>[^/]+)(?:/(?P<section>jobs|requisitions|job)(?:/(?P<job_id>[^/?#]+))?)?/?$",
    re.IGNORECASE,
)
_JOB_PATH = re.compile(
    r"/(?:sites/[^/]+/)?job/(?P<job_id>[^/?#]+)(?:/|$)", re.IGNORECASE
)
_CONTAINER_TAGS = {"article", "li", "section", "tr"}
_CONTAINER_CLASS_HINT = re.compile(
    r"(?:job|requisition|search)[-_ ]?(?:result|item|row|card|tile)", re.IGNORECASE
)
_POSTING_DATE = re.compile(
    r"(?:posting\s+date|date\s+posted|posted\s+on)\s*:?\s*"
    r"([0-9]{1,4}[-/.][0-9]{1,2}[-/.][0-9]{1,4})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class OracleCloudHcmTarget:
    careers_url: str
    listing_url: str
    site_base_url: str
    language: str
    site: str
    listing_section: str


@dataclass(frozen=True, slots=True)
class _Anchor:
    href: str
    text: str
    context: str
    rel: str = ""


class _OracleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self.documents: list[Any] = []
        self.text_parts: list[str] = []
        self.title_candidates: list[str] = []
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
            key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
            ).strip().casefold()
            content = normalize_whitespace(attributes.get("content"))
            if key and content and key not in self.meta:
                self.meta[key] = content
            return
        if name == "script":
            script_type = attributes.get("type", "").casefold()
            if "json" in script_type or attributes.get("id", "").casefold() in {
                "__next_data__",
                "initial-state",
                "initial_state",
            }:
                self._script_mode = "json"
                self._script_parts = []
            else:
                self._blocked_depth += 1
            return
        if name in {"style", "noscript", "template", "svg"}:
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


class OracleCloudHcmJobSource:
    """Collect public jobs from Oracle Recruiting Candidate Experience sites.

    The connector is intentionally URL-driven and unauthenticated. It uses the
    same public Candidate Experience requisition resources loaded by the career
    site, with a bounded HTML/JSON-LD fallback for vanity domains or older sites.
    It does not accept tenant credentials or call authenticated recruiting APIs.
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
        validate_source_policy(source, expected_type=JobSourceType.ORACLE_CLOUD_HCM)
        target = parse_oracle_cloud_hcm_careers_url(source.careers_url)
        host = (urlsplit(target.listing_url).hostname or "").casefold()
        allowed_domains = (host,)
        validate_fetch_url(target.listing_url, allowed_domains=allowed_domains)

        max_pages = _bounded_int(source.filters.get("max_pages"), 20, 1, 50)
        max_jobs = _bounded_int(source.filters.get("max_jobs"), 1000, 1, 5000)
        detail_limit = _bounded_int(
            source.filters.get("detail_fetch_limit"), max_jobs, 0, max_jobs
        )
        budget = _bounded_float(
            source.filters.get("fetch_budget_seconds"), 0.0, 0.0, 300.0
        )
        deadline = self.clock() + budget if budget > 0 else None

        candidates = self._fetch_api_candidates(
            source,
            target,
            allowed_domains,
            max_pages=max_pages,
            max_jobs=max_jobs,
            deadline=deadline,
        )
        if not candidates:
            candidates = self._fetch_html_candidates(
                source,
                target,
                allowed_domains,
                max_pages=max_pages,
                max_jobs=max_jobs,
                deadline=deadline,
            )

        jobs: list[DiscoveredJob] = []
        seen_at = utc_now_iso()
        for index, record in enumerate(candidates.values()):
            if len(jobs) >= max_jobs:
                break
            detail = dict(record)
            detail_url = canonicalize_url(
                detail.get("canonical_url")
                or _job_url(target, detail.get("external_id"))
            )
            deferred = index >= detail_limit or (
                deadline is not None and self.clock() >= deadline
            )
            if detail_url and not deferred and not _record_has_complete_detail(detail):
                try:
                    fetched = self._fetch_detail(source, detail_url, allowed_domains, target)
                except SourceFetchError as exc:
                    detail["detail_error"] = str(exc)
                else:
                    detail = _merge_detail(detail, fetched)

            external_id = normalize_whitespace(detail.get("external_id")) or _external_id(
                detail_url
            )
            title = normalize_whitespace(detail.get("title"))
            if not external_id or not title or not detail_url:
                continue
            location = normalize_whitespace(detail.get("location"))
            description = _description_text(detail)
            if not description:
                description = normalize_whitespace(detail.get("listing_context"))
            salary_min = parse_number(detail.get("salary_min"))
            salary_max = parse_number(detail.get("salary_max"))
            currency = normalize_whitespace(detail.get("salary_currency"))
            interval = normalize_whitespace(detail.get("salary_interval"))
            metadata = {
                "oracle_cloud_hcm_site": target.site,
                "oracle_cloud_hcm_language": target.language,
                "oracle_cloud_hcm_listing_section": target.listing_section,
                "detail_status": (
                    "deferred"
                    if deferred and not _record_has_complete_detail(detail)
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
                    canonical_url=detail_url,
                    apply_url=canonicalize_url(detail.get("apply_url") or detail_url),
                    posted_at=normalize_iso_timestamp(
                        _parse_oracle_date(detail.get("posted_at"))
                    ),
                    valid_through=normalize_iso_timestamp(
                        _parse_oracle_date(detail.get("valid_through"))
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

    def _fetch_api_candidates(
        self,
        source: CompanySource,
        target: OracleCloudHcmTarget,
        allowed_domains: tuple[str, ...],
        *,
        max_pages: int,
        max_jobs: int,
        deadline: float | None,
    ) -> dict[str, dict[str, Any]]:
        if not _enabled_filter(source.filters.get("use_public_api", True)):
            return {}
        page_size = _bounded_int(source.filters.get("page_size"), 24, 1, 50)
        candidates: dict[str, dict[str, Any]] = {}
        for page_number in range(max_pages):
            if len(candidates) >= max_jobs:
                break
            if deadline is not None and self.clock() >= deadline:
                break
            offset = page_number * page_size
            api_url = _api_listing_url(
                target, limit=min(page_size, max_jobs - len(candidates)), offset=offset
            )
            try:
                payload = self._fetch_json(
                    source, api_url, allowed_domains, language=target.language
                )
            except (SourceFetchError, KeyError, ValueError, json.JSONDecodeError):
                break
            records = _job_records_from_document(
                payload, target=target, page_url=target.listing_url
            )
            for record in records:
                key = record.get("external_id") or record.get("canonical_url")
                if key and str(key) not in candidates:
                    candidates[str(key)] = record
                    if len(candidates) >= max_jobs:
                        break
            if not _api_has_more(payload, page_size=page_size, offset=offset):
                break
        return candidates

    def _fetch_html_candidates(
        self,
        source: CompanySource,
        target: OracleCloudHcmTarget,
        allowed_domains: tuple[str, ...],
        *,
        max_pages: int,
        max_jobs: int,
        deadline: float | None,
    ) -> dict[str, dict[str, Any]]:
        host = (urlsplit(target.listing_url).hostname or "").casefold()
        pending = [target.listing_url]
        visited: set[str] = set()
        candidates: dict[str, dict[str, Any]] = {}
        while pending and len(visited) < max_pages and len(candidates) < max_jobs:
            if deadline is not None and self.clock() >= deadline:
                break
            page_url = pending.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            html = self._fetch_html(source, page_url, allowed_domains)
            parser = _OracleHtmlParser()
            parser.feed(html)
            parser.close()

            for document in parser.documents:
                for record in _job_records_from_document(
                    document, target=target, page_url=page_url
                ):
                    key = record.get("external_id") or record.get("canonical_url")
                    if key and str(key) not in candidates:
                        candidates[str(key)] = record
                        if len(candidates) >= max_jobs:
                            break
                if len(candidates) >= max_jobs:
                    break

            if len(candidates) < max_jobs:
                for anchor in parser.anchors:
                    candidate_url = canonicalize_url(urljoin(page_url, anchor.href))
                    if not candidate_url or not _is_job_url(candidate_url, host):
                        continue
                    external_id = _external_id(candidate_url)
                    key = external_id or candidate_url
                    if key in candidates:
                        existing = candidates[key]
                        if not existing.get("title"):
                            existing["title"] = _title_from_anchor(anchor)
                        if not existing.get("location"):
                            existing["location"] = _location_from_context(
                                anchor.context, existing.get("title", "")
                            )
                        continue
                    title = _title_from_anchor(anchor)
                    candidates[key] = {
                        "external_id": external_id,
                        "canonical_url": candidate_url,
                        "apply_url": candidate_url,
                        "title": title,
                        "location": _location_from_context(anchor.context, title),
                        "listing_context": anchor.context,
                    }
                    if len(candidates) >= max_jobs:
                        break

            for anchor in parser.anchors:
                candidate_url = canonicalize_url(urljoin(page_url, anchor.href))
                if (
                    not candidate_url
                    or candidate_url in visited
                    or candidate_url in pending
                ):
                    continue
                if _is_pagination_link(anchor, candidate_url, host, target):
                    pending.append(candidate_url)
                    if len(visited) + len(pending) >= max_pages:
                        break
        return candidates

    def fetch_job_description(self, job: DiscoveredJob) -> str:
        if job.source_type is not JobSourceType.ORACLE_CLOUD_HCM:
            raise ValueError("fetch_job_description requires an Oracle Cloud HCM job")
        target = parse_oracle_cloud_hcm_careers_url(job.canonical_url)
        host = (urlsplit(job.canonical_url).hostname or "").casefold()
        source = CompanySource(
            id=job.source_id,
            owner_id=job.owner_id,
            company_name=job.company,
            careers_url=target.listing_url,
            source_type=JobSourceType.ORACLE_CLOUD_HCM,
            source_identifier="",
            filters={"min_request_interval_seconds": 0.0, "timeout_seconds": 8.0},
        )
        detail = self._fetch_detail(source, job.canonical_url, (host,), target)
        return _description_text(detail)

    def _fetch_detail(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: tuple[str, ...],
        target: OracleCloudHcmTarget,
    ) -> dict[str, Any]:
        external_id = _external_id(url)
        if _enabled_filter(source.filters.get("use_public_api", True)):
            api_url = _api_detail_url(target, external_id)
            try:
                payload = self._fetch_json(
                    source, api_url, allowed_domains, language=target.language
                )
            except (SourceFetchError, KeyError, ValueError, json.JSONDecodeError):
                pass
            else:
                records = _job_records_from_document(
                    payload, target=target, page_url=url
                )
                for record in records:
                    if normalize_whitespace(record.get("external_id")) == external_id:
                        return record
                if records:
                    return max(records, key=lambda item: len(_description_text(item)))

        html = self._fetch_html(source, url, allowed_domains)
        parser = _OracleHtmlParser()
        parser.feed(html)
        parser.close()
        records: list[dict[str, Any]] = []
        for document in parser.documents:
            records.extend(
                _job_records_from_document(document, target=target, page_url=url)
            )
        for record in records:
            if normalize_whitespace(record.get("external_id")) == external_id:
                return record
        if records:
            return max(records, key=lambda item: len(_description_text(item)))
        date_match = _POSTING_DATE.search(parser.page_text)
        return {
            "title": _detail_title(parser),
            "description": parser.meta.get("description") or parser.page_text,
            "posted_at": date_match.group(1) if date_match else "",
            "external_id": external_id,
            "canonical_url": url,
            "apply_url": url,
        }

    def _fetch_json(
        self,
        source: CompanySource,
        url: str,
        allowed_domains: tuple[str, ...],
        *,
        language: str,
    ) -> Any:
        validate_fetch_url(url, allowed_domains=allowed_domains)
        if not self._robots_allowed(source, url, allowed_domains):
            raise SourceFetchError(f"robots.txt disallows crawling {url}")
        self.rate_limiter.wait(
            company_rate_limit_key(source),
            source_min_request_interval(source, default=0.5),
        )
        response = self.http.get(
            url,
            headers={
                "Accept": (
                    "application/json, "
                    "application/vnd.oracle.adf.resourcecollection+json, "
                    "application/vnd.oracle.adf.resourceitem+json"
                ),
                "Accept-Language": f"{language},en;q=0.8",
                "Ora-Irc-Language": language,
            },
            timeout=source_timeout(source),
            max_bytes=source_response_limit(source, default=DEFAULT_JSON_MAX_BYTES),
            max_redirects=source_redirect_limit(source),
            allowed_domains=allowed_domains,
        )
        validate_fetch_url(response.url, allowed_domains=allowed_domains)
        if not 200 <= response.status < 300:
            raise SourceFetchError(f"GET {url} returned HTTP {response.status}")
        content_type = response.headers.get("content-type", "").casefold()
        if content_type and "json" not in content_type:
            raise SourceFetchError(f"GET {url} did not return JSON")
        try:
            return json.loads(response.text())
        except json.JSONDecodeError as exc:
            raise SourceFetchError(f"GET {url} returned invalid JSON") from exc

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
            headers={
                "Accept": "text/html, application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.8",
            },
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


def parse_oracle_cloud_hcm_careers_url(careers_url: str) -> OracleCloudHcmTarget:
    """Validate and canonicalize a public Oracle Candidate Experience URL."""

    url = canonicalize_url(careers_url)
    if not url:
        raise ValueError("Oracle Cloud HCM requires a public Candidate Experience URL")
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    match = _SITE_PATH.match(path)
    if match is None:
        raise ValueError(
            "Oracle Cloud HCM URL must contain /<language>/sites/<site>/jobs, "
            "for example /hcmUI/CandidateExperience/en/sites/CX_1/jobs"
        )
    prefix = (match.group("prefix") or "").rstrip("/")
    language = match.group("language")
    site = match.group("site")
    section = (match.group("section") or "jobs").casefold()
    listing_section = section if section in {"jobs", "requisitions"} else "jobs"
    site_path = f"{prefix}/{language}/sites/{site}"
    listing_path = f"{site_path}/{listing_section}"
    site_base_url = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, site_path, "", ""))
    )
    listing_url = canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, listing_path, "", ""))
    )
    return OracleCloudHcmTarget(
        careers_url=site_base_url,
        listing_url=listing_url,
        site_base_url=site_base_url,
        language=language,
        site=site,
        listing_section=listing_section,
    )


def _api_listing_url(
    target: OracleCloudHcmTarget, *, limit: int, offset: int
) -> str:
    parsed = urlsplit(target.listing_url)
    finder = (
        f"findReqs;siteNumber={target.site},"
        "facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;"
        "CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS,"
        f"limit={limit},offset={offset}"
    )
    query = urlencode(
        (
            ("onlyData", "true"),
            ("expand", "requisitionList.secondaryLocations,flexFieldsFacet.values"),
            ("finder", finder),
        )
    )
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            "/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
            query,
            "",
        )
    )


def _api_detail_url(target: OracleCloudHcmTarget, external_id: str) -> str:
    parsed = urlsplit(target.listing_url)
    finder = f'ById;Id="{normalize_whitespace(external_id)}",siteNumber={target.site}'
    query = urlencode((("onlyData", "true"), ("finder", finder)))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
            query,
            "",
        )
    )


def _api_has_more(payload: Any, *, page_size: int, offset: int) -> bool:
    for item in _walk_json(payload):
        folded = _casefold_mapping(item)
        if "hasmore" in folded:
            value = folded["hasmore"]
            if isinstance(value, bool):
                return value
            return str(value).strip().casefold() in {"1", "true", "yes"}
        total = parse_number(folded.get("totalresults"))
        if total is not None:
            return offset + page_size < total
    return False


def _enabled_filter(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() not in {"0", "false", "no", "off"}


def _job_records_from_document(
    document: Any,
    *,
    target: OracleCloudHcmTarget,
    page_url: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _walk_json(document):
        if _is_jobposting(item):
            record = _record_from_jsonld(item, page_url=page_url, target=target)
        elif _looks_like_oracle_job(item):
            record = _record_from_oracle_mapping(item, page_url=page_url, target=target)
        else:
            continue
        key = normalize_whitespace(record.get("external_id")) or canonicalize_url(
            record.get("canonical_url")
        )
        if key and key not in seen:
            seen.add(key)
            records.append(record)
    return records


def _walk_json(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _is_jobposting(item: Mapping[str, Any]) -> bool:
    value = item.get("@type")
    values = value if isinstance(value, list) else [value]
    return any(str(item_type or "").casefold() == "jobposting" for item_type in values)


def _looks_like_oracle_job(item: Mapping[str, Any]) -> bool:
    keys = {str(key).casefold() for key in item}
    has_title = any(
        key in keys
        for key in (
            "title",
            "requisitiontitle",
            "otherrequisitiontitle",
            "jobtitle",
        )
    )
    has_id = any(
        key in keys
        for key in (
            "id",
            "requisitionid",
            "requisitionnumber",
            "jobid",
            "searchid",
        )
    )
    oracle_hint = any(
        key in keys
        for key in (
            "externaldescriptionstr",
            "externaldescriptionhtml",
            "externalpostedstartdate",
            "postingstartdate",
            "primarylocation",
            "primaryworklocationname",
            "applyurl",
            "hotjobflag",
        )
    )
    return has_title and has_id and oracle_hint


def _record_from_jsonld(
    item: Mapping[str, Any],
    *,
    page_url: str,
    target: OracleCloudHcmTarget,
) -> dict[str, Any]:
    locations = _jsonld_locations(item.get("jobLocation"))
    identifier = item.get("identifier")
    if isinstance(identifier, Mapping):
        external_id = identifier.get("value") or identifier.get("name") or ""
    else:
        external_id = identifier or ""
    url = _same_host_url(item.get("url"), page_url)
    external_id = normalize_whitespace(external_id) or _external_id(url)
    if not url or not _is_job_url(
        url, (urlsplit(target.site_base_url).hostname or "").casefold()
    ):
        url = _job_url(target, external_id)
    salary = item.get("baseSalary") if isinstance(item.get("baseSalary"), Mapping) else {}
    value = salary.get("value") if isinstance(salary, Mapping) else {}
    if not isinstance(value, Mapping):
        value = {}
    return {
        "title": html_to_text(item.get("title")),
        "description": html_to_text(item.get("description")),
        "location": ", ".join(locations),
        "locations": locations,
        "workplace_type": item.get("jobLocationType") or "",
        "employment_type": item.get("employmentType") or "",
        "posted_at": item.get("datePosted") or "",
        "valid_through": item.get("validThrough") or "",
        "external_id": external_id,
        "canonical_url": url,
        "apply_url": _same_host_url(item.get("url"), page_url) or url,
        "skills": item.get("skills") or item.get("qualifications") or (),
        "department": item.get("industry") or "",
        "salary_min": value.get("minValue") or value.get("value"),
        "salary_max": value.get("maxValue") or value.get("value"),
        "salary_currency": salary.get("currency") or "",
        "salary_interval": value.get("unitText") or "",
        "metadata": {"jsonld": True},
    }


def _record_from_oracle_mapping(
    item: Mapping[str, Any],
    *,
    page_url: str,
    target: OracleCloudHcmTarget,
) -> dict[str, Any]:
    value = _casefold_mapping(item)
    external_id = _first(
        value,
        "requisitionnumber",
        "requisitionid",
        "jobid",
        "id",
        "searchid",
    )
    canonical_url = _same_host_url(
        _first(value, "joburl", "canonicalurl", "url", "detailurl"),
        page_url,
    )
    host = (urlsplit(target.site_base_url).hostname or "").casefold()
    if not canonical_url or not _is_job_url(canonical_url, host):
        canonical_url = _job_url(target, external_id)
    apply_url = _same_host_url(
        _first(value, "applyurl", "apply_url"), page_url
    )

    locations = _oracle_locations(item)
    location = normalize_whitespace(
        _first(
            value,
            "primarylocation",
            "primaryworklocationname",
            "location",
            "locationname",
        )
    ) or (locations[0] if locations else "")
    description_values = [
        _first(
            value,
            "externaldescriptionhtml",
            "externaldescriptionstr",
            "externalshortdescription",
            "externalshortdescriptionstr",
            "shortdescriptionstr",
            "description",
        ),
        _first(
            value,
            "externalresponsibilitieshtml",
            "externalresponsibilitiesstr",
            "responsibilities",
        ),
        _first(
            value,
            "externalqualificationshtml",
            "externalqualificationsstr",
            "qualifications",
        ),
        _first(value, "shortdescription", "summary"),
    ]
    skills = item.get("skills") or value.get("skills") or ()
    return {
        "title": html_to_text(
            _first(value, "title", "requisitiontitle", "otherrequisitiontitle", "jobtitle")
        ),
        "description": " ".join(
            text for text in (html_to_text(part) for part in description_values) if text
        ),
        "location": location,
        "locations": locations,
        "workplace_type": _first(
            value,
            "workplacetypecode",
            "workplacetype",
            "joblocationtype",
        ),
        "employment_type": _first(
            value,
            "fulltimeorparttime",
            "jobschedule",
            "jobtype",
            "contracttype",
            "regularortemporary",
        ),
        "posted_at": _first(
            value,
            "externalpostedstartdate",
            "externalpublishedjobstartdate",
            "postingstartdate",
            "posteddate",
            "dateposted",
        ),
        "valid_through": _first(
            value,
            "externalpostedenddate",
            "externalpublishedjobenddate",
            "postingenddate",
            "validthrough",
        ),
        "external_id": normalize_whitespace(external_id) or _external_id(canonical_url),
        "canonical_url": canonical_url,
        "apply_url": apply_url or canonical_url,
        "skills": skills,
        "department": _first(
            value,
            "department",
            "organization",
            "jobfunction",
            "jobfamily",
            "businessunit",
        ),
        "salary_text": _first(value, "salarytext", "salarysummary", "compensation"),
        "salary_min": _first(value, "salarymin", "minimumsalary"),
        "salary_max": _first(value, "salarymax", "maximumsalary"),
        "salary_currency": _first(value, "salarycurrency", "compensationcurrency"),
        "salary_interval": _first(value, "salaryperiodcode", "salaryinterval"),
        "metadata": {
            "oracle_embedded_record": True,
            "hot_job": _first(value, "hotjob", "hotjobflag"),
            "requisition_id": _first(value, "requisitionid"),
            "requisition_number": _first(value, "requisitionnumber"),
        },
    }


def _casefold_mapping(item: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).casefold(): value for key, value in item.items()}


def _first(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        candidate = value.get(key.casefold())
        if candidate not in (None, "", [], {}):
            return candidate
    return ""


def _oracle_locations(item: Mapping[str, Any]) -> tuple[str, ...]:
    value = _casefold_mapping(item)
    candidates: list[Any] = [
        _first(value, "primarylocation", "primaryworklocationname", "location", "locationname"),
        item.get("secondaryLocations") or item.get("secondarylocations"),
        item.get("otherWorkLocations") or item.get("otherworklocations"),
        item.get("workLocation") or item.get("worklocation"),
        item.get("locations"),
    ]
    parts = [
        _first(value, "primaryworklocationcity", "townorcity"),
        _first(value, "primarylocationleveltwoname", "region2", "statename"),
        _first(value, "primarylocationcountryname", "country"),
    ]
    combined = ", ".join(
        normalize_whitespace(part)
        for part in parts
        if normalize_whitespace(part)
    )
    if combined:
        candidates.append(combined)
    locations: list[str] = []
    for candidate in candidates:
        for text in _location_strings(candidate):
            key = text.casefold()
            if text and key not in {item.casefold() for item in locations}:
                locations.append(text)
    return tuple(locations)


def _location_strings(value: Any) -> Iterable[str]:
    if value in (None, ""):
        return
    if isinstance(value, Mapping):
        folded = _casefold_mapping(value)
        text = normalize_whitespace(
            _first(folded, "primarylocation", "location", "locationname", "name")
        )
        if not text:
            pieces = [
                _first(folded, "townorcity", "city"),
                _first(folded, "region2", "state", "region"),
                _first(folded, "country", "countryname"),
            ]
            text = ", ".join(
                normalize_whitespace(piece)
                for piece in pieces
                if normalize_whitespace(piece)
            )
        if text:
            yield text
        return
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _location_strings(item)
        return
    text = normalize_whitespace(value)
    if text:
        yield text


def _jsonld_locations(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, list) else [value]
    locations: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        address = item.get("address") if isinstance(item.get("address"), Mapping) else {}
        pieces = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        text = ", ".join(
            normalize_whitespace(piece)
            for piece in pieces
            if normalize_whitespace(piece)
        )
        if text and text.casefold() not in {existing.casefold() for existing in locations}:
            locations.append(text)
    return tuple(locations)


def _record_has_complete_detail(record: Mapping[str, Any]) -> bool:
    description = _description_text(record)
    return len(description) >= 500 or len(description.split()) >= 75


def _description_text(record: Mapping[str, Any]) -> str:
    return html_to_text(record.get("description"))


def _merge_detail(existing: Mapping[str, Any], fetched: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in fetched.items():
        if value not in (None, "", [], {}, ()):
            if key == "metadata" and isinstance(value, Mapping):
                metadata = dict(merged.get("metadata") or {})
                metadata.update(value)
                merged[key] = metadata
            else:
                merged[key] = value
    return merged


def _same_host_url(value: Any, base_url: str) -> str:
    raw = normalize_whitespace(value)
    if not raw:
        return ""
    candidate = canonicalize_url(urljoin(base_url, raw))
    if not candidate:
        return ""
    candidate_host = (urlsplit(candidate).hostname or "").casefold()
    base_host = (urlsplit(base_url).hostname or "").casefold()
    return candidate if candidate_host and candidate_host == base_host else ""


def _is_job_url(url: str, host: str) -> bool:
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != host:
        return False
    return _JOB_PATH.search(parsed.path) is not None and "/share/" not in parsed.path.casefold()


def _is_pagination_link(
    anchor: _Anchor,
    url: str,
    host: str,
    target: OracleCloudHcmTarget,
) -> bool:
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != host or _is_job_url(url, host):
        return False
    if not parsed.path.casefold().startswith(urlsplit(target.site_base_url).path.casefold()):
        return False
    text = anchor.text.casefold()
    rel = anchor.rel.casefold()
    query = {key.casefold() for key, _ in parse_qsl(parsed.query)}
    return (
        "next" in rel
        or text in {"next", "next page", "show more results", ">", "›", "»"}
        or bool(query.intersection({"page", "offset", "start", "startrow"}))
    )


def _title_from_anchor(anchor: _Anchor) -> str:
    title = normalize_whitespace(anchor.text)
    if title.casefold() in {
        "apply",
        "apply now",
        "view job",
        "view details",
        "job details",
        "share",
    }:
        title = ""
    return title


def _location_from_context(context: str, title: str) -> str:
    text = normalize_whitespace(context)
    if title:
        text = re.sub(re.escape(title), " ", text, flags=re.IGNORECASE)
    text = re.sub(
        (
            r"\b(?:apply|view job|job details|save job|posting date|hot job|"
            r"trending|be the first to apply)\b"
        ),
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = normalize_whitespace(text)
    if not text or len(text) > 180:
        return ""
    return text


def _external_id(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    match = _JOB_PATH.search(parsed.path)
    if match and match.group("job_id"):
        return normalize_whitespace(match.group("job_id"))
    query = parse_qs(parsed.query)
    for key in ("jobId", "jobid", "requisitionId", "requisitionid", "id"):
        values = query.get(key)
        if values and values[0]:
            return normalize_whitespace(values[0])
    return hashlib.sha256(str(url or "").encode("utf-8")).hexdigest()[:24] if url else ""


def _job_url(target: OracleCloudHcmTarget, external_id: Any) -> str:
    identifier = normalize_whitespace(external_id)
    if not identifier:
        return ""
    return canonicalize_url(f"{target.site_base_url}/job/{identifier}")


def _detail_title(parser: _OracleHtmlParser) -> str:
    for key in ("og:title", "twitter:title", "title"):
        value = normalize_whitespace(parser.meta.get(key))
        if value:
            return value
    for value in parser.title_candidates:
        normalized = normalize_whitespace(value)
        if normalized and normalized.casefold() not in {
            "search jobs",
            "job search",
            "careers",
        }:
            return normalized
    return ""


def _parse_oracle_date(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    text = normalize_whitespace(value)
    for fmt in (
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%b %d, %Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)
