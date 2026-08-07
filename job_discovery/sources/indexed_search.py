from __future__ import annotations

from .common import (
    bounded_int as _bounded_int,
    bounded_float as _bounded_float,
)

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol
from urllib.parse import unquote, urlsplit

from ..normalization import canonicalize_url, normalize_whitespace
from .base import SourceFetchError


_URL_RE = re.compile(r"https://[^\s<>\"'\\)\]]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class IndexedPostingHit:
    """One official posting reconstructed from a hosted search index.

    Search-index metadata is deliberately compact. It is sufficient to create a
    usable partial job record when an ATS blocks both its listing and detail
    pages from the Career Bridge server. Direct ATS content remains preferred.
    """

    url: str
    title: str = ""
    location: str = ""
    posted_at: str = ""
    description: str = ""
    is_active: bool | None = None


class IndexedPostingSearch(Protocol):
    def find_postings(
        self,
        *,
        company_name: str,
        host: str,
        path_pattern: re.Pattern[str],
        max_results: int,
        index_page_url: str = "",
    ) -> list[IndexedPostingHit]:
        ...


class OpenAIIndexedPostingSearch:
    """Discover official postings without crawling a blocked ATS route.

    OpenAI's hosted web-search tool queries its search index. Career Bridge then
    accepts only HTTPS URLs on the exact configured host whose path matches the
    connector's job-detail pattern. The model is asked to return compact indexed
    metadata so a blocked detail page is no longer a hard dependency.
    """

    def __init__(self, *, client: Any | None = None) -> None:
        self._client = client

    def find_postings(
        self,
        *,
        company_name: str,
        host: str,
        path_pattern: re.Pattern[str],
        max_results: int,
        index_page_url: str = "",
    ) -> list[IndexedPostingHit]:
        normalized_host = str(host or "").strip().casefold()
        limit = max(1, min(int(max_results or 1), 100))
        if not normalized_host:
            raise SourceFetchError("Indexed discovery requires an exact career-site host")

        normalized_index_page = _validated_index_page_url(
            index_page_url, host=normalized_host
        )
        client = self._client or self._build_client()
        model = (
            os.getenv("JOB_DISCOVERY_WEB_SEARCH_MODEL") or "gpt-5-mini"
        ).strip()
        total_timeout = _bounded_float(
            os.getenv("JOB_DISCOVERY_WEB_SEARCH_TIMEOUT_SECONDS"),
            default=50.0,
            minimum=45.0,
            maximum=90.0,
        )
        attempts = _bounded_int(
            os.getenv("JOB_DISCOVERY_WEB_SEARCH_ATTEMPTS"),
            default=2,
            minimum=1,
            maximum=2,
        )
        path_rule = path_pattern.pattern
        prompts = _search_prompts(
            company_name=company_name,
            host=normalized_host,
            path_rule=path_rule,
            limit=limit,
            index_page_url=normalized_index_page,
        )[:attempts]
        attempt_timeouts = _attempt_timeouts(total_timeout, len(prompts))

        errors: list[str] = []
        received_response = False
        for attempt, (prompt, attempt_timeout) in enumerate(
            zip(prompts, attempt_timeouts, strict=True), start=1
        ):
            try:
                response = client.responses.create(
                    model=model,
                    tools=[
                        {
                            "type": "web_search",
                            "filters": {"allowed_domains": [normalized_host]},
                            # The listing page is already indexed. Low context plus a
                            # direct URL prompt is faster and more reliable than asking
                            # the model to perform a broad multi-page research session.
                            "search_context_size": "low",
                        }
                    ],
                    tool_choice="required",
                    parallel_tool_calls=False,
                    reasoning={"effort": "minimal"},
                    input=prompt,
                    max_output_tokens=3000,
                    max_tool_calls=2 if attempt == 1 else 1,
                    timeout=attempt_timeout,
                    store=False,
                )
            except Exception as exc:  # provider errors are isolated to this source
                message = normalize_whitespace(exc) or exc.__class__.__name__
                errors.append(message)
                if attempt >= len(prompts) or not _retryable_search_error(
                    exc, message
                ):
                    detail = errors[-1] if len(errors) == 1 else "; ".join(errors)
                    raise SourceFetchError(
                        "Indexed posting discovery failed"
                        f" after {attempt} strategy attempt(s): {detail}"
                    ) from exc
                continue

            received_response = True
            hits = _hits_from_response(
                response,
                host=normalized_host,
                path_pattern=path_pattern,
                limit=limit,
            )
            if hits:
                return hits

            # A successful provider response with no usable official URLs is not a
            # transient exception, but the second compact site-query strategy can
            # still recover from a stale or thin listing-page index entry.
            errors.append("no matching official posting URLs were returned")

        if received_response:
            return []
        detail = "; ".join(errors) or "no response"
        raise SourceFetchError(f"Indexed posting discovery failed: {detail}")

    @staticmethod
    def _build_client() -> Any:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise SourceFetchError(
                "Indexed posting discovery requires OPENAI_API_KEY"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - production dependency
            raise SourceFetchError("The OpenAI SDK is not installed") from exc
        return OpenAI(api_key=api_key, timeout=30.0, max_retries=0)



def _validated_index_page_url(value: object, *, host: str) -> str:
    url = canonicalize_url(value)
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https":
        return ""
    if (parsed.hostname or "").casefold() != host:
        return ""
    return url


def _search_prompts(
    *,
    company_name: str,
    host: str,
    path_rule: str,
    limit: int,
    index_page_url: str,
) -> list[str]:
    fields = (
        "Return JSON only: an array of objects with url, title, location, "
        "posted_at, description, and is_active. Keep description under 220 "
        "characters and use an empty string when metadata is unavailable. "
        "Set is_active to false only when the indexed result explicitly says the "
        "posting is closed, expired, filled, unavailable, or no longer active."
    )
    prompts: list[str] = []
    if index_page_url:
        prompts.append(
            f"Use the hosted web-search index to open the exact official "
            f"{company_name} job listing page {index_page_url}. Extract up to "
            f"{limit} currently open job-detail links shown by that indexed page. "
            f"Accept only HTTPS URLs on the exact host {host} whose URL path "
            f"matches this regular expression: {path_rule!r}. Exclude the listing "
            "page itself, login pages, application-only pages, and third-party "
            f"boards. {fields}"
        )
    prompts.append(
        f"Run one compact web search for current {company_name} job-detail pages "
        f"using the query site:{host}. Return at most {limit} results. Accept only "
        f"HTTPS URLs on the exact host {host} whose URL path matches this regular "
        f"expression: {path_rule!r}. Exclude search/listing pages, login pages, "
        f"application-only pages, and third-party boards. {fields}"
    )
    # Keep two genuinely different strategies even when no explicit listing page
    # exists, so a transient or thin first search can still be retried compactly.
    if len(prompts) == 1:
        prompts.append(prompts[0] + " Prefer the newest indexed results first.")
    return prompts


def _attempt_timeouts(total_timeout: float, count: int) -> list[float]:
    if count <= 1:
        return [total_timeout]
    primary = min(35.0, max(30.0, total_timeout * 0.7))
    secondary = max(10.0, total_timeout - primary)
    return [primary, secondary]


def _hits_from_response(
    response: Any,
    *,
    host: str,
    path_pattern: re.Pattern[str],
    limit: int,
) -> list[IndexedPostingHit]:
    output_text = str(getattr(response, "output_text", "") or "")
    structured = _postings_from_output(output_text)
    hits = _validated_hits(
        structured, host=host, path_pattern=path_pattern, limit=limit
    )
    if hits:
        return hits

    # Compatibility fallback for a provider response that contains URL source
    # annotations but no parseable JSON. These records are intentionally sparse,
    # but still receive a deterministic title from the official URL slug.
    payload = _model_payload(response)
    candidate_strings = list(_walk_strings(payload))
    candidate_strings.append(output_text)
    fallback: list[IndexedPostingHit] = []
    seen: set[tuple[str, str]] = set()
    for value in candidate_strings:
        for raw_url in _URL_RE.findall(value):
            url = canonicalize_url(raw_url.rstrip(".,;:"))
            parsed = urlsplit(url)
            if parsed.scheme.casefold() != "https":
                continue
            if (parsed.hostname or "").casefold() != host:
                continue
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            if not path_pattern.search(target):
                continue
            identity = _url_identity(url)
            if identity in seen:
                continue
            seen.add(identity)
            fallback.append(
                IndexedPostingHit(
                    url=url,
                    title=_title_from_job_url(url),
                    is_active=None,
                )
            )
            if len(fallback) >= limit:
                return fallback
    return fallback

def _validated_hits(
    candidates: Iterable[IndexedPostingHit],
    *,
    host: str,
    path_pattern: re.Pattern[str],
    limit: int,
) -> list[IndexedPostingHit]:
    result: list[IndexedPostingHit] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate.is_active is False:
            continue
        url = canonicalize_url(candidate.url)
        parsed = urlsplit(url)
        if parsed.scheme.casefold() != "https":
            continue
        if (parsed.hostname or "").casefold() != host:
            continue
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        if not path_pattern.search(target):
            continue
        identity = _url_identity(url)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
            IndexedPostingHit(
                url=url,
                title=normalize_whitespace(candidate.title) or _title_from_job_url(url),
                location=normalize_whitespace(candidate.location),
                posted_at=normalize_whitespace(candidate.posted_at),
                description=normalize_whitespace(candidate.description)[:4000],
                is_active=candidate.is_active,
            )
        )
        if len(result) >= limit:
            break
    return result


def _postings_from_output(text: str) -> list[IndexedPostingHit]:
    raw = str(text or "").strip()
    if not raw:
        return []
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())
    array = re.search(r"\[.*\]", raw, re.DOTALL)
    if array and array.group(0) not in candidates:
        candidates.append(array.group(0))

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(value, list):
            continue
        result: list[IndexedPostingHit] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            url = canonicalize_url(item.get("url"))
            if not url:
                continue
            active = _optional_bool(
                item.get("is_active", item.get("active", item.get("is_open")))
            )
            result.append(
                IndexedPostingHit(
                    url=url,
                    title=normalize_whitespace(item.get("title")),
                    location=normalize_whitespace(item.get("location")),
                    posted_at=normalize_whitespace(
                        item.get("posted_at")
                        or item.get("date_posted")
                        or item.get("datePosted")
                    ),
                    description=normalize_whitespace(
                        item.get("description")
                        or item.get("summary")
                        or item.get("snippet")
                    ),
                    is_active=active,
                )
            )
        return result
    return []


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1", "yes", "open", "active"}:
        return True
    if normalized in {"false", "0", "no", "closed", "inactive", "expired"}:
        return False
    return None


def _url_identity(url: str) -> tuple[str, str]:
    parsed = urlsplit(canonicalize_url(url))
    return ((parsed.hostname or "").casefold(), (parsed.path or "/").rstrip("/"))


def _title_from_job_url(url: str) -> str:
    path = unquote(urlsplit(url).path or "").rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    slug = re.sub(r"^\d{3,}[-_]", "", slug)
    slug = re.sub(r"[-_]+", " ", slug)
    slug = normalize_whitespace(slug)
    if not slug:
        return "Indexed job posting"
    words: list[str] = []
    acronyms = {
        "ai",
        "bi",
        "crm",
        "dba",
        "erm",
        "hr",
        "it",
        "qa",
        "sql",
        "vp",
    }
    for word in slug.split():
        words.append(word.upper() if word.casefold() in acronyms else word.capitalize())
    return " ".join(words)


def _model_payload(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    if isinstance(value, (dict, list, tuple, str)):
        return value
    return vars(value) if hasattr(value, "__dict__") else str(value)


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _walk_strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_strings(nested)


def _retryable_search_error(exc: Exception, message: str) -> bool:
    normalized = f"{exc.__class__.__name__} {message}".casefold()
    return any(
        token in normalized
        for token in (
            "timeout",
            "timed out",
            "rate limit",
            "429",
            "connection",
            "temporarily unavailable",
            "service unavailable",
            "502",
            "503",
            "504",
        )
    )
