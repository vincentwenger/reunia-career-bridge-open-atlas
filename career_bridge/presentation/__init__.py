"""Framework-neutral presentation definitions for Career Bridge."""

from career_bridge.presentation.application_builder import (
    APPLICATION_BUILDER_STEPS,
    ApplicationBuilderStep,
    ApplicationDashboardItem,
    application_builder_steps,
    validate_application_builder_model_alignment,
)
from career_bridge.presentation.feature_mapping import (
    CURRENT_APPLICATION_FOLLOW_UP_CAPABILITIES,
    REPURPOSED_FEATURES,
    SECONDARY_FEATURE_ROADMAP,
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
    "APPLICATION_BUILDER_STEPS",
    "ApplicationBuilderStep",
    "ApplicationDashboardItem",
    "application_builder_steps",
    "validate_application_builder_model_alignment",
    "CURRENT_APPLICATION_FOLLOW_UP_CAPABILITIES",
    "REPURPOSED_FEATURES",
    "SECONDARY_FEATURE_ROADMAP",
    "RepurposedFeature",
    "feature_by_legacy_name",
    "repurposed_features",
    "CAREER_NAVIGATION",
    "CareerNavigationSection",
    "career_navigation",
    "validate_navigation_model_alignment",
]
