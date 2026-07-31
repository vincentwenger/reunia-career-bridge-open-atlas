from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..models import CompanySource, DiscoveredJob, JobSourceType


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; ReuniaJobBot/1.0; "
    "+https://reunia.app/job-discovery)"
)
ROBOTS_PRODUCT_TOKEN = "ReuniaJobBot"
DEFAULT_JSON_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_HTML_MAX_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
DEFAULT_REDIRECT_LIMIT = 3
MAX_REDIRECT_LIMIT = 5
DEFAULT_COMPANY_REQUEST_INTERVAL_SECONDS = 0.5


@runtime_checkable
class JobSource(Protocol):
    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        ...


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str

    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.casefold() == "charset" and value:
                charset = value.strip("\"'")
        return self.body.decode(charset, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        max_bytes: int | None = None,
        max_redirects: int = DEFAULT_REDIRECT_LIMIT,
        allowed_domains: Sequence[str] = (),
    ) -> HttpResponse:
        ...

    def post(
        self,
        url: str,
        *,
        body: bytes,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        max_bytes: int | None = None,
        max_redirects: int = DEFAULT_REDIRECT_LIMIT,
        allowed_domains: Sequence[str] = (),
    ) -> HttpResponse:
        ...


class SourceFetchError(RuntimeError):
    pass


class SourcePolicyError(SourceFetchError):
    pass


class UnsafeUrlError(SourceFetchError):
    pass


class RobotsDeniedError(SourceFetchError):
    pass


class RedirectLimitError(SourceFetchError):
    pass


class CompanyRateLimiter:
    """Process-local request spacing keyed by owner and configured company source."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.clock = clock
        self.sleeper = sleeper
        self._last_request: dict[str, float] = {}
        self._lock = threading.RLock()

    def wait(self, company_key: str, minimum_interval_seconds: float) -> None:
        interval = max(0.0, float(minimum_interval_seconds))
        with self._lock:
            now = self.clock()
            last = self._last_request.get(company_key)
            if last is not None:
                delay = interval - (now - last)
                if delay > 0:
                    self.sleeper(delay)
                    now = self.clock()
            self._last_request[company_key] = now


class _LimitedRedirectHandler(HTTPRedirectHandler):
    def __init__(
        self,
        *,
        max_redirects: int,
        allowed_domains: Sequence[str],
    ) -> None:
        super().__init__()
        self.max_redirects = max(0, int(max_redirects))
        self.allowed_domains = tuple(allowed_domains)
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        self.redirect_count += 1
        if self.redirect_count > self.max_redirects:
            raise RedirectLimitError(
                f"GET {req.full_url} exceeded {self.max_redirects} redirects"
            )
        validate_fetch_url(
            newurl,
            allowed_domains=self.allowed_domains,
            resolve_dns=True,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibHttpClient:
    def __init__(self, *, user_agent: str = DEFAULT_USER_AGENT) -> None:
        self.user_agent = user_agent

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        max_bytes: int | None = None,
        max_redirects: int = DEFAULT_REDIRECT_LIMIT,
        allowed_domains: Sequence[str] = (),
    ) -> HttpResponse:
        return self._request(
            "GET",
            url,
            body=None,
            headers=headers,
            timeout=timeout,
            max_bytes=max_bytes,
            max_redirects=max_redirects,
            allowed_domains=allowed_domains,
        )

    def post(
        self,
        url: str,
        *,
        body: bytes,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        max_bytes: int | None = None,
        max_redirects: int = DEFAULT_REDIRECT_LIMIT,
        allowed_domains: Sequence[str] = (),
    ) -> HttpResponse:
        return self._request(
            "POST",
            url,
            body=body,
            headers=headers,
            timeout=timeout,
            max_bytes=max_bytes,
            max_redirects=max_redirects,
            allowed_domains=allowed_domains,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None,
        headers: Mapping[str, str] | None,
        timeout: float,
        max_bytes: int | None,
        max_redirects: int,
        allowed_domains: Sequence[str],
    ) -> HttpResponse:
        validate_fetch_url(url, allowed_domains=allowed_domains, resolve_dns=True)
        request_headers = {"Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
        request_headers.update(headers or {})
        request_headers.setdefault("User-Agent", self.user_agent)
        request = Request(
            url,
            data=body,
            headers=request_headers,
            method=method,
        )
        redirect_handler = _LimitedRedirectHandler(
            max_redirects=max_redirects,
            allowed_domains=allowed_domains,
        )
        opener = build_opener(redirect_handler)
        try:
            with opener.open(request, timeout=timeout) as response:
                _validate_content_length(response.headers, max_bytes)
                response_body = _read_limited(response, max_bytes)
                final_url = response.geturl()
                validate_fetch_url(
                    final_url,
                    allowed_domains=allowed_domains,
                    resolve_dns=True,
                )
                return HttpResponse(
                    status=int(response.status),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=response_body,
                    url=final_url,
                )
        except HTTPError as exc:
            _validate_content_length(exc.headers, max_bytes)
            response_body = _read_limited(exc, max_bytes)
            final_url = exc.geturl()
            validate_fetch_url(
                final_url,
                allowed_domains=allowed_domains,
                resolve_dns=True,
            )
            return HttpResponse(
                status=int(exc.code),
                headers={key.casefold(): value for key, value in exc.headers.items()},
                body=response_body,
                url=final_url,
            )
        except (RedirectLimitError, SourceFetchError):
            raise
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise SourceFetchError(f"Unable to fetch {url}: {reason}") from exc

def fetch_json(
    client: HttpClient,
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    max_bytes: int = DEFAULT_JSON_MAX_BYTES,
    max_redirects: int = DEFAULT_REDIRECT_LIMIT,
    allowed_domains: Sequence[str] = (),
) -> Any:
    validate_fetch_url(url, allowed_domains=allowed_domains)
    response = client.get(
        url,
        headers=headers,
        timeout=timeout,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        allowed_domains=allowed_domains,
    )
    validate_fetch_url(response.url, allowed_domains=allowed_domains)
    if response.status < 200 or response.status >= 300:
        raise SourceFetchError(f"GET {url} returned HTTP {response.status}")
    try:
        return response.json()
    except (ValueError, TypeError) as exc:
        raise SourceFetchError(f"GET {url} did not return valid JSON") from exc



def fetch_json_post(
    client: HttpClient,
    url: str,
    payload: Any,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    max_bytes: int = DEFAULT_JSON_MAX_BYTES,
    max_redirects: int = DEFAULT_REDIRECT_LIMIT,
    allowed_domains: Sequence[str] = (),
) -> Any:
    validate_fetch_url(url, allowed_domains=allowed_domains)
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    request_headers.update(headers or {})
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    response = client.post(
        url,
        body=body,
        headers=request_headers,
        timeout=timeout,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        allowed_domains=allowed_domains,
    )
    validate_fetch_url(response.url, allowed_domains=allowed_domains)
    if response.status < 200 or response.status >= 300:
        raise SourceFetchError(f"POST {url} returned HTTP {response.status}")
    try:
        return response.json()
    except (ValueError, TypeError) as exc:
        raise SourceFetchError(f"POST {url} did not return valid JSON") from exc

def validate_source_policy(
    source: CompanySource,
    *,
    expected_type: JobSourceType,
) -> None:
    if source.source_type is not expected_type:
        raise ValueError(
            f"{expected_type.value} adapter requires a {expected_type.value} CompanySource"
        )
    if not source.enabled:
        raise SourcePolicyError("Disabled company sources cannot be fetched")
    if expected_type in {JobSourceType.GENERIC_JSONLD, JobSourceType.WORKDAY}:
        host = urlsplit(source.careers_url).hostname or ""
        validate_fetch_url(source.careers_url, allowed_domains=(host,))
        if expected_type is JobSourceType.GENERIC_JSONLD:
            robots_setting = source.filters.get("respect_robots", True)
            if robots_setting is False or str(robots_setting).strip().casefold() in {"0", "false", "no", "off"}:
                raise SourcePolicyError("Generic crawling cannot disable robots.txt checks")
    else:
        identifier = source.source_identifier.strip()
        if not identifier or len(identifier) > 200:
            raise SourcePolicyError("The public source identifier is invalid")
    include_unlisted = source.filters.get("include_unlisted", False)
    if expected_type is JobSourceType.ASHBY and (
        include_unlisted is True
        or str(include_unlisted).strip().casefold() in {"1", "true", "yes", "on"}
    ):
        raise SourcePolicyError("Ashby discovery is limited to publicly listed postings")


def validate_fetch_url(
    url: str,
    *,
    allowed_domains: Sequence[str] = (),
    resolve_dns: bool = False,
) -> str:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise UnsafeUrlError("Only http(s) URLs may be fetched")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URLs containing credentials are not allowed")
    host = _normalize_hostname(parsed.hostname or "")
    if not host:
        raise UnsafeUrlError("A fetch URL must contain a hostname")
    normalized_allowed = tuple(
        _normalize_hostname(domain) for domain in allowed_domains if str(domain or "").strip()
    )
    if normalized_allowed and host not in normalized_allowed:
        raise UnsafeUrlError(f"Host {host} is outside the configured allowed domains")
    _reject_non_public_host(host)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("The fetch URL contains an invalid port") from exc
    if resolve_dns:
        _reject_non_public_dns_results(host, port)
    return host


def source_timeout(source: CompanySource, default: float = 10.0) -> float:
    try:
        timeout = float(source.options.get("timeout_seconds", default))
    except (TypeError, ValueError):
        timeout = default
    return min(max(timeout, 1.0), 30.0)


def source_response_limit(source: CompanySource, *, default: int) -> int:
    try:
        value = int(source.options.get("max_response_bytes", default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, 1024), MAX_RESPONSE_BYTES)


def source_redirect_limit(source: CompanySource) -> int:
    try:
        value = int(source.options.get("max_redirects", DEFAULT_REDIRECT_LIMIT))
    except (TypeError, ValueError):
        value = DEFAULT_REDIRECT_LIMIT
    return min(max(value, 0), MAX_REDIRECT_LIMIT)


def source_min_request_interval(
    source: CompanySource,
    default: float = DEFAULT_COMPANY_REQUEST_INTERVAL_SECONDS,
) -> float:
    try:
        interval = float(source.options.get("min_request_interval_seconds", default))
    except (TypeError, ValueError):
        interval = default
    return min(max(interval, 0.0), 60.0)


def company_rate_limit_key(source: CompanySource) -> str:
    return f"{source.owner_id}:{' '.join(source.company_name.casefold().split())}"


def source_deactivation_threshold(source: CompanySource, default: int = 3) -> int:
    try:
        threshold = int(source.options.get("deactivate_after_missed_scans", default))
    except (TypeError, ValueError):
        threshold = default
    return min(max(threshold, 2), 10)


def _read_limited(stream: Any, max_bytes: int | None) -> bytes:
    if max_bytes is None:
        return stream.read()
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SourceFetchError(f"Response exceeded {max_bytes} bytes")
    return data


def _validate_content_length(headers: Mapping[str, Any] | None, max_bytes: int | None) -> None:
    if max_bytes is None or headers is None:
        return
    raw = headers.get("Content-Length") or headers.get("content-length")
    if raw in (None, ""):
        return
    try:
        length = int(raw)
    except (TypeError, ValueError):
        return
    if length > max_bytes:
        raise SourceFetchError(f"Response declared {length} bytes; limit is {max_bytes}")


def _normalize_hostname(value: str) -> str:
    host = str(value or "").strip().rstrip(".").casefold()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeUrlError("The hostname is invalid") from exc


def _reject_non_public_host(host: str) -> None:
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise UnsafeUrlError("Localhost destinations are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise UnsafeUrlError("Private, loopback, link-local, and reserved IPs are not allowed")


def _reject_non_public_dns_results(host: str, port: int) -> None:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SourceFetchError(f"Unable to resolve {host}") from exc
    if not results:
        raise SourceFetchError(f"Unable to resolve {host}")
    for result in results:
        address_text = result[4][0]
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise UnsafeUrlError(f"Invalid resolved address for {host}") from exc
        if not address.is_global:
            raise UnsafeUrlError(
                f"Host {host} resolved to a non-public address and was blocked"
            )
