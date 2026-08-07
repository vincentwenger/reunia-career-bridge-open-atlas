#!/usr/bin/env python3
"""Reject silent browser persistence for durable Career Bridge records.

Browser storage is allowed only for non-authoritative preferences or transient
session identifiers. Career actions, application workspaces, materials, files,
and profile/context records must be confirmed by a server response before the
UI presents them as saved.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = ROOT / "products"

ALLOWED_LOCAL_STORAGE_WRITERS = {
    Path("products/reunia/static/js/i18n.js"),  # selected interface language
    Path("products/reunia/static/js/pages/settings.js"),  # selected interface language
    Path("products/reunia/static/js/pages/meeting-recorder.js"),  # resumable server session id
}

AUTHORITATIVE_DATA_SCRIPTS = {
    Path("products/reunia/static/js/pages/action-center.js"),
    Path("products/reunia/static/js/pages/knowledge.js"),
}

FORBIDDEN_PHRASES = (
    "saved in this browser",
    "using browser storage",
    "saved in browser only",
    "browser storage fallback",
    "temporarily saved in this browser",
    "stored as browser metadata",
    "cleared locally only",
    "removed from browser record only",
)

LOCAL_STORAGE_WRITE = re.compile(r"(?:window\.)?localStorage\.setItem\s*\(")


def audit() -> list[str]:
    issues: list[str] = []
    for path in sorted(PRODUCTS.rglob("*.js")):
        if path.name.endswith(".min.js"):
            continue
        relative = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8")
        if LOCAL_STORAGE_WRITE.search(source) and relative not in ALLOWED_LOCAL_STORAGE_WRITERS:
            issues.append(f"{relative}: unauthorized localStorage write")
        lowered = source.lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase in lowered:
                issues.append(f"{relative}: forbidden browser-save wording: {phrase!r}")

    for relative in AUTHORITATIVE_DATA_SCRIPTS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        if "localStorage.setItem" in source or "window.localStorage.setItem" in source:
            issues.append(f"{relative}: durable product data must not be written to localStorage")

    knowledge_template = (
        ROOT / "products/reunia/templates/knowledge.html"
    ).read_text(encoding="utf-8")
    if "data-context-storage-scope" in knowledge_template:
        issues.append("products/reunia/templates/knowledge.html: obsolete browser storage scope")

    return issues


def main() -> int:
    issues = audit()
    if issues:
        print("Browser storage policy failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("Browser storage policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
