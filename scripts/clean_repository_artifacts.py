#!/usr/bin/env python3
"""Remove generated local artifacts that should never be submitted."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

CACHE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".stylelintcache",
}
COMPILED_SUFFIXES = {".pyc", ".pyo"}


def clean(root: Path) -> tuple[int, int]:
    removed_dirs = 0
    removed_files = 0
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and path.name in CACHE_DIR_NAMES:
            shutil.rmtree(path, ignore_errors=True)
            removed_dirs += 1
        elif path.is_file() and path.suffix.lower() in COMPILED_SUFFIXES:
            path.unlink(missing_ok=True)
            removed_files += 1
    return removed_dirs, removed_files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="Repository root")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    removed_dirs, removed_files = clean(root)
    print(f"Removed {removed_dirs} cache directories and {removed_files} compiled files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
