"""Visibility and sorting rules for assessed Job Discovery results."""

from __future__ import annotations

import unittest

from job_discovery.result_policy import (
    DiscoveryResultFilters,
    assessed_sort_key,
    assessed_visibility_group,
    confidence_tier,
    parse_confidence_query,
    recommendation_tier,
)


class DiscoveryResultPolicyTests(unittest.TestCase):
    def test_default_recommended_requires_strong_or_good_fit_60_and_high_or_medium_confidence(self) -> None:
        filters = DiscoveryResultFilters()

        self.assertEqual(
            "recommended",
            assessed_visibility_group(
                fit_score=82,
                recommendation="Strong match — Apply",
                confidence="Medium",
                filters=filters,
            ),
        )
        self.assertEqual(
            "recommended",
            assessed_visibility_group(
                fit_score=70,
                recommendation="Good match — Worth applying",
                confidence="High",
                filters=filters,
            ),
        )
        self.assertEqual(
            "possible",
            assessed_visibility_group(
                fit_score=64,
                recommendation="Stretch opportunity — Apply selectively",
                confidence="Medium",
                filters=filters,
            ),
        )

    def test_low_match_is_separate_and_low_confidence_is_hidden_by_default(self) -> None:
        filters = DiscoveryResultFilters()

        self.assertEqual(
            "low_match",
            assessed_visibility_group(
                fit_score=45,
                recommendation="Low match — Probably not worth your time",
                confidence="High",
                filters=filters,
            ),
        )
        self.assertIsNone(
            assessed_visibility_group(
                fit_score=88,
                recommendation="Strong match — Apply",
                confidence="Low",
                filters=filters,
            )
        )

    def test_user_can_broaden_confidence_and_fit_filters(self) -> None:
        filters = DiscoveryResultFilters(
            minimum_fit=50,
            confidence_tiers=("high", "medium", "low"),
        )

        self.assertEqual(
            "recommended",
            assessed_visibility_group(
                fit_score=85,
                recommendation="Strong match — Apply",
                confidence="Low",
                filters=filters,
            ),
        )
        self.assertEqual(
            "possible",
            assessed_visibility_group(
                fit_score=55,
                recommendation="Stretch opportunity — Apply selectively",
                confidence="Low",
                filters=filters,
            ),
        )

    def test_recommendation_filter_can_narrow_visible_results(self) -> None:
        filters = DiscoveryResultFilters(recommendation_filter="strong")
        self.assertEqual(
            "recommended",
            assessed_visibility_group(
                fit_score=90,
                recommendation="Strong match — Apply",
                confidence="High",
                filters=filters,
            ),
        )
        self.assertIsNone(
            assessed_visibility_group(
                fit_score=75,
                recommendation="Good match — Worth applying",
                confidence="High",
                filters=filters,
            )
        )

    def test_recommended_sort_uses_recommendation_then_fit_then_confidence(self) -> None:
        strong_medium = assessed_sort_key(
            fit_score=82,
            recommendation="Strong match — Apply",
            confidence="Medium",
            preference_score=75,
            freshness_score=80,
            posted_at="2026-07-29T00:00:00+00:00",
            title="Strong",
            sort_mode="recommended",
        )
        good_high = assessed_sort_key(
            fit_score=95,
            recommendation="Good match — Worth applying",
            confidence="High",
            preference_score=95,
            freshness_score=100,
            posted_at="2026-07-30T00:00:00+00:00",
            title="Good",
            sort_mode="recommended",
        )
        self.assertGreater(strong_medium, good_high)

        high_confidence = assessed_sort_key(
            fit_score=80,
            recommendation="Strong match — Apply",
            confidence="High",
            preference_score=70,
            freshness_score=70,
            posted_at="2026-07-28T00:00:00+00:00",
            title="High",
            sort_mode="confidence",
        )
        medium_confidence = assessed_sort_key(
            fit_score=95,
            recommendation="Strong match — Apply",
            confidence="Medium",
            preference_score=90,
            freshness_score=90,
            posted_at="2026-07-30T00:00:00+00:00",
            title="Medium",
            sort_mode="confidence",
        )
        self.assertGreater(high_confidence, medium_confidence)

    def test_normalization_accepts_current_labels_and_query_values(self) -> None:
        self.assertEqual("good", recommendation_tier("Good match — Worth applying"))
        self.assertEqual("low", recommendation_tier("Low match — Probably not worth your time"))
        self.assertEqual("medium", confidence_tier("Medium"))
        self.assertEqual(("high", "low"), parse_confidence_query("high,low"))


if __name__ == "__main__":
    unittest.main()
