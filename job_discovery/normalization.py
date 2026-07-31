from __future__ import annotations

import html
import ipaddress
import posixpath
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from .models import WorkplaceType


_WHITESPACE = re.compile(r"\s+")
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"fbclid", "gclid", "gh_src", "lever-source"}


class _TextExtractor(HTMLParser):
    _BLOCKED_TAGS = {"script", "style", "noscript", "template", "iframe", "object", "svg"}
    _SEPARATOR_TAGS = {"br", "p", "div", "li", "tr", "td", "th", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.casefold()
        if name in self._BLOCKED_TAGS:
            self._blocked_depth += 1
        elif self._blocked_depth == 0 and name in self._SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self._blocked_depth == 0 and tag.casefold() in self._SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in self._BLOCKED_TAGS and self._blocked_depth:
            self._blocked_depth -= 1
        elif self._blocked_depth == 0 and name in self._SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._blocked_depth == 0:
            self.parts.append(data)


def normalize_whitespace(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return _WHITESPACE.sub(" ", text).strip()


def html_to_text(value: Any, *, max_chars: int = 500_000) -> str:
    """Convert untrusted HTML fragments to bounded plain text.

    Script/style/template-like content is discarded rather than rendered or
    persisted. The result contains no markup and is safe to place in normal
    escaped templates or send to the analysis pipeline as plain text.
    """
    parser = _TextExtractor()
    try:
        parser.feed(str(value or ""))
        parser.close()
        text = normalize_whitespace(" ".join(parser.parts))
    except Exception:
        text = normalize_whitespace(value)
    return text[: max(0, int(max_chars))]


def normalize_string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = re.split(r"[,;|\n]", value)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        parts = list(value)
    else:
        parts = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in parts:
        if isinstance(item, dict):
            item = item.get("name") or item.get("value") or item.get("location") or ""
        text = html_to_text(item)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:[,.]\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def normalize_employment_type(value: Any) -> str:
    text = html_to_text(value)
    key = re.sub(r"[^a-z]", "", text.casefold())
    mapping = {
        "fulltime": "Full-time",
        "parttime": "Part-time",
        "contract": "Contract",
        "contractor": "Contract",
        "temporary": "Temporary",
        "temp": "Temporary",
        "intern": "Internship",
        "internship": "Internship",
        "perdiem": "Per diem",
        "volunteer": "Volunteer",
    }
    return mapping.get(key, text)


def normalize_workplace_type(value: Any, *, location: str = "", is_remote: Any = None) -> WorkplaceType:
    text = html_to_text(value).casefold()
    if bool(is_remote) or "remote" in text or "telecommute" in text:
        return WorkplaceType.REMOTE
    if "hybrid" in text:
        return WorkplaceType.HYBRID
    if text in {"onsite", "on-site", "on site"}:
        return WorkplaceType.ONSITE
    location_text = location.casefold()
    if "remote" in location_text:
        return WorkplaceType.REMOTE
    if "hybrid" in location_text:
        return WorkplaceType.HYBRID
    return WorkplaceType.UNSPECIFIED


def canonicalize_url(value: str) -> str:
    """Return a stable public http(s) URL without tracking parameters/fragments."""
    parsed = urlsplit(str(value or "").strip())
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    try:
        host = parsed.hostname.rstrip(".").casefold().encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            return ""
        if address.version == 6:
            host = f"[{host}]"
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    decoded_path = unquote(parsed.path or "/")
    normalized_path = posixpath.normpath(decoded_path)
    if decoded_path.startswith("/") and not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if normalized_path in {"", "."}:
        normalized_path = "/"
    path = quote(normalized_path, safe="/%:@!$&'()*+,;=-._~")
    if path != "/":
        path = path.rstrip("/")
    query: list[tuple[str, str]] = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        low = key.casefold()
        if low in _TRACKING_KEYS or any(low.startswith(prefix) for prefix in _TRACKING_PREFIXES):
            continue
        query.append((key, item))
    query.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def stable_text_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_whitespace(value).casefold()).strip()


def format_salary_text(
    minimum: float | None,
    maximum: float | None,
    currency: str = "",
    interval: str = "",
    summary: str = "",
) -> str:
    explicit = html_to_text(summary)
    if explicit:
        return explicit
    if minimum is None and maximum is None:
        return ""
    prefix = f"{normalize_whitespace(currency)} " if currency else ""
    if minimum is not None and maximum is not None:
        amount = f"{_format_amount(minimum)}–{_format_amount(maximum)}"
    else:
        amount = _format_amount(minimum if minimum is not None else maximum)
    suffix = f" / {normalize_whitespace(interval)}" if interval else ""
    return f"{prefix}{amount}{suffix}".strip()


def _format_amount(value: float | None) -> str:
    if value is None:
        return ""
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")
