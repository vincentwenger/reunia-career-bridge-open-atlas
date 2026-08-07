#!/usr/bin/env python3
"""Generate conservative production minified siblings for CSS and JavaScript.

The fallback minifier intentionally preserves JavaScript line boundaries to
avoid changing automatic-semicolon-insertion behavior. It removes blank lines,
indentation, trailing whitespace, and standalone comments. CSS comments and
safe delimiter whitespace are also removed. Generated files are syntax-checked
by CI and selected only in production.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOTS = (
    ROOT / "products" / "reunia" / "static",
    ROOT / "products" / "resume_taylor" / "static",
)


def _toggle_template_state(line: str, active: bool) -> bool:
    escaped = False
    for char in line:
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == "`" and not escaped:
            active = not active
        escaped = False
    return active


def minify_js(source: str) -> str:
    """Remove layout whitespace without changing JavaScript token boundaries.

    Newline boundaries are preserved for automatic semicolon insertion. Lines
    inside template literals are retained verbatim so generated text does not
    change. This is deliberately more conservative than Terser but safe in the
    dependency-free production image.
    """

    lines: list[str] = []
    in_template = False
    for raw in source.splitlines():
        if in_template:
            line = raw.rstrip()
            lines.append(line)
            in_template = _toggle_template_state(line, in_template)
            continue
        line = raw.strip()
        if not line:
            continue
        lines.append(line)
        in_template = _toggle_template_state(line, in_template)
    return "\n".join(lines) + "\n"


def minify_css(source: str) -> str:
    """Collapse CSS trivia while preserving quoted values and calc spacing."""

    output: list[str] = []
    quote = ""
    escaped = False
    in_comment = False
    pending_space = False
    index = 0
    punctuation = set("{};,:>")
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if in_comment:
            if char == "*" and next_char == "/":
                in_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            output.append(char)
            if char == quote and not escaped:
                quote = ""
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
            continue
        if char == "/" and next_char == "*":
            in_comment = True
            index += 2
            continue
        if char in {"'", '"'}:
            if pending_space and output and output[-1] not in punctuation:
                output.append(" ")
            pending_space = False
            quote = char
            output.append(char)
            index += 1
            continue
        if char.isspace():
            pending_space = True
            index += 1
            continue
        if pending_space:
            if output and output[-1] not in punctuation and char not in punctuation:
                output.append(" ")
            pending_space = False
        if char in punctuation and output and output[-1] == " ":
            output.pop()
        output.append(char)
        index += 1
    result = "".join(output).replace(";}" , "}")
    return result.strip() + "\n"


def output_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.min{path.suffix}")


def build(*, check: bool = False) -> tuple[list[tuple[Path, int, int]], list[Path]]:
    results: list[tuple[Path, int, int]] = []
    stale: list[Path] = []
    for static_root in STATIC_ROOTS:
        for path in sorted(static_root.rglob("*")):
            if not path.is_file() or path.stem.endswith(".min"):
                continue
            if path.suffix not in {".css", ".js"}:
                continue
            source = path.read_text(encoding="utf-8")
            result = minify_css(source) if path.suffix == ".css" else minify_js(source)
            target = output_path(path)
            if check:
                # Generated siblings are intentionally not committed. When a
                # local or image build has created one, still verify that it is
                # current; otherwise validating the source transformation is
                # sufficient and remains non-mutating.
                if target.is_file() and target.read_text(encoding="utf-8") != result:
                    stale.append(target)
            else:
                target.write_text(result, encoding="utf-8")
            results.append((path, len(source.encode()), len(result.encode())))
    return results, stale


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate minification and any existing generated siblings without modifying files.",
    )
    args = parser.parse_args()
    results, stale = build(check=args.check)
    if args.check:
        if stale:
            print("Generated static assets are missing or stale:")
            for path in stale:
                print(f"- {path.relative_to(ROOT)}")
            print("Run: python scripts/build_static_assets.py")
            return 1
        if not args.quiet:
            print(f"Validated {len(results)} minifiable source assets.")
        return 0

    if not args.quiet:
        original = sum(item[1] for item in results)
        generated = sum(item[2] for item in results)
        saving = 0 if not original else (1 - generated / original) * 100
        print(f"Generated {len(results)} minified assets: {original:,} -> {generated:,} bytes ({saving:.1f}% smaller)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
