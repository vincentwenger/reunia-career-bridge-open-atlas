#!/usr/bin/env python3
"""Fail CI when static assets exceed reviewed size budgets."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config" / "quality" / "asset-budgets.json").read_text(encoding="utf-8"))


def size(path: str) -> int:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"Required asset is missing: {path}")
    return target.stat().st_size


def main() -> int:
    failures: list[str] = []
    for path, maximum in CONFIG["files"].items():
        actual = size(path)
        if actual > maximum:
            failures.append(f"{path}: {actual:,} > {maximum:,} bytes")
    for group in CONFIG.get("groups", []):
        actual = sum(size(path) for path in group["files"])
        if actual > group["maximum_bytes"]:
            failures.append(f"{group['name']}: {actual:,} > {group['maximum_bytes']:,} bytes")
    if failures:
        print("Static asset budget exceeded:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("Static asset budgets passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
