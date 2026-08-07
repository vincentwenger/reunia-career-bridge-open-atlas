"""Career Evidence Library readiness calculations."""

from __future__ import annotations

from typing import Any, Mapping


def build_evidence_library_readiness(
    library: Mapping[str, Any] | None,
) -> dict[str, int | bool]:
    """Summarize reusable evidence that can support future applications.

    Processed library documents, supportive confirmed answers, and active
    confirmed career roles count as ready evidence. Negative answers and roles
    that still need review are retained by the product but do not make the
    evidence library ready.
    """

    payload = library or {}
    document_count = sum(
        1
        for item in payload.get("files", []) or []
        if str(item.get("status") or "ready").strip().casefold() == "ready"
    )
    answer_count = sum(
        1
        for item in payload.get("evidence_answers", []) or []
        if item.get("yes_no") is not False
        and str(item.get("confirmation_status") or "confirmed").strip().casefold() == "confirmed"
        and bool(str(item.get("answer_text") or "").strip())
    )
    confirmed_role_count = sum(
        1
        for item in payload.get("career_roles", []) or []
        if bool(item.get("source_active", True))
        and str(item.get("status") or "").strip().casefold() == "confirmed"
    )
    item_count = document_count + answer_count + confirmed_role_count
    return {
        "ready": item_count > 0,
        "item_count": item_count,
        "document_count": document_count,
        "answer_count": answer_count,
        "confirmed_role_count": confirmed_role_count,
    }
