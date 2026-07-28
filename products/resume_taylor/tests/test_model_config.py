from __future__ import annotations

import pytest

from resume_tailor.web_state import WorkflowState

from resume_tailor.model_config import (
    MODEL_PRESETS,
    DEFAULT_PROCESSING_MODE,
    PROCESSING_MODE_ORDER,
    get_default_evidence_review_effort,
    get_default_evidence_review_model,
    get_default_analysis_tailoring_effort,
    get_default_analysis_tailoring_model,
    get_default_processing_mode,
    model_supports_reasoning_effort,
    validated_reasoning_effort,
)


def test_processing_modes_put_testing_first_then_include_all_other_choices():
    assert PROCESSING_MODE_ORDER == ("testing", "balanced", "fast", "accuracy", "custom")


def test_testing_is_the_unconditional_default_processing_mode(monkeypatch):
    assert DEFAULT_PROCESSING_MODE == "testing"
    monkeypatch.setenv("OPENAI_PROCESSING_MODE", "balanced")
    assert get_default_processing_mode() == "testing"


def test_balanced_mode_uses_stronger_final_evidence_review_model():
    settings = MODEL_PRESETS["balanced"]
    assert settings.analysis_tailoring_model == "gpt-5.6-terra"
    assert settings.evidence_review_model == "gpt-5.6-sol"
    assert settings.analysis_tailoring_reasoning_effort == "low"
    assert settings.evidence_review_reasoning_effort == "medium"


def test_testing_mode_uses_gpt_4o_mini_without_reasoning_effort():
    settings = MODEL_PRESETS["testing"]
    assert settings.analysis_tailoring_model == "gpt-4o-mini"
    assert settings.evidence_review_model == "gpt-4o-mini"
    assert settings.analysis_tailoring_reasoning_effort is None
    assert settings.evidence_review_reasoning_effort is None
    assert settings.warning


def test_reasoning_effort_is_omitted_for_non_reasoning_models():
    assert model_supports_reasoning_effort("gpt-4o-mini") is False
    assert validated_reasoning_effort("gpt-4o-mini", "high") is None


def test_reasoning_effort_is_validated_for_reasoning_models():
    assert model_supports_reasoning_effort("gpt-5.6-terra") is True
    assert validated_reasoning_effort("gpt-5.6-terra", "low") == "low"
    with pytest.raises(ValueError):
        validated_reasoning_effort("gpt-5.6-terra", "turbo")


def test_app_exposes_processing_mode_and_separate_evidence_review_model(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    assert "Processing mode" in template
    assert "Analysis and tailoring model" in template
    assert "Evidence review model (Step 3)" in template
    assert "evidence review model" in source.casefold()
    assert "clear_tailoring_results" in source


def test_workflow_state_defaults_to_testing(profile):
    state = WorkflowState(source_profile=profile)
    assert state.processing_mode == "testing"


def test_custom_model_environment_names_match_webapp_labels(monkeypatch):
    monkeypatch.setenv("OPENAI_ANALYSIS_TAILORING_MODEL", "analysis-model")
    monkeypatch.setenv("OPENAI_EVIDENCE_REVIEW_MODEL", "evidence-model")
    monkeypatch.setenv("OPENAI_ANALYSIS_TAILORING_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENAI_EVIDENCE_REVIEW_REASONING_EFFORT", "high")

    assert get_default_analysis_tailoring_model() == "analysis-model"
    assert get_default_evidence_review_model() == "evidence-model"
    assert get_default_analysis_tailoring_effort() == "medium"
    assert get_default_evidence_review_effort() == "high"
