"""Small parsing helpers shared by public job-source adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypeVar

from ..normalization import normalize_whitespace

Number = TypeVar("Number", int, float)


def _bounded_number(
    value: object,
    *,
    default: Number,
    minimum: Number,
    maximum: Number,
    converter: type[Number],
) -> Number:
    try:
        parsed = converter(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """Parse an integer and clamp it to an inclusive range."""

    return _bounded_number(
        value,
        default=default,
        minimum=minimum,
        maximum=maximum,
        converter=int,
    )


def bounded_float(
    value: object, default: float, minimum: float, maximum: float
) -> float:
    """Parse a float and clamp it to an inclusive range."""

    return _bounded_number(
        value,
        default=default,
        minimum=minimum,
        maximum=maximum,
        converter=float,
    )


def walk_json(value: Any) -> Iterable[Mapping[str, Any]]:
    """Yield every mapping contained in a nested JSON-compatible value."""

    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_json(nested)


def jsonld_address_locations(
    value: Any,
    *,
    address_fields: Sequence[str] = (
        "addressLocality",
        "addressRegion",
        "addressCountry",
    ),
) -> tuple[str, ...]:
    """Extract unique, readable addresses from JSON-LD job locations."""

    items = value if isinstance(value, list) else [value]
    locations: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        address_value = item.get("address")
        address = address_value if isinstance(address_value, Mapping) else {}
        text = ", ".join(
            part
            for field in address_fields
            if (part := normalize_whitespace(address.get(field)))
        )
        key = text.casefold()
        if text and key not in seen:
            locations.append(text)
            seen.add(key)
    return tuple(locations)
