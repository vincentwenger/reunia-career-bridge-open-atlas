from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

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
from ..storage import CacheStore, InMemoryTTLCache
from .base import (
    DEFAULT_HTML_MAX_BYTES,
    CompanyRateLimiter,
    HttpClient,
    ROBOTS_PRODUCT_TOKEN,
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


ROBOTS_MAX_BYTES = 500 * 1024
DEFAULT_CACHE_SECONDS = 15 * 60
ROBOTS_CACHE_SECONDS = 24 * 60 * 60
_JOB_LINK_HINT = re.compile(r"(?:career|job|jobs|opening|position|vacanc)", re.IGNORECASE)


class GenericJsonLdJobSource:
    """Bounded JSON-LD crawler for configured public career pages.

    It honors robots.txt before each page request, identifies itself with a
    descriptive user agent, applies per-company rate limiting and timeouts, and
    caches both robots policies and fetched page bodies.
    """

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        cache: CacheStore | None = None,
        rate_limiter: CompanyRateLimiter | None = None,
    ) -> None:
        self.http = http_client or UrllibHttpClient()
        self.cache = cache or InMemoryTTLCache()
        self.rate_limiter = rate_limiter or CompanyRateLimiter()

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        validate_source_policy(source, expected_type=JobSourceType.GENERIC_JSONLD)
        max_pages = _bounded_int(source.options.get("max_pages"), default=10, minimum=1, maximum=25)
        cache_seconds = _bounded_int(
            source.options.get("cache_seconds"),
            default=DEFAULT_CACHE_SECONDS,
            minimum=0,
            maximum=24 * 60 * 60,
        )
        min_interval = source_min_request_interval(source, default=1.0)
        follow_links = bool(source.options.get("follow_job_links", True))
        timeout = source_timeout(source)

        start = canonicalize_url(source.careers_url)
        start_host = urlsplit(start).hostname.casefold() if urlsplit(start).hostname else ""
        allowed_domains = (start_host,)
        validate_fetch_url(start, allowed_domains=allowed_domains)
        company_key = company_rate_limit_key(source)
        max_bytes = source_response_limit(source, default=DEFAULT_HTML_MAX_BYTES)
        max_redirects = source_redirect_limit(source)
        queue = [start]
        visited: set[str] = set()
        jobs: list[DiscoveredJob] = []

        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            if url in visited or (urlsplit(url).hostname or "").casefold() != start_host:
                continue
            visited.add(url)
            if not self._allowed(
                url,
                timeout=timeout,
                min_interval=min_interval,
                company_key=company_key,
                allowed_domains=allowed_domains,
                max_redirects=max_redirects,
            ):
                if url == start:
                    raise RobotsDeniedError(f"robots.txt disallows crawling {url}")
                continue
            page = self._fetch_page(
                url,
                timeout=timeout,
                cache_seconds=cache_seconds,
                min_interval=min_interval,
                company_key=company_key,
                allowed_domains=allowed_domains,
                max_bytes=max_bytes,
                max_redirects=max_redirects,
            )
            parser = _JsonLdHtmlParser()
            parser.feed(page)
            parser.close()
            jobs.extend(_jobs_from_documents(parser.documents, source=source, page_url=url))
            if follow_links and len(visited) + len(queue) < max_pages:
                for href in parser.links:
                    candidate = _same_host_job_link(url, href, start_host)
                    if candidate and candidate not in visited and candidate not in queue:
                        queue.append(candidate)
                        if len(visited) + len(queue) >= max_pages:
                            break
        return jobs

    def _fetch_page(
        self,
        url: str,
        *,
        timeout: float,
        cache_seconds: int,
        min_interval: float,
        company_key: str,
        allowed_domains: tuple[str, ...],
        max_bytes: int,
        max_redirects: int,
    ) -> str:
        key = f"page:{url}"
        cached = self.cache.get(key)
        if isinstance(cached, str):
            return cached
        validate_fetch_url(url, allowed_domains=allowed_domains)
        self.rate_limiter.wait(company_key, min_interval)
        response = self.http.get(
            url,
            headers={"Accept": "text/html, application/xhtml+xml"},
            timeout=timeout,
            max_bytes=max_bytes,
            max_redirects=max_redirects,
            allowed_domains=allowed_domains,
        )
        validate_fetch_url(response.url, allowed_domains=allowed_domains)
        if response.status < 200 or response.status >= 300:
            raise SourceFetchError(f"GET {url} returned HTTP {response.status}")
        content_type = response.headers.get("content-type", "").casefold()
        if content_type and not any(value in content_type for value in ("text/html", "application/xhtml+xml")):
            raise SourceFetchError(f"GET {url} did not return HTML")
        page = response.text()
        self.cache.set(key, page, cache_seconds)
        return page

    def _allowed(
        self,
        url: str,
        *,
        timeout: float,
        min_interval: float,
        company_key: str,
        allowed_domains: tuple[str, ...],
        max_redirects: int,
    ) -> bool:
        parsed = urlsplit(url)
        robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        cache_key = f"robots:{robots_url}"
        policy = self.cache.get(cache_key)
        if isinstance(policy, _RobotsPolicy):
            return policy.allowed(url)
        try:
            validate_fetch_url(robots_url, allowed_domains=allowed_domains)
            self.rate_limiter.wait(company_key, min_interval)
            response = self.http.get(
                robots_url,
                headers={"Accept": "text/plain"},
                timeout=timeout,
                max_bytes=ROBOTS_MAX_BYTES,
                max_redirects=max_redirects,
                allowed_domains=allowed_domains,
            )
            validate_fetch_url(response.url, allowed_domains=allowed_domains)
        except SourceFetchError:
            policy = _RobotsPolicy.disallow_all()
        else:
            if 200 <= response.status < 300:
                policy = _RobotsPolicy.parse(response.text(), ROBOTS_PRODUCT_TOKEN)
            elif 400 <= response.status < 500:
                policy = _RobotsPolicy.allow_all()
            else:
                policy = _RobotsPolicy.disallow_all()
        self.cache.set(cache_key, policy, ROBOTS_CACHE_SECONDS)
        return policy.allowed(url)


# Backward-compatible name retained for existing callers/tests.
HostRateLimiter = CompanyRateLimiter


@dataclass(frozen=True, slots=True)
class _Rule:
    allow: bool
    pattern: str

    @property
    def specificity(self) -> int:
        return len(self.pattern.rstrip("$").replace("*", "").encode("utf-8"))

    def matches(self, target: str) -> bool:
        expression = re.escape(self.pattern)
        expression = expression.replace(r"\*", ".*")
        if expression.endswith(r"\$"):
            expression = expression[:-2] + "$"
        else:
            expression += ".*"
        return re.match(expression, target) is not None


@dataclass(frozen=True, slots=True)
class _RobotsPolicy:
    rules: tuple[_Rule, ...] = ()
    default_allowed: bool = True

    @classmethod
    def allow_all(cls) -> "_RobotsPolicy":
        return cls(default_allowed=True)

    @classmethod
    def disallow_all(cls) -> "_RobotsPolicy":
        return cls(default_allowed=False)

    @classmethod
    def parse(cls, text: str, product_token: str) -> "_RobotsPolicy":
        groups: list[tuple[list[str], list[_Rule]]] = []
        agents: list[str] = []
        rules: list[_Rule] = []
        saw_rules = False

        def flush() -> None:
            nonlocal agents, rules, saw_rules
            if agents:
                groups.append((agents, rules))
            agents, rules, saw_rules = [], [], False

        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.casefold()
            if key == "user-agent":
                if saw_rules:
                    flush()
                agents.append(value.casefold())
            elif key in {"allow", "disallow"} and agents:
                saw_rules = True
                if value:
                    rules.append(_Rule(allow=key == "allow", pattern=value))
        flush()

        token = product_token.casefold()
        exact = [group_rules for group_agents, group_rules in groups if token in group_agents]
        selected = exact or [group_rules for group_agents, group_rules in groups if "*" in group_agents]
        if not selected:
            return cls.allow_all()
        return cls(rules=tuple(rule for group in selected for rule in group), default_allowed=True)

    def allowed(self, url: str) -> bool:
        if not self.rules:
            return self.default_allowed
        parsed = urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        matches = [rule for rule in self.rules if rule.matches(target)]
        if not matches:
            return self.default_allowed
        best_specificity = max(rule.specificity for rule in matches)
        best = [rule for rule in matches if rule.specificity == best_specificity]
        return any(rule.allow for rule in best)


class _JsonLdHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[Any] = []
        self.links: list[str] = []
        self._in_jsonld = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "script" and "ld+json" in attributes.get("type", "").casefold():
            self._in_jsonld = True
            self._script_parts = []
        elif tag.casefold() == "a" and attributes.get("href"):
            self.links.append(attributes["href"])

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or not self._in_jsonld:
            return
        raw = "".join(self._script_parts).strip()
        self._in_jsonld = False
        self._script_parts = []
        if not raw:
            return
        try:
            self.documents.append(json.loads(raw))
        except json.JSONDecodeError:
            return


def _jobs_from_documents(documents: list[Any], *, source: CompanySource, page_url: str) -> list[DiscoveredJob]:
    jobs: list[DiscoveredJob] = []
    for document in documents:
        for item in _walk_json(document):
            type_value = item.get("@type")
            types = {str(value).casefold() for value in type_value} if isinstance(type_value, list) else {str(type_value).casefold()}
            if "jobposting" not in types:
                continue
            job = _job_from_jsonld(item, source=source, page_url=page_url)
            if job is not None:
                jobs.append(job)
    return jobs


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _job_from_jsonld(item: dict[str, Any], *, source: CompanySource, page_url: str) -> DiscoveredJob | None:
    title = html_to_text(item.get("title"))
    if not title:
        return None
    organization = item.get("hiringOrganization") or {}
    company = html_to_text(organization.get("name") if isinstance(organization, dict) else organization)
    company = company or source.company_name
    job_url = canonicalize_url(item.get("url") or page_url)
    identifier = item.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value") or identifier.get("name")
    external_id = normalize_whitespace(identifier)
    if not external_id:
        external_id = hashlib.sha256(job_url.encode("utf-8")).hexdigest()[:24]

    locations = _jsonld_locations(item)
    location = ", ".join(locations)
    salary_min, salary_max, currency, interval, summary = _jsonld_salary(item.get("baseSalary"))
    skills = normalize_string_list(item.get("skills"))
    seen_at = utc_now_iso()
    return DiscoveredJob(
        id=discovered_job_id(source.owner_id, source.id, external_id),
        owner_id=source.owner_id,
        source_id=source.id,
        external_job_id=external_id,
        company=company,
        title=title,
        location=location,
        locations=locations,
        workplace_type=normalize_workplace_type(item.get("jobLocationType"), location=location),
        employment_type=normalize_employment_type(_first(item.get("employmentType"))),
        salary_text=format_salary_text(salary_min, salary_max, currency, interval, summary),
        description=html_to_text(item.get("description")),
        canonical_url=job_url,
        apply_url=canonicalize_url(item.get("applicationContact", {}).get("url", "") if isinstance(item.get("applicationContact"), dict) else ""),
        posted_at=normalize_iso_timestamp(parse_datetime(item.get("datePosted"))),
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        source_type=source.source_type,
        skills=skills,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=currency,
        salary_interval=interval,
        valid_through=normalize_iso_timestamp(parse_datetime(item.get("validThrough"))),
        metadata={
            "industry": html_to_text(item.get("industry")),
            "qualifications": html_to_text(item.get("qualifications")),
            "education_requirements": html_to_text(item.get("educationRequirements")),
            "experience_requirements": html_to_text(item.get("experienceRequirements")),
        },
    )


def _jsonld_locations(item: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    raw_locations = item.get("jobLocation") or []
    if not isinstance(raw_locations, list):
        raw_locations = [raw_locations]
    for location in raw_locations:
        if isinstance(location, str):
            values.append(location)
            continue
        if not isinstance(location, dict):
            continue
        address = location.get("address") or {}
        if isinstance(address, str):
            values.append(address)
            continue
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        text = ", ".join(normalize_whitespace(part) for part in parts if normalize_whitespace(part))
        if text:
            values.append(text)
        elif location.get("name"):
            values.append(str(location["name"]))
    if "telecommute" in normalize_whitespace(item.get("jobLocationType")).casefold():
        values.append("Remote")
    return normalize_string_list(values)


def _jsonld_salary(value: Any) -> tuple[float | None, float | None, str, str, str]:
    if not isinstance(value, dict):
        return None, None, "", "", normalize_whitespace(value)
    currency = normalize_whitespace(value.get("currency"))
    amount = value.get("value") or {}
    if not isinstance(amount, dict):
        number = parse_number(amount)
        return number, number, currency, "", normalize_whitespace(amount)
    minimum = parse_number(amount.get("minValue"))
    maximum = parse_number(amount.get("maxValue"))
    exact = parse_number(amount.get("value"))
    if minimum is None and maximum is None and exact is not None:
        minimum = maximum = exact
    interval = normalize_whitespace(amount.get("unitText"))
    summary = normalize_whitespace(
        " ".join(
            part
            for part in (
                currency,
                str(minimum or ""),
                "-" if minimum is not None and maximum is not None and minimum != maximum else "",
                str(maximum or "") if maximum != minimum else "",
                interval,
            )
            if part
        )
    )
    return minimum, maximum, currency, interval, summary


def _same_host_job_link(base_url: str, href: str, host: str) -> str | None:
    joined = canonicalize_url(urljoin(base_url, href))
    parsed = urlsplit(joined)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").casefold() != host:
        return None
    if not _JOB_LINK_HINT.search(parsed.path + "?" + parsed.query):
        return None
    return joined


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else ""
    return value


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)
