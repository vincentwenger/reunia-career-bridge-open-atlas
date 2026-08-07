from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_discovery.models import (
    DiscoveryScanSchedule,
    DiscoveryScheduleCadence,
    DiscoverySearchPreferences,
    WorkplaceType,
)
from job_discovery.storage import InMemoryDiscoveryStore, JsonFileDiscoveryStore


class DiscoverySearchPreferencesTests(unittest.TestCase):
    def _preferences(self, owner_id: str = "owner-a") -> DiscoverySearchPreferences:
        return DiscoverySearchPreferences(
            owner_id=owner_id,
            target_titles=("Senior Data Engineer", "Data Platform Engineer"),
            preferred_locations=("Portland, OR",),
            accepted_workplace_types=(WorkplaceType.REMOTE, WorkplaceType.HYBRID),
            preferred_employment_types=("Full-time",),
            preferred_keywords=("Snowflake", "regulatory reporting"),
            required_keywords=("SQL",),
            minimum_salary=150000,
            minimum_salary_currency="usd",
            excluded_terms=("commission only",),
            excluded_title_terms=("intern", "sales"),
            maximum_posting_age_days=14,
            require_title_match=True,
            require_workplace_match=True,
            updated_at="2026-07-30T20:00:00+00:00",
        )

    def test_memory_store_round_trips_preferences_with_owner_isolation(self) -> None:
        store = InMemoryDiscoveryStore()
        preferences = self._preferences()

        self.assertEqual(preferences, store.put_search_preferences(preferences))
        self.assertEqual(preferences, store.get_search_preferences("owner-a"))
        self.assertIsNone(store.get_search_preferences("owner-b"))
        self.assertEqual("USD", preferences.minimum_salary_currency)
        self.assertEqual(14, preferences.maximum_posting_age_days)
        self.assertEqual(("intern", "sales"), preferences.excluded_title_terms)
        self.assertEqual(
            (WorkplaceType.REMOTE, WorkplaceType.HYBRID),
            preferences.accepted_workplace_types,
        )

    def test_json_store_survives_a_second_repository_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            first = JsonFileDiscoveryStore(path)
            preferences = self._preferences()
            first.put_search_preferences(preferences)

            second = JsonFileDiscoveryStore(path)
            self.assertEqual(preferences, second.get_search_preferences("owner-a"))

    def test_empty_workplace_selection_means_no_mandatory_workplace_filter(self) -> None:
        preferences = DiscoverySearchPreferences(owner_id="owner-a")
        self.assertEqual((), preferences.accepted_workplace_types)
        self.assertFalse(preferences.require_workplace_match)
        self.assertEqual(30, preferences.maximum_posting_age_days)

    def test_any_age_is_supported_and_invalid_limits_are_rejected(self) -> None:
        self.assertIsNone(
            DiscoverySearchPreferences(
                owner_id="owner-a", maximum_posting_age_days=None
            ).maximum_posting_age_days
        )
        self.assertIsNone(
            DiscoverySearchPreferences(
                owner_id="owner-a", maximum_posting_age_days="any"
            ).maximum_posting_age_days
        )
        with self.assertRaises(ValueError):
            DiscoverySearchPreferences(
                owner_id="owner-a", maximum_posting_age_days=366
            )

    def test_scan_schedule_survives_memory_and_json_repositories(self) -> None:
        schedule = DiscoveryScanSchedule(
            owner_id="owner-a",
            cadence=DiscoveryScheduleCadence.WEEKLY,
            local_hour=9,
            weekday=2,
            timezone_name="America/Los_Angeles",
            updated_at="2026-07-30T20:00:00+00:00",
        )
        memory = InMemoryDiscoveryStore()
        self.assertEqual(schedule, memory.put_scan_schedule(schedule))
        self.assertEqual(schedule, memory.get_scan_schedule("owner-a"))
        self.assertIsNone(memory.get_scan_schedule("owner-b"))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            JsonFileDiscoveryStore(path).put_scan_schedule(schedule)
            self.assertEqual(
                schedule,
                JsonFileDiscoveryStore(path).get_scan_schedule("owner-a"),
            )


if __name__ == "__main__":
    unittest.main()
