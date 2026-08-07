"""Chronological ordering helpers for Baseline Resume employment roles."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_MONTH_NUMBERS = {
    "jan": 1,
    "january": 1,
    "janv": 1,
    "janvier": 1,
    "feb": 2,
    "february": 2,
    "fev": 2,
    "fevr": 2,
    "fevrier": 2,
    "mar": 3,
    "march": 3,
    "mars": 3,
    "apr": 4,
    "april": 4,
    "avr": 4,
    "avril": 4,
    "may": 5,
    "mai": 5,
    "jun": 6,
    "june": 6,
    "juin": 6,
    "jul": 7,
    "july": 7,
    "juil": 7,
    "juillet": 7,
    "aug": 8,
    "august": 8,
    "aout": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "septembre": 9,
    "oct": 10,
    "october": 10,
    "octobre": 10,
    "nov": 11,
    "november": 11,
    "novembre": 11,
    "dec": 12,
    "december": 12,
    "decembre": 12,
}
_CURRENT_ROLE_MARKERS = (
    "present",
    "current",
    "now",
    "ongoing",
    "today",
    "actuel",
    "actuelle",
    "actuellement",
    "en cours",
    "aujourd'hui",
)


def _normalized_date_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        character for character in text if not unicodedata.combining(character)
    ).casefold()


def career_role_date_points(value: Any) -> tuple[list[tuple[int, int]], bool]:
    """Return parsed ``(year, month)`` points and whether the role is current.

    Baseline resumes contain display-oriented ranges rather than a strict date
    schema. The parser supports formats commonly produced by resume imports:
    ``MM/YYYY``, ``YYYY-MM``, year-only ranges, and English or French month names.
    """

    text = _normalized_date_text(value)
    current = any(marker in text for marker in _CURRENT_ROLE_MARKERS)
    points: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(match: re.Match[str]) -> bool:
        return any(
            start < match.end() and match.start() < end for start, end in occupied
        )

    def add(match: re.Match[str], year: int, month: int) -> None:
        if 1900 <= year <= 2100 and 1 <= month <= 12:
            points.append((year, month))
            occupied.append(match.span())

    for match in re.finditer(
        r"(?<!\d)(0?[1-9]|1[0-2])\s*[/.-]\s*((?:19|20)\d{2})(?!\d)", text
    ):
        add(match, int(match.group(2)), int(match.group(1)))
    for match in re.finditer(
        r"(?<!\d)((?:19|20)\d{2})\s*[/.-]\s*(0?[1-9]|1[0-2])(?!\d)", text
    ):
        if not overlaps(match):
            add(match, int(match.group(1)), int(match.group(2)))

    month_names = "|".join(
        sorted((re.escape(name) for name in _MONTH_NUMBERS), key=len, reverse=True)
    )
    for match in re.finditer(
        rf"\b({month_names})\.?\s+((?:19|20)\d{{2}})\b", text
    ):
        if not overlaps(match):
            add(match, int(match.group(2)), _MONTH_NUMBERS[match.group(1)])
    for match in re.finditer(
        rf"\b((?:19|20)\d{{2}})\s+({month_names})\.?\b", text
    ):
        if not overlaps(match):
            add(match, int(match.group(1)), _MONTH_NUMBERS[match.group(2)])

    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text):
        if not any(
            start <= match.start() and match.end() <= end for start, end in occupied
        ):
            # A year-only endpoint is treated as December so it follows roles
            # ending earlier in that same year.
            points.append((int(match.group(1)), 12))

    return points, current


def career_role_date_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Sort key for most-recent-first employment-role display."""

    points, current = career_role_date_points(item.get("dates"))
    has_date = bool(points) or current
    end_year, end_month = (
        (9999, 12) if current else (max(points) if points else (0, 0))
    )
    start_year, start_month = min(points) if points else (0, 0)
    return (
        0 if has_date else 1,
        -(end_year * 12 + end_month),
        -(start_year * 12 + start_month),
        0 if bool(item.get("source_active", True)) else 1,
        str(item.get("employer") or "").casefold(),
        str(item.get("official_title") or "").casefold(),
        str(item.get("role_id") or ""),
    )
