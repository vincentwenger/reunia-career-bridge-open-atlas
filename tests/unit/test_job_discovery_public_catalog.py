from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from job_discovery.models import (
    CompanySource,
    DiscoveredJob,
    JobSourceType,
    discovered_job_id,
)
from job_discovery.public_catalog import (
    SHARED_CATALOG_SOURCE_OWNER_ID,
    public_source_key,
)
from job_discovery.service import JobDiscoveryService
from job_discovery.storage import InMemoryDiscoveryStore, JsonFileDiscoveryStore


NOW = "2026-07-30T20:00:00+00:00"


def source(owner_id: str, source_id: str) -> CompanySource:
    return CompanySource(
        id=source_id,
        owner_id=owner_id,
        company_name="Intel",
        careers_url="https://intel.wd1.myworkdayjobs.com/en-US/External",
        source_type=JobSourceType.WORKDAY,
        source_identifier="External",
    )


class CountingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_jobs(self, configured_source: CompanySource):
        self.calls += 1
        external_id = "JR0283151"
        return [
            DiscoveredJob(
                id=discovered_job_id(
                    configured_source.owner_id, configured_source.id, external_id
                ),
                owner_id=configured_source.owner_id,
                source_id=configured_source.id,
                external_job_id=external_id,
                company=configured_source.company_name,
                title="Senior Software Engineer",
                canonical_url=(
                    "https://intel.wd1.myworkdayjobs.com/en-US/External/job/"
                    "Senior-Software-Engineer_JR0283151"
                ),
                description="Build software with Python and SQL.",
                posted_at="2026-07-29T00:00:00+00:00",
                source_type=JobSourceType.WORKDAY,
            )
        ]


class SharedPublicCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryDiscoveryStore(clock=lambda: NOW)
        self.adapter = CountingAdapter()

    def service(self) -> JobDiscoveryService:
        return JobDiscoveryService(
            adapters={JobSourceType.WORKDAY: self.adapter},
            store=self.store,
            ranking_clock=lambda: NOW,
            use_shared_public_catalog=True,
        )

    def test_equivalent_sources_share_one_public_scan_but_keep_private_jobs(self) -> None:
        first_source = source("owner-a", "intel-a")
        second_source = source("owner-b", "intel-b")

        first = self.service().discover([first_source])
        second = self.service().discover([second_source])

        self.assertEqual(1, self.adapter.calls)
        self.assertEqual(1, first.shared_catalog_refreshes)
        self.assertEqual(1, second.shared_catalog_hits)
        self.assertEqual("owner-a", first.jobs[0].owner_id)
        self.assertEqual("intel-a", first.jobs[0].source_id)
        self.assertEqual("owner-b", second.jobs[0].owner_id)
        self.assertEqual("intel-b", second.jobs[0].source_id)
        self.assertNotEqual(first.jobs[0].id, second.jobs[0].id)
        self.assertEqual(
            public_source_key(first_source), public_source_key(second_source)
        )

    def test_opening_discovery_can_hydrate_newer_shared_jobs_without_http(self) -> None:
        first_source = source("owner-a", "intel-a")
        second_source = self.store.put_company_source(source("owner-b", "intel-b"))
        self.service().discover([first_source])
        self.assertEqual([], self.store.list_discovered_jobs("owner-b"))

        hydrated = self.service().hydrate_from_shared_catalog([second_source])

        self.assertEqual(1, hydrated)
        self.assertEqual(1, self.adapter.calls)
        owner_jobs = self.store.list_discovered_jobs("owner-b")
        self.assertEqual(1, len(owner_jobs))
        self.assertEqual("owner-b", owner_jobs[0].owner_id)
        self.assertEqual("intel-b", owner_jobs[0].source_id)

    def test_json_store_persists_shared_catalog_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            first_store = JsonFileDiscoveryStore(path, clock=lambda: NOW)
            first_service = JobDiscoveryService(
                adapters={JobSourceType.WORKDAY: self.adapter},
                store=first_store,
                ranking_clock=lambda: NOW,
                use_shared_public_catalog=True,
            )
            first_service.discover([source("owner-a", "intel-a")])

            second_store = JsonFileDiscoveryStore(path, clock=lambda: NOW)
            second_service = JobDiscoveryService(
                adapters={JobSourceType.WORKDAY: self.adapter},
                store=second_store,
                ranking_clock=lambda: NOW,
                use_shared_public_catalog=True,
            )
            result = second_service.discover([source("owner-b", "intel-b")])

            self.assertEqual(1, self.adapter.calls)
            self.assertEqual(1, result.shared_catalog_hits)
            self.assertEqual("owner-b", result.jobs[0].owner_id)

    def test_workday_locale_url_variants_share_the_same_catalog(self) -> None:
        localized = source("owner-a", "intel-a")
        unlocalized = replace(
            source("owner-b", "intel-b"),
            careers_url="https://intel.wd1.myworkdayjobs.com/External",
        )
        self.assertEqual(public_source_key(localized), public_source_key(unlocalized))

    def test_full_scheduled_scan_upgrades_a_fresh_partial_browser_catalog(self) -> None:
        configured = source("owner-a", "intel-a")
        browser_transform = lambda item: replace(
            item, filters={**item.filters, "max_jobs": 80}
        )

        first = self.service().discover(
            [configured], source_fetch_transform=browser_transform
        )
        second = self.service().discover(
            [configured], source_fetch_transform=browser_transform
        )
        scheduled = self.service().discover([configured])

        self.assertEqual(2, self.adapter.calls)
        self.assertEqual(1, first.shared_catalog_refreshes)
        self.assertEqual(1, second.shared_catalog_hits)
        self.assertEqual(1, scheduled.shared_catalog_refreshes)
        status = self.store.get_public_catalog_status(public_source_key(configured))
        self.assertIsNotNone(status)
        self.assertTrue(status.complete_scan)

    def test_centrally_managed_sources_materialize_for_every_user(self) -> None:
        catalog_source = self.store.put_company_source(
            source(SHARED_CATALOG_SOURCE_OWNER_ID, "intel-shared")
        )
        self.service().discover([catalog_source], candidate_profile=None)

        first_count = self.service().hydrate_owner_from_shared_catalog(
            "owner-a", [catalog_source]
        )
        second_count = self.service().hydrate_owner_from_shared_catalog(
            "owner-b", [catalog_source]
        )

        self.assertEqual(1, self.adapter.calls)
        self.assertEqual(1, first_count)
        self.assertEqual(1, second_count)
        first_job = self.store.list_discovered_jobs("owner-a")[0]
        second_job = self.store.list_discovered_jobs("owner-b")[0]
        self.assertEqual("intel-shared", first_job.source_id)
        self.assertEqual("intel-shared", second_job.source_id)
        self.assertNotEqual(first_job.id, second_job.id)
        self.assertEqual("owner-a", first_job.owner_id)
        self.assertEqual("owner-b", second_job.owner_id)

    def test_disabling_central_source_updates_user_copy_without_http(self) -> None:
        catalog_source = self.store.put_company_source(
            source(SHARED_CATALOG_SOURCE_OWNER_ID, "intel-shared")
        )
        self.service().discover([catalog_source], candidate_profile=None)
        self.service().hydrate_owner_from_shared_catalog("owner-a", [catalog_source])

        current_catalog_source = self.store.get_company_source(
            SHARED_CATALOG_SOURCE_OWNER_ID, "intel-shared"
        )
        self.assertIsNotNone(current_catalog_source)
        disabled = self.store.put_company_source(
            replace(current_catalog_source, enabled=False)
        )
        hydrated = self.service().hydrate_owner_from_shared_catalog(
            "owner-a", [disabled]
        )

        self.assertEqual(0, hydrated)
        self.assertEqual(1, self.adapter.calls)
        owner_source = self.store.get_company_source("owner-a", "intel-shared")
        self.assertIsNotNone(owner_source)
        self.assertFalse(owner_source.enabled)

    def test_private_source_can_opt_out_of_shared_catalog(self) -> None:
        private_a = replace(
            source("owner-a", "intel-a"),
            filters={"public_catalog_enabled": False},
        )
        private_b = replace(
            source("owner-b", "intel-b"),
            filters={"public_catalog_enabled": False},
        )

        self.service().discover([private_a])
        self.service().discover([private_b])

        self.assertEqual(2, self.adapter.calls)


if __name__ == "__main__":
    unittest.main()
