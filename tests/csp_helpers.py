"""Shared helpers for CSP/template compatibility tests."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

EXECUTABLE_SCRIPT_TYPES = {
    "",
    "application/ecmascript",
    "application/javascript",
    "module",
    "text/ecmascript",
    "text/javascript",
}

INLINE_EVENT_HANDLER_RE = re.compile(r"\son[a-z][a-z0-9_:-]*\s*=", re.IGNORECASE)
SCRIPT_TAG_RE = re.compile(r"<script\b(?P<attributes>[^>]*)>", re.IGNORECASE | re.DOTALL)
ATTRIBUTE_RE_TEMPLATE = r"\b{name}\s*=\s*([\"'])(?P<value>.*?)\1"


def is_executable_script_type(value: str | None) -> bool:
    """Return whether a script type is executable under normal browser rules."""

    normalized = str(value or "").strip().lower().split(";", 1)[0]
    return normalized in EXECUTABLE_SCRIPT_TYPES


def _template_attribute(attributes: str, name: str) -> str | None:
    pattern = re.compile(
        ATTRIBUTE_RE_TEMPLATE.format(name=re.escape(name)),
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(attributes)
    return match.group("value") if match else None


def template_csp_violations(path: Path) -> list[str]:
    """Return inline-handler and nonce problems in a Jinja HTML template."""

    content = path.read_text(encoding="utf-8")
    violations: list[str] = []
    for match in INLINE_EVENT_HANDLER_RE.finditer(content):
        line = content.count("\n", 0, match.start()) + 1
        violations.append(f"{path}:{line}: inline event handler")

    for match in SCRIPT_TAG_RE.finditer(content):
        attributes = match.group("attributes")
        if _template_attribute(attributes, "src") is not None:
            continue
        if not is_executable_script_type(_template_attribute(attributes, "type")):
            continue
        nonce = _template_attribute(attributes, "nonce")
        if nonce is None or not re.fullmatch(r"\s*{{\s*csp_nonce\s*}}\s*", nonce):
            line = content.count("\n", 0, match.start()) + 1
            violations.append(
                f"{path}:{line}: inline executable script missing nonce=\"{{{{ csp_nonce }}}}\""
            )
    return violations


class RenderedHTMLCSPParser(HTMLParser):
    """Inspect rendered HTML for event handlers and inline script nonces."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inline_event_handlers: list[str] = []
        self.inline_executable_script_nonces: list[str | None] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized = {str(name).lower(): value for name, value in attrs}
        for name in normalized:
            if name.startswith("on"):
                self.inline_event_handlers.append(name)
        if tag.lower() != "script" or "src" in normalized:
            return
        if is_executable_script_type(normalized.get("type")):
            self.inline_executable_script_nonces.append(normalized.get("nonce"))


def inspect_rendered_html(content: str) -> RenderedHTMLCSPParser:
    parser = RenderedHTMLCSPParser()
    parser.feed(content)
    parser.close()
    return parser
