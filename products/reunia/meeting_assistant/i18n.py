from __future__ import annotations

from typing import Any


SUPPORTED_LANGUAGES = {"en", "fr"}
LANGUAGE_NAMES = {"en": "English", "fr": "Français"}


def supported_language(value: Any) -> str | None:
    """Return a supported language code, or ``None`` for an invalid value."""
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized.startswith("fr"):
        return "fr"
    if normalized.startswith("en"):
        return "en"
    return None


def normalize_language(value: Any, default: str = "en") -> str:
    """Return a supported two-letter application language code."""
    return supported_language(value) or (
        default if default in SUPPORTED_LANGUAGES else "en"
    )


def transcription_language(value: Any) -> str:
    """Return the ISO-639-1 language hint expected by audio transcription APIs."""
    return normalize_language(value)


def ai_language_instruction(value: Any, *, json_values: bool = False) -> str:
    """Return an instruction that keeps generated content in the selected language."""
    if normalize_language(value) != "fr":
        return (
            "Write all user-facing content in English."
            if not json_values
            else "Write all human-readable JSON values in English while preserving the required JSON keys and structure."
        )
    if json_values:
        return (
            "Write all human-readable JSON values in French while preserving every required "
            "JSON key, enum value, numeric value, and structural field exactly as requested."
        )
    return (
        "Respond entirely in French. Use natural, professional French and keep names, "
        "technical identifiers, code, URLs, and quoted source text unchanged when appropriate."
    )
