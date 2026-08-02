from __future__ import annotations

import unittest

from job_discovery.location_filter import (
    infer_country_codes,
    infer_us_state_codes,
    job_matches_location_filters,
    normalize_country_filter,
    normalize_us_state_filter,
)
from job_discovery.models import DiscoveredJob, WorkplaceType
from job_discovery.result_policy import DiscoveryResultFilters


def job(location: str, *, workplace_type: WorkplaceType = WorkplaceType.UNSPECIFIED) -> DiscoveredJob:
    return DiscoveredJob(
        id=f"job-{abs(hash((location, workplace_type.value)))}",
        owner_id="owner-a",
        source_id="source-a",
        external_job_id=f"external-{abs(hash(location))}",
        company="Example",
        title="Software Engineer",
        location=location,
        workplace_type=workplace_type,
        canonical_url="https://jobs.example.com/role",
    )


class JobDiscoveryLocationFilterTests(unittest.TestCase):
    def test_us_state_location_infers_country_and_state(self) -> None:
        posting = job("Portland, OR", workplace_type=WorkplaceType.HYBRID)

        self.assertEqual(frozenset({"US"}), infer_country_codes(posting))
        self.assertEqual(frozenset({"OR"}), infer_us_state_codes(posting))
        self.assertTrue(
            job_matches_location_filters(
                posting, country_code="US", us_state_code="OR"
            )
        )
        self.assertFalse(
            job_matches_location_filters(
                posting, country_code="US", us_state_code="WA"
            )
        )


    def test_quoted_multi_location_us_states_infer_united_states(self) -> None:
        for location in (
            '"Milwaukee, WI", and "Chicago, IL"',
            '["Milwaukee, WI", "Chicago, IL"]',
            '“Milwaukee, WI”, and “Chicago, IL”',
        ):
            with self.subTest(location=location):
                posting = job(location)
                self.assertEqual(
                    frozenset({"IL", "WI"}),
                    infer_us_state_codes(posting),
                )
                self.assertTrue(
                    job_matches_location_filters(posting, country_code="US")
                )

    def test_multiple_locations_tuple_infers_united_states(self) -> None:
        posting = DiscoveredJob(
            id="job-multiple-us-locations",
            owner_id="owner-a",
            source_id="source-a",
            external_job_id="external-multiple-us-locations",
            company="Example",
            title="Software Engineer",
            location="Milwaukee, WI; Chicago, IL",
            locations=("Milwaukee, WI", "Chicago, IL"),
            canonical_url="https://jobs.example.com/role",
        )

        self.assertEqual(frozenset({"US"}), infer_country_codes(posting))
        self.assertEqual(frozenset({"IL", "WI"}), infer_us_state_codes(posting))
        self.assertTrue(job_matches_location_filters(posting, country_code="US"))

    def test_country_filter_supports_non_us_locations(self) -> None:
        posting = job("Toronto, Ontario, Canada")

        self.assertTrue(job_matches_location_filters(posting, country_code="CA"))
        self.assertFalse(job_matches_location_filters(posting, country_code="US"))

    def test_state_filter_keeps_nationwide_us_remote_roles(self) -> None:
        posting = job("Remote - US", workplace_type=WorkplaceType.REMOTE)

        self.assertTrue(
            job_matches_location_filters(
                posting, country_code="US", us_state_code="WA"
            )
        )

    def test_unknown_remote_location_is_not_assigned_to_a_country(self) -> None:
        posting = job("Remote", workplace_type=WorkplaceType.REMOTE)

        self.assertFalse(job_matches_location_filters(posting, country_code="US"))
        self.assertTrue(job_matches_location_filters(posting))

    def test_invalid_query_values_are_ignored(self) -> None:
        self.assertEqual("", normalize_country_filter("not-a-country"))
        self.assertEqual("", normalize_us_state_filter("XX"))
        filters = DiscoveryResultFilters(country_code="us", us_state_code="or")
        self.assertEqual("US", filters.country_code)
        self.assertEqual("OR", filters.us_state_code)


if __name__ == "__main__":
    unittest.main()
