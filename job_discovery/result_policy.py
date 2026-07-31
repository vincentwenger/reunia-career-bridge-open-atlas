"""Shared visibility and ordering policy for Job Discovery result cards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

DEFAULT_MINIMUM_FIT = 60
DEFAULT_CONFIDENCE_TIERS = ("high", "medium")
DEFAULT_RECOMMENDATION_FILTER = "all_viable"
DEFAULT_SORT_MODE = "recommended"

CONFIDENCE_TIERS = ("high", "medium", "low")
RECOMMENDATION_FILTERS = (
    "all_viable",
    "strong",
    "good",
    "stretch",
    "all",
)
SORT_MODES = ("recommended", "job_fit", "confidence", "newest")

_RECOMMENDATION_WEIGHTS = {"strong": 4, "good": 3, "stretch": 2, "low": 1}
_CONFIDENCE_WEIGHTS = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True, slots=True)
class DiscoveryResultFilters:
    """Per-view filters that also form part of the materialized-index key."""

    minimum_fit: int = DEFAULT_MINIMUM_FIT
    confidence_tiers: tuple[str, ...] = DEFAULT_CONFIDENCE_TIERS
    recommendation_filter: str = DEFAULT_RECOMMENDATION_FILTER
    sort_mode: str = DEFAULT_SORT_MODE

    def __post_init__(self) -> None:
        try:
            minimum_fit = int(self.minimum_fit)
        except (TypeError, ValueError) as exc:
            raise ValueError("minimum_fit must be an integer") from exc
        if not 0 <= minimum_fit <= 100:
            raise ValueError("minimum_fit must be between 0 and 100")

        normalized_confidences: list[str] = []
        for raw in self.confidence_tiers or ():
            value = str(raw or "").strip().casefold()
            if value in CONFIDENCE_TIERS and value not in normalized_confidences:
                normalized_confidences.append(value)
        if not normalized_confidences:
            normalized_confidences = list(DEFAULT_CONFIDENCE_TIERS)

        recommendation_filter = str(
            self.recommendation_filter or DEFAULT_RECOMMENDATION_FILTER
        ).strip().casefold()
        if recommendation_filter not in RECOMMENDATION_FILTERS:
            recommendation_filter = DEFAULT_RECOMMENDATION_FILTER

        sort_mode = str(self.sort_mode or DEFAULT_SORT_MODE).strip().casefold()
        if sort_mode not in SORT_MODES:
            sort_mode = DEFAULT_SORT_MODE

        object.__setattr__(self, "minimum_fit", minimum_fit)
        object.__setattr__(self, "confidence_tiers", tuple(normalized_confidences))
        object.__setattr__(self, "recommendation_filter", recommendation_filter)
        object.__setattr__(self, "sort_mode", sort_mode)

    @property
    def confidence_query(self) -> str:
        return ",".join(self.confidence_tiers)


def parse_confidence_query(raw: object) -> tuple[str, ...]:
    values: Iterable[object]
    if isinstance(raw, (tuple, list, set)):
        values = raw
    else:
        values = str(raw or "").split(",")
    normalized = tuple(
        value
        for value in (str(item or "").strip().casefold() for item in values)
        if value in CONFIDENCE_TIERS
    )
    return normalized or DEFAULT_CONFIDENCE_TIERS


def recommendation_tier(recommendation: object) -> str:
    value = str(recommendation or "").strip().casefold()
    if "probably not worth" in value or value.startswith("low match"):
        return "low"
    if value.startswith("strong") or "strong match" in value:
        return "strong"
    if value.startswith("good") or "worth applying" in value or "worth considering" in value:
        return "good"
    if value.startswith("stretch") or "apply selectively" in value:
        return "stretch"
    return "low"


def confidence_tier(confidence: object) -> str:
    value = str(confidence or "").strip().casefold()
    return value if value in CONFIDENCE_TIERS else "low"


def recommendation_is_allowed(tier: str, selected_filter: str) -> bool:
    selected = str(selected_filter or DEFAULT_RECOMMENDATION_FILTER).strip().casefold()
    if selected == "all":
        return True
    if selected == "all_viable":
        return tier in {"strong", "good", "stretch"}
    return tier == selected


def assessed_visibility_group(
    *,
    fit_score: float,
    recommendation: object,
    confidence: object,
    filters: DiscoveryResultFilters,
) -> str | None:
    """Return the assessed result group or ``None`` when quality-filtered.

    Low-match recommendations always remain available in their own tab. The
    main Recommended tab contains only strong/good recommendations that meet
    the selected fit and confidence controls. Stretch roles that meet those
    controls are placed in Possible matches.
    """

    rec_tier = recommendation_tier(recommendation)
    conf_tier = confidence_tier(confidence)
    if rec_tier == "low":
        return "low_match"
    if not recommendation_is_allowed(rec_tier, filters.recommendation_filter):
        return None
    if float(fit_score) < filters.minimum_fit:
        return None
    if conf_tier not in filters.confidence_tiers:
        return None
    if rec_tier in {"strong", "good"}:
        return "recommended"
    return "possible"


def assessed_sort_key(
    *,
    fit_score: float,
    recommendation: object,
    confidence: object,
    preference_score: float,
    freshness_score: float,
    posted_at: object,
    title: object,
    sort_mode: str,
) -> tuple[object, ...]:
    rec_tier = recommendation_tier(recommendation)
    conf_tier = confidence_tier(confidence)
    recommendation_weight = _RECOMMENDATION_WEIGHTS[rec_tier]
    confidence_weight = _CONFIDENCE_WEIGHTS[conf_tier]
    fit_value = float(fit_score)
    preference_value = float(preference_score)
    freshness_value = float(freshness_score)
    posted_value = _timestamp_value(posted_at)
    title_value = str(title or "").casefold()

    selected_sort = str(sort_mode or DEFAULT_SORT_MODE).strip().casefold()
    if selected_sort == "job_fit":
        return (
            fit_value,
            recommendation_weight,
            confidence_weight,
            preference_value,
            freshness_value,
            posted_value,
            title_value,
        )
    if selected_sort == "confidence":
        return (
            confidence_weight,
            recommendation_weight,
            fit_value,
            preference_value,
            freshness_value,
            posted_value,
            title_value,
        )
    if selected_sort == "newest":
        return (
            posted_value,
            recommendation_weight,
            fit_value,
            confidence_weight,
            preference_value,
            freshness_value,
            title_value,
        )
    return (
        recommendation_weight,
        fit_value,
        confidence_weight,
        preference_value,
        freshness_value,
        posted_value,
        title_value,
    )


def _timestamp_value(raw: object) -> float:
    value = str(raw or "").strip()
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()
