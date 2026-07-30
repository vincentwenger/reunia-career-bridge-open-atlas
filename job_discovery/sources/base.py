from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..models import CompanySource, DiscoveredJob


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; ReuniaJobBot/1.0; "
    "+https://reunia.app/job-discovery)"
)
ROBOTS_PRODUCT_TOKEN = "ReuniaJobBot"


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
                charset = value.strip('"\'')
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
    ) -> HttpResponse:
        ...


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
    ) -> HttpResponse:
        request_headers = {"Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
        request_headers.update(headers or {})
        request_headers.setdefault("User-Agent", self.user_agent)
        request = Request(url, headers=request_headers, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                body = _read_limited(response, max_bytes)
                return HttpResponse(
                    status=int(response.status),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=body,
                    url=response.geturl(),
                )
        except HTTPError as exc:
            body = _read_limited(exc, max_bytes)
            return HttpResponse(
                status=int(exc.code),
                headers={key.casefold(): value for key, value in exc.headers.items()},
                body=body,
                url=exc.geturl(),
            )
        except URLError as exc:
            raise SourceFetchError(f"Unable to fetch {url}: {exc.reason}") from exc


class SourceFetchError(RuntimeError):
    pass


class RobotsDeniedError(SourceFetchError):
    pass


def fetch_json(
    client: HttpClient,
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> Any:
    response = client.get(url, headers=headers, timeout=timeout)
    if response.status < 200 or response.status >= 300:
        raise SourceFetchError(f"GET {url} returned HTTP {response.status}")
    try:
        return response.json()
    except (ValueError, TypeError) as exc:
        raise SourceFetchError(f"GET {url} did not return valid JSON") from exc


def source_timeout(source: CompanySource, default: float = 10.0) -> float:
    try:
        timeout = float(source.options.get("timeout_seconds", default))
    except (TypeError, ValueError):
        timeout = default
    return min(max(timeout, 1.0), 60.0)


def _read_limited(stream: Any, max_bytes: int | None) -> bytes:
    if max_bytes is None:
        return stream.read()
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SourceFetchError(f"Response exceeded {max_bytes} bytes")
    return data
