#!/usr/bin/env python3
"""Enforce the Career Bridge CSS token namespace and migration baseline."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config" / "quality" / "css-policy.json").read_text(encoding="utf-8"))
TOKEN_FILE = ROOT / CONFIG["token_file"]
CSS_ROOTS = (
    ROOT / "products" / "reunia" / "static" / "css",
    ROOT / "products" / "resume_taylor" / "static",
)
HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
NAMED_COLOR_RE = re.compile(r"(?i):[^;{}]*(?<![-\w])(?:white|black|red|green|blue)(?![-\w])[^;{}]*;")
CUSTOM_PROPERTY_RE = re.compile(r"(?m)^\s*--([A-Za-z_][A-Za-z0-9_-]*)\s*:|var\(\s*--([A-Za-z_][A-Za-z0-9_-]*)")
PALETTE_DECLARATION_RE = re.compile(r"(?m)^\s*--cb-p-[0-9a-f]{3,8}\s*:")


def source_css_files() -> list[Path]:
    files: list[Path] = []
    for root in CSS_ROOTS:
        files.extend(
            path
            for path in root.rglob("*.css")
            if not path.name.endswith(".min.css")
        )
    return sorted(set(files))


def audit() -> list[str]:
    failures: list[str] = []
    important_count = 0
    for path in source_css_files():
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        if path != TOKEN_FILE:
            raw_colors = sorted(set(HEX_RE.findall(source)))
            if raw_colors:
                failures.append(
                    f"{relative}: raw hex colors must be moved to design-tokens.css: "
                    + ", ".join(raw_colors[:8])
                )
            uncommented = CSS_COMMENT_RE.sub("", source)
            named_colors = sorted(set(NAMED_COLOR_RE.findall(uncommented)))
            if named_colors:
                failures.append(
                    f"{relative}: named colors must use design tokens: "
                    + ", ".join(value.strip() for value in named_colors[:4])
                )
        property_names = {name for match in CUSTOM_PROPERTY_RE.findall(source) for name in match if name}
        noncanonical = sorted(
            f"--{name}" for name in property_names if not name.startswith("cb-")
        )
        if noncanonical:
            failures.append(
                f"{relative}: custom properties must use --cb-*: "
                + ", ".join(noncanonical[:8])
            )
        important_count += source.count("!important")

    maximum_important = int(CONFIG["maximum_important_declarations"])
    if important_count > maximum_important:
        failures.append(
            f"!important declarations increased: {important_count} > {maximum_important}. "
            "Prefer source-order, specificity, or a shared component rule."
        )

    token_source = TOKEN_FILE.read_text(encoding="utf-8")
    palette_count = len(PALETTE_DECLARATION_RE.findall(token_source))
    maximum_palette = int(CONFIG["maximum_exact_palette_tokens"])
    if palette_count > maximum_palette:
        failures.append(
            f"Exact palette tokens increased: {palette_count} > {maximum_palette}. "
            "Reuse or promote an existing semantic token."
        )
    return failures


def main() -> int:
    failures = audit()
    if failures:
        print("CSS token policy failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(
        "CSS token policy passed: one --cb-* namespace, no page-level raw hex colors, "
        "and no increase in !important or exact palette tokens."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
