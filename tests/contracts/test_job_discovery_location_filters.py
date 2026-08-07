from __future__ import annotations

import unittest
from pathlib import Path

from tests.application_builder_source import application_builder_source


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_results.html"
)


class JobDiscoveryLocationFilterContractTests(unittest.TestCase):
    def test_public_results_default_to_all_countries_without_location_controls(self) -> None:
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )

        self.assertIn("All countries are included.", template)
        self.assertNotIn("<span>Country</span>", template)
        self.assertNotIn('select name="country"', template)
        self.assertNotIn("<span>U.S. state</span>", template)
        self.assertNotIn('select name="us_state"', template)
        self.assertNotIn("United States is selected by default.", template)
        self.assertNotIn("data-discovery-country-filter", template)
        self.assertNotIn("data-discovery-us-state-filter", template)

    def test_result_index_ignores_country_and_state_query_parameters(self) -> None:
        source = application_builder_source()

        self.assertIn('country_code=""', source)
        self.assertIn('us_state_code=""', source)
        self.assertIn("Country and U.S.-state result filters are intentionally ignored.", source)
        self.assertNotIn(
            'country_code = source.get("country", "") if "country" in source else "US"',
            source,
        )
        self.assertNotIn('source.get("us_state", "")', source)
        self.assertNotIn('f"country={filters.country_code}"', source)
        self.assertNotIn('f"us_state={filters.us_state_code}"', source)
        self.assertIn("job_matches_location_filters(", source)
        self.assertIn('_DISCOVERY_RESULT_INDEX_VERSION = "6"', source)


if __name__ == "__main__":
    unittest.main()
