from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSettings:
    """OpenAI model choices for analysis/tailoring and evidence review."""

    key: str
    label: str
    description: str
    analysis_tailoring_model: str
    evidence_review_model: str
    analysis_tailoring_reasoning_effort: str | None
    evidence_review_reasoning_effort: str | None
    warning: str = ""


MODEL_PRESETS: dict[str, ModelSettings] = {
    "balanced": ModelSettings(
        key="balanced",
        label="Balanced — recommended",
        description=(
            "Uses GPT-5.6 Terra for job analysis, tailoring, refinements, and suggested fixes, "
            "then GPT-5.6 Sol for independent evidence review."
        ),
        analysis_tailoring_model="gpt-5.6-terra",
        evidence_review_model="gpt-5.6-sol",
        analysis_tailoring_reasoning_effort="low",
        evidence_review_reasoning_effort="medium",
    ),
    "fast": ModelSettings(
        key="fast",
        label="Fast and economical",
        description=(
            "Uses GPT-5.6 Luna for analysis and tailoring, and GPT-5.6 Terra for evidence review. "
            "This favors lower latency and cost while retaining a stronger evidence review model."
        ),
        analysis_tailoring_model="gpt-5.6-luna",
        evidence_review_model="gpt-5.6-terra",
        analysis_tailoring_reasoning_effort="low",
        evidence_review_reasoning_effort="medium",
    ),
    "accuracy": ModelSettings(
        key="accuracy",
        label="Maximum accuracy",
        description=(
            "Uses GPT-5.6 Sol for every AI step, with more reasoning for evidence review. "
            "This is the slowest and most expensive preset."
        ),
        analysis_tailoring_model="gpt-5.6-sol",
        evidence_review_model="gpt-5.6-sol",
        analysis_tailoring_reasoning_effort="medium",
        evidence_review_reasoning_effort="high",
    ),
    "testing": ModelSettings(
        key="testing",
        label="Testing — GPT-4o mini (very low cost)",
        description=(
            "Uses GPT-4o mini for every AI step. Choose this to test the interface and workflow "
            "at very low cost."
        ),
        analysis_tailoring_model="gpt-4o-mini",
        evidence_review_model="gpt-4o-mini",
        analysis_tailoring_reasoning_effort=None,
        evidence_review_reasoning_effort=None,
        warning=(
            "Testing mode can miss subtle evidence problems or produce weaker tailoring. "
            "Do not rely on it for a final application without careful manual review."
        ),
    ),
}

DEFAULT_PROCESSING_MODE = "testing"
PROCESSING_MODE_ORDER = ("testing", "balanced", "fast", "accuracy", "custom")
PROCESSING_MODE_LABELS = {
    **{key: settings.label for key, settings in MODEL_PRESETS.items()},
    "custom": "Custom models",
}
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def get_default_processing_mode() -> str:
    """Return the mode selected for every new workflow session.

    The default is deliberately not read from the environment. This guarantees that a
    fresh browser session starts in the low-cost testing preset. Users can still select
    and save another mode from the Configuration panel.
    """
    return DEFAULT_PROCESSING_MODE


def get_preset(mode: str) -> ModelSettings:
    try:
        return MODEL_PRESETS[mode]
    except KeyError as exc:
        raise ValueError(f"Unknown processing mode: {mode}") from exc


def _environment_value(name: str, default: str) -> str:
    """Return one configured environment value or its explicit default."""
    return os.environ.get(name, "").strip() or default


def get_default_analysis_tailoring_model() -> str:
    return _environment_value(
        "OPENAI_ANALYSIS_TAILORING_MODEL",
        "gpt-5.6-terra",
    )


def get_default_evidence_review_model() -> str:
    return _environment_value(
        "OPENAI_EVIDENCE_REVIEW_MODEL",
        "gpt-5.6-sol",
    )


def get_default_analysis_tailoring_effort() -> str | None:
    return _environment_effort(
        "OPENAI_ANALYSIS_TAILORING_REASONING_EFFORT",
        "low",
    )


def get_default_evidence_review_effort() -> str | None:
    return _environment_effort(
        "OPENAI_EVIDENCE_REVIEW_REASONING_EFFORT",
        "medium",
    )


def _environment_effort(name: str, default: str) -> str | None:
    value = _environment_value(name, default).casefold()
    if value == "automatic":
        return None
    return value if value in REASONING_EFFORTS else default


def model_supports_reasoning_effort(model: str) -> bool:
    """Return whether the selected model family accepts a reasoning-effort setting."""
    normalized = model.strip().casefold()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))


def validated_reasoning_effort(model: str, effort: str | None) -> str | None:
    if not effort or not model_supports_reasoning_effort(model):
        return None
    normalized = effort.strip().casefold()
    if normalized not in REASONING_EFFORTS:
        raise ValueError(
            f"Unsupported reasoning effort '{effort}'. Choose one of: {', '.join(REASONING_EFFORTS)}."
        )
    return normalized
