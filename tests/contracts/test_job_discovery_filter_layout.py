from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "products" / "resume_taylor" / "templates" / "application_builder" / "_discovery_results.html"
STYLES = ROOT / "products" / "resume_taylor" / "static" / "styles.css"
SCRIPT = ROOT / "products" / "resume_taylor" / "static" / "app.js"


class JobDiscoveryFilterLayoutContractTests(unittest.TestCase):
    def test_filters_use_one_compact_match_and_sort_group(self) -> None:
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )

        self.assertIn("Match filters", template)
        self.assertIn("All countries are included.", template)
        self.assertIn("<span>Sort by</span>", template)
        self.assertIn("discovery-match-filter-group", template)
        self.assertNotIn("Location and order", template)
        self.assertNotIn("discovery-location-filter-group", template)

    def test_filter_layout_is_responsive(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")

        self.assertIn(".discovery-filter-fields", styles)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr)) auto;", styles)
        self.assertIn("@media (max-width: 640px)", styles)
        self.assertNotIn(".discovery-location-filter-fields", styles)

    def test_obsolete_country_state_script_is_removed(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("data-discovery-country-filter", script)
        self.assertNotIn("data-discovery-us-state-filter", script)
        self.assertNotIn("state.disabled = !isUnitedStates;", script)


if __name__ == "__main__":
    unittest.main()
