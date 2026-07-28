from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any


VALID_GRADES = {
    "A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"
}


def clean_json_response(raw: str) -> str:
    cleaned = str(raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_meeting_insights(raw: str) -> dict[str, Any]:
    cleaned = clean_json_response(raw)

    default_result = {
        "meeting_name": "Unnamed Meeting",
        "summary": "",
        "topics": [],
        "action_items": [],
        "open_questions": [],
    }

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return default_result

    if not isinstance(data, dict):
        return default_result

    return {
        "meeting_name": str(
            data.get("meeting_name") or "Unnamed Meeting"
        ).strip(),
        "summary": str(data.get("summary") or "").strip(),
        "topics": _unique_string_list(data.get("topics"), limit=3, item_limit=60),
        "action_items": _string_list(data.get("action_items")),
        "open_questions": _string_list(data.get("open_questions")),
    }


def parse_content_grades(raw: str) -> list[dict[str, str]]:
    try:
        data = json.loads(clean_json_response(raw))
    except (json.JSONDecodeError, TypeError):
        return []

    items = data.get("content_grades", []) if isinstance(data, dict) else []
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        grade = str(item.get("grade") or "F").strip().upper()
        result.append(
            {
                "question": str(item.get("question") or "").strip(),
                "answer": str(item.get("answer") or "").strip(),
                "relevance_analysis": str(item.get("relevance_analysis") or "").strip(),
                "grade": grade if grade in VALID_GRADES else "F",
            }
        )
    return result


def parse_form_metrics(raw: str) -> dict[str, Any]:
    empty = {
        "pace_wpm": None,
        "pace_grade": None,
        "filler_words_count": None,
        "filler_words": None,
        "filler_words_grade": None,
        "power_words_count": None,
        "power_words": None,
        "power_words_grade": None,
        "negative_words_count": None,
        "negative_words": None,
        "negative_words_grade": None,
        "negative_tone_count": None,
        "negative_tone": None,
        "negative_tone_grade": None,
        "pauses_count": None,
        "pauses_grade": None,
        "overall_assessment": None,
    }

    cleaned = clean_json_response(raw)
    if not cleaned or cleaned.lower() == "null":
        return empty

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return empty

    if not isinstance(data, dict):
        return empty

    result = dict(empty)

    def parse_optional_string_list(key: str) -> list[str] | None:
        if key not in data or data.get(key) is None:
            return None
        value = data.get(key)
        if not isinstance(value, list):
            return None
        return [
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        ]

    def parse_optional_nonnegative_int(key: str) -> int | None:
        if key not in data or data.get(key) is None:
            return None
        try:
            return max(0, int(float(data.get(key))))
        except (TypeError, ValueError):
            return None

    list_to_count = {
        "filler_words": "filler_words_count",
        "power_words": "power_words_count",
        "negative_words": "negative_words_count",
        "negative_tone": "negative_tone_count",
    }

    for list_key, count_key in list_to_count.items():
        parsed_list = parse_optional_string_list(list_key)
        result[list_key] = parsed_list
        if parsed_list is not None:
            result[count_key] = len(parsed_list)
        else:
            result[count_key] = parse_optional_nonnegative_int(count_key)

    result["pace_wpm"] = parse_optional_nonnegative_int("pace_wpm")
    result["pauses_count"] = parse_optional_nonnegative_int("pauses_count")

    grade_fields = {
        "pace_grade",
        "filler_words_grade",
        "power_words_grade",
        "negative_words_grade",
        "negative_tone_grade",
        "pauses_grade",
    }

    for key in grade_fields:
        if key not in data or data.get(key) is None:
            result[key] = None
            continue
        grade = str(data.get(key)).strip().upper()
        result[key] = grade if grade in VALID_GRADES else "F"

    if "overall_assessment" in data and data.get("overall_assessment") is not None:
        result["overall_assessment"] = str(data.get("overall_assessment")).strip()

    return result


def parse_scorecard_grading(raw: str) -> dict[str, Any]:
    """Parse the combined one-call Scorecard response.

    The public result keeps the same ``content_grades`` and ``form_metrics``
    shapes used by the rest of the application. Each section is validated
    independently so a malformed section does not corrupt the other one.
    """
    empty = {
        "content_grades": [],
        "form_metrics": parse_form_metrics("null"),
    }

    try:
        data = json.loads(clean_json_response(raw))
    except (json.JSONDecodeError, TypeError):
        return empty

    if not isinstance(data, dict):
        return empty

    content_grades = parse_content_grades(
        json.dumps({"content_grades": data.get("content_grades", [])})
    )

    form_data = data.get("form_metrics")
    if isinstance(form_data, dict):
        form_metrics = parse_form_metrics(json.dumps(form_data))
    else:
        form_metrics = parse_form_metrics("null")

    return {
        "content_grades": content_grades,
        "form_metrics": form_metrics,
    }


def parse_wins_and_improvements(raw: str) -> dict[str, list[str]]:
    try:
        data = json.loads(clean_json_response(raw))
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "key_wins": _string_list(data.get("key_wins")),
        "improvement_areas": _string_list(data.get("improvement_areas")),
    }


def normalize_transcript_item(item: dict[str, Any]) -> dict[str, Any]:
    """Read both new native structures and records written by the old code."""
    normalized = dict(item)

    content = normalized.get("content_grades")
    if isinstance(content, dict) and "L" in content:
        normalized["content_grades"] = [
            {
                key: _legacy_scalar(value)
                for key, value in entry.get("M", {}).items()
            }
            for entry in content.get("L", [])
            if isinstance(entry, dict)
        ]

    for field in ("key_wins", "improvement_areas", "topics"):
        value = normalized.get(field)
        if isinstance(value, dict) and "L" in value:
            normalized[field] = [_legacy_scalar(entry) for entry in value.get("L", [])]

    return normalized


def to_json_compatible(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_compatible(item) for item in value]
    return value


def _legacy_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("S", "N", "BOOL"):
            if key in value:
                return value[key]
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique_string_list(
    value: Any,
    *,
    limit: int | None = None,
    item_limit: int | None = None,
) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = str(item or "").strip()
        if item_limit is not None:
            normalized = normalized[:item_limit].strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if limit is not None and len(result) >= limit:
            break
    return result
