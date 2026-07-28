"""Framework-neutral presentation definitions for Career Bridge."""

from career_bridge.presentation.feature_mapping import (
    REPURPOSED_FEATURES,
    RepurposedFeature,
    feature_by_legacy_name,
    repurposed_features,
)
from career_bridge.presentation.navigation import (
    CAREER_NAVIGATION,
    CareerNavigationSection,
    career_navigation,
    validate_navigation_model_alignment,
)

__all__ = [
    "REPURPOSED_FEATURES",
    "RepurposedFeature",
    "feature_by_legacy_name",
    "repurposed_features",
    "CAREER_NAVIGATION",
    "CareerNavigationSection",
    "career_navigation",
    "validate_navigation_model_alignment",
]
