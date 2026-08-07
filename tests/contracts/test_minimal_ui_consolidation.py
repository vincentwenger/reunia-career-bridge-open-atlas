"""Regression contracts for the coordinated minimal-UI cleanup."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class MinimalUIConsolidationContractTests(unittest.TestCase):
    def test_application_cards_have_one_primary_action_and_collapsed_secondary_actions(self) -> None:
        text = (ROOT / "products/resume_taylor/templates/application_builder/applications.html").read_text(encoding="utf-8")
        self.assertIn(">Open application</a>", text)
        self.assertIn('class="application-card-more"', text)
        self.assertIn('<summary aria-haspopup="menu">More</summary>', text)
        self.assertIn('>Materials</a>', text)
        self.assertIn('data-application-menu', text)
        self.assertIn("Interview preparation", text)
        self.assertIn("Resume stage", text)
        self.assertIn("Interview readiness", text)
        self.assertNotIn("Start resume", text)
        self.assertNotIn("Continue resume", text)
        self.assertNotIn("Save only", text)
        self.assertIn('name="start_builder" value="1"', text)

    def test_application_cards_use_a_compact_responsive_grid(self) -> None:
        styles = (ROOT / "products/resume_taylor/static/styles.css").read_text(encoding="utf-8")
        bridge = (ROOT / "products/resume_taylor/static/career_bridge.css").read_text(encoding="utf-8")
        self.assertIn(".application-dashboard-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }", styles)
        self.assertIn(".application-dashboard-grid { grid-template-columns: repeat(2, minmax(0, 1fr));", bridge)
        self.assertIn("@media (max-width: 1080px)", bridge)
        self.assertIn("padding: 15px !important", bridge)

    def test_interview_readiness_has_separate_readable_status_elements(self) -> None:
        text = (ROOT / "products/resume_taylor/templates/application_builder/applications.html").read_text(encoding="utf-8")
        styles = (ROOT / "products/resume_taylor/static/styles.css").read_text(encoding="utf-8")
        self.assertIn('class="application-readiness-value"', text)
        self.assertIn('class="application-readiness-status"', text)
        self.assertIn('class="application-readiness-empty">Not started</span>', text)
        self.assertNotIn('<strong>{{ readiness.label }}</strong><small>{{ readiness.status_label }}</small>', text)
        self.assertIn(".application-readiness-status", styles)

    def test_action_plan_keeps_search_and_status_visible_and_collapses_advanced_filters(self) -> None:
        text = (ROOT / "products/reunia/templates/action-center.html").read_text(encoding="utf-8")
        details_start = text.index('class="action-advanced-filters"')
        self.assertLess(text.index('id="action-search"'), details_start)
        self.assertLess(text.index('id="action-status-filter"'), details_start)
        for control in ("action-application-filter", "action-source-filter", "action-due-filter", "action-priority-filter", "action-sort"):
            self.assertGreater(text.index(f'id="{control}"'), details_start)
        self.assertIn("<summary>More filters</summary>", text)

    def test_progress_keeps_four_metrics_visible_and_collapses_five_more(self) -> None:
        text = (ROOT / "products/reunia/templates/analytics.html").read_text(encoding="utf-8")
        primary = text[text.index('impact-metric-grid-primary'):text.index('class="impact-more-metrics"')]
        secondary = text[text.index('class="impact-more-metrics"'):text.index('class="impact-story-grid"')]
        self.assertEqual(primary.count('class="impact-metric-card"'), 4)
        self.assertEqual(secondary.count('class="impact-metric-card"'), 5)
        self.assertIn("<summary>Additional measurements</summary>", secondary)

    def test_grouped_navbar_css_has_one_consolidated_block(self) -> None:
        text = (ROOT / "products/reunia/static/css/navbar.css").read_text(encoding="utf-8")
        self.assertEqual(text.count("/* Career Bridge authenticated navigation */"), 1)
        self.assertNotIn("/* Career Bridge top-level navigation */", text)
        self.assertNotIn("/* Grouped Career Bridge navigation */", text)
        self.assertNotIn("/* Minimal Career Bridge navigation */", text)
        self.assertEqual(text.count(".navbar-minimal .nav-group-trigger {"), 2)  # base and mobile rule


if __name__ == "__main__":
    unittest.main()
