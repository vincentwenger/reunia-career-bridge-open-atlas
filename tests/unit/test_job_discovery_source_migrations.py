from __future__ import annotations

import unittest

from job_discovery.models import CompanySource, JobSourceType
from job_discovery.service import JobDiscoveryService
from job_discovery.source_migrations import migrate_known_company_source
from job_discovery.storage import InMemoryDiscoveryStore


class RecordingAdapter:
    def __init__(self) -> None:
        self.sources: list[CompanySource] = []

    def fetch_jobs(self, source: CompanySource):
        self.sources.append(source)
        return []


class SourceMigrationTests(unittest.TestCase):
    def first_tech_jobvite_source(self) -> CompanySource:
        return CompanySource(
            id="first-tech",
            owner_id="owner",
            company_name="First Tech Federal Credit Union",
            careers_url="https://jobs.jobvite.com/firsttechfed/jobs",
            source_type=JobSourceType.JOBVITE,
            source_identifier="firsttechfed",
        )

    def test_migrates_retired_first_tech_jobvite_board_to_current_ttc_portal(self) -> None:
        migrated = migrate_known_company_source(self.first_tech_jobvite_source())

        self.assertEqual(JobSourceType.TALEMETRY_TTC, migrated.source_type)
        self.assertEqual(
            "https://firsttechfedcareers.ttcportals.com/search/jobs",
            migrated.careers_url,
        )
        self.assertEqual("", migrated.source_identifier)
        self.assertEqual("first-tech", migrated.id)

    def test_does_not_rewrite_other_jobvite_boards(self) -> None:
        source = CompanySource(
            id="other",
            owner_id="owner",
            company_name="Other Company",
            careers_url="https://jobs.jobvite.com/othercompany/jobs",
            source_type=JobSourceType.JOBVITE,
            source_identifier="othercompany",
        )

        self.assertIs(source, migrate_known_company_source(source))

    def test_discovery_persists_migration_before_selecting_adapter(self) -> None:
        store = InMemoryDiscoveryStore()
        ttc = RecordingAdapter()
        jobvite = RecordingAdapter()
        service = JobDiscoveryService(
            adapters={
                JobSourceType.TALEMETRY_TTC: ttc,
                JobSourceType.JOBVITE: jobvite,
            },
            store=store,
        )

        result = service.discover(
            [self.first_tech_jobvite_source()],
            analyze_new_jobs=False,
        )

        self.assertEqual((), result.errors)
        self.assertEqual(1, len(ttc.sources))
        self.assertEqual([], jobvite.sources)
        stored = store.get_company_source("owner", "first-tech")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(JobSourceType.TALEMETRY_TTC, stored.source_type)
        self.assertEqual(
            "https://firsttechfedcareers.ttcportals.com/search/jobs",
            stored.careers_url,
        )


if __name__ == "__main__":
    unittest.main()
