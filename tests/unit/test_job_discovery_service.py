from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_discovery.deduplication import deduplicate_jobs
from job_discovery.models import (
    CompanySource,
    DiscoveredJob,
    JobSourceType,
    WorkplaceType,
    discovered_job_id,
)
from job_discovery.ranking import CandidateJobProfile
from job_discovery.service import JobDiscoveryService, PUBLIC_COVERAGE_DESCRIPTION
from job_discovery.storage import InMemoryDiscoveryStore, JsonFileDiscoveryStore


class StaticAdapter:
    def __init__(self, jobs):
        self.jobs = jobs

    def fetch_jobs(self, source):
        return list(self.jobs)


class FailingAdapter:
    def fetch_jobs(self, source):
        raise RuntimeError("source temporarily unavailable")


def source(**overrides):
    values = {
        "id": "source-a",
        "owner_id": "owner-1",
        "company_name": "Acme",
        "careers_url": "https://jobs.example.com",
        "source_type": JobSourceType.GREENHOUSE,
        "source_identifier": "acme",
    }
    values.update(overrides)
    return CompanySource(**values)


def job(**overrides):
    values = {
        "id": discovered_job_id("owner-1", "source-a", "1"),
        "owner_id": "owner-1",
        "source_id": "source-a",
        "external_job_id": "1",
        "company": "Acme",
        "title": "Senior Python Engineer",
        "canonical_url": "https://jobs.example.com/1",
        "description": "Build services with Python and AWS.",
        "location": "Portland, OR",
        "locations": ("Portland, OR",),
        "workplace_type": WorkplaceType.HYBRID,
        "employment_type": "Full-time",
        "skills": ("Python", "AWS"),
        "source_type": JobSourceType.GREENHOUSE,
        "posted_at": "2026-07-29T00:00:00+00:00",
        "first_seen_at": "2026-07-30T17:00:00+00:00",
        "last_seen_at": "2026-07-30T17:00:00+00:00",
    }
    values.update(overrides)
    if "external_job_id" in overrides and "id" not in overrides:
        values["id"] = discovered_job_id(values["owner_id"], values["source_id"], values["external_job_id"])
    return DiscoveredJob(**values)


class JobDiscoveryServiceTests(unittest.TestCase):
    def test_collects_deduplicates_ranks_and_persists_discovery_records(self) -> None:
        duplicate = job(
            external_job_id="2",
            canonical_url="https://jobs.example.com/1?utm_source=x",
            description="",
        )
        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([job(), duplicate])},
            store=store,
        )
        profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python", "SQL", "AWS"),
            preferred_locations=("Portland",),
        )

        result = service.discover([source()], candidate_profile=profile)

        self.assertEqual(1, len(result.jobs))
        self.assertEqual(1, len(result.ranked_jobs))
        self.assertGreater(result.ranked_jobs[0].score, 50)
        self.assertEqual(
            1,
            len(store.list_discovered_jobs("owner-1", source_id="source-a")),
        )
        snapshot = result.ranked_jobs[0].fit_snapshot
        self.assertEqual(
            snapshot,
            store.get_fit_snapshot("owner-1", snapshot.job_id, snapshot.profile_fingerprint),
        )
        self.assertEqual("2026-07-30T18:00:00+00:00", result.jobs[0].last_seen_at)
        self.assertEqual("2026-07-30T18:00:00+00:00", result.jobs[0].first_seen_at)

    def test_missing_posting_becomes_inactive_without_becoming_an_application(self) -> None:
        responses = iter([[job()], []])

        class ChangingAdapter:
            def fetch_jobs(self, configured_source):
                return next(responses)

        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: ChangingAdapter()},
            store=store,
        )

        service.discover([source()])
        service.discover([source()])

        self.assertEqual([], store.list_discovered_jobs("owner-1", active_only=True))
        historical = store.list_discovered_jobs("owner-1", active_only=False)
        self.assertEqual(1, len(historical))
        self.assertFalse(historical[0].active)
        self.assertFalse(hasattr(store, "create_application"))
        self.assertFalse(hasattr(store, "create"))

    def test_source_failures_are_isolated(self) -> None:
        service = JobDiscoveryService(adapters={JobSourceType.LEVER: FailingAdapter()})
        configured = source(
            id="bad",
            company_name="Bad Co",
            source_type=JobSourceType.LEVER,
            source_identifier="bad",
        )

        result = service.discover([configured])

        self.assertEqual((), result.jobs)
        self.assertEqual(1, len(result.errors))
        self.assertIn("temporarily unavailable", result.errors[0].message)

    def test_public_coverage_wording_is_bounded(self) -> None:
        self.assertIn("publicly accessible", PUBLIC_COVERAGE_DESCRIPTION)
        self.assertIn("cannot be guaranteed", PUBLIC_COVERAGE_DESCRIPTION)
        self.assertNotIn("every job", PUBLIC_COVERAGE_DESCRIPTION.casefold())

    def test_json_file_store_survives_second_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            first = JsonFileDiscoveryStore(path, clock=lambda: "2026-07-30T18:00:00+00:00")
            first.put_company_source(source())
            first.sync_discovered_jobs(source(), [job()])

            second = JsonFileDiscoveryStore(path)

            loaded = second.list_discovered_jobs("owner-1", source_id="source-a")
            self.assertEqual(1, len(loaded))
            self.assertEqual("Senior Python Engineer", loaded[0].title)
            self.assertEqual("Acme", second.get_company_source("owner-1", "source-a").company_name)

    def test_cross_source_signature_deduplication_keeps_richer_record(self) -> None:
        sparse = job(
            id=discovered_job_id("owner-1", "lever", "l1"),
            source_id="lever",
            source_type=JobSourceType.LEVER,
            external_job_id="l1",
            description="",
        )
        rich = job(
            id=discovered_job_id("owner-1", "greenhouse", "g1"),
            source_id="greenhouse",
            external_job_id="g1",
        )

        result = deduplicate_jobs([sparse, rich])

        self.assertEqual(1, len(result))
        self.assertEqual("greenhouse", result[0].source_id)


if __name__ == "__main__":
    unittest.main()
