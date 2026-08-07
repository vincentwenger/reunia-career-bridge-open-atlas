#!/usr/bin/env python3
"""Verify that every user-facing template resolves shared CSS and JavaScript.

The audit follows Jinja ``extends`` and ``include`` references so page templates
can inherit the common asset fragments through a base template. Partials that
are only included by another page are not treated as independent pages.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOTS = (
    ROOT / "products" / "reunia" / "templates",
    ROOT / "products" / "resume_taylor" / "templates",
)

REFERENCE_RE = re.compile(
    r"{%\s*(?:extends|include)\s+['\"]([^'\"]+)['\"](?:\s+[^%]*)?%}"
)
COMMON_CSS_MARKERS = (
    "css/design-tokens.css",
    "css/base.css",
)
COMMON_JS_MARKERS = ("js/common.js",)
SKIPPED_DIRECTORIES = {"components", "macros", "meeting"}


def template_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root in TEMPLATE_ROOTS:
        for path in root.rglob("*.html"):
            index[path.relative_to(root).as_posix()] = path
    return index


def is_page_template(name: str, source: str) -> bool:
    path = Path(name)
    if path.name.startswith("_") or any(part in SKIPPED_DIRECTORIES for part in path.parts):
        return False
    return "<html" in source.lower() or bool(re.search(r"{%\s*extends\s+", source))


def resolved_source(name: str, index: dict[str, Path], seen: set[str] | None = None) -> str:
    seen = set() if seen is None else seen
    if name in seen:
        return ""
    seen.add(name)
    path = index.get(name)
    if path is None:
        return ""
    source = path.read_text(encoding="utf-8")
    related = [resolved_source(reference, index, seen) for reference in REFERENCE_RE.findall(source)]
    return source + "\n" + "\n".join(related)


def audit() -> list[str]:
    index = template_index()
    failures: list[str] = []
    for name, path in sorted(index.items()):
        source = path.read_text(encoding="utf-8")
        if not is_page_template(name, source):
            continue
        combined = resolved_source(name, index)
        has_css = any(marker in combined for marker in COMMON_CSS_MARKERS)
        has_js = any(marker in combined for marker in COMMON_JS_MARKERS)
        if not has_css or not has_js:
            missing = []
            if not has_css:
                missing.append("shared CSS")
            if not has_js:
                missing.append("shared JavaScript")
            failures.append(f"{name}: missing {' and '.join(missing)}")
    return failures


def main() -> int:
    failures = audit()
    if failures:
        print("Common page asset audit failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("All user-facing pages resolve shared CSS and JavaScript assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
