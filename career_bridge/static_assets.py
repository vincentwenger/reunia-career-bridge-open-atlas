"""Static asset selection shared by the Réunia shell and Application Builder."""
from __future__ import annotations

from pathlib import Path

_MINIFIABLE_SUFFIXES = {".css", ".js"}


def minified_asset_name(static_root: Path, filename: str, *, enabled: bool) -> str:
    """Return a generated minified sibling when production asset mode is enabled.

    Source filenames remain stable for development, debugging, tests, and direct
    static access. Production templates transparently select ``*.min.css`` and
    ``*.min.js`` only when the generated file exists, so a missing build artifact
    cannot break the page.
    """

    normalized = str(filename or "").lstrip("/")
    if not enabled:
        return normalized
    path = Path(normalized)
    if path.suffix.lower() not in _MINIFIABLE_SUFFIXES or path.stem.endswith(".min"):
        return normalized
    candidate = path.with_name(f"{path.stem}.min{path.suffix}")
    if (static_root / candidate).is_file():
        return candidate.as_posix()
    return normalized
