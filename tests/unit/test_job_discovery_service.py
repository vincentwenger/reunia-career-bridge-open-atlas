from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from job_discovery.deduplication import deduplicate_jobs
from job_discovery.models import CompanySource, DiscoveredJob, JobSourceType, WorkplaceType
from job_discovery.ranking import CandidateJobProfile
from job_discovery.service import JobDiscoveryService, PUBLIC_COVERAGE_DESCRIPTION
from job_discovery.storage import InMemoryJobStore, JsonFileJobStore


class StaticAdapter:
    def __init__(self, jobs):
        self.jobs = jobs

    def fetch_jobs(self, source):
        return list(self.jobs)


class FailingAdapter:
    def fetch_jobs(self, source):
        raise RuntimeError("source temporarily unavailable")


def job(**overrides):
    values = {
        "source_id": "source-a",
        "source_type": JobSourceType.GREENHOUSE,
        "external_id": "1",
        "company": "Acme",
        "title": "Senior Python Engineer",
        "job_url": "https://jobs.example.com/1",
        "description": "Build services with Python and AWS.",
        "location": "Portland, OR",
        "locations": ("Portland, OR",),
        "workplace_type": WorkplaceType.HYBRID,
        "employment_type": "Full-time",
        "skills": ("Python", "AWS"),
        "posted_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return DiscoveredJob(**values)


class JobDiscoveryServiceTests(unittest.TestCase):
    def test_collects_deduplicates_ranks_and_persists(self) -> None:
        duplicate = job(external_id="2", job_url="https://jobs.example.com/1?utm_source=x", description="")
        store = InMemoryJobStore()
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([job(), duplicate])},
            store=store,
        )
        source = CompanySource("source-a", "Acme", JobSourceType.GREENHOUSE, identifier="acme")
        profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python", "SQL", "AWS"),
            preferred_locations=("Portland",),
        )

        result = service.discover([source], candidate_profile=profile)

        self.assertEqual(1, len(result.jobs))
        self.assertEqual(1, len(result.ranked_jobs))
        self.assertGreater(result.ranked_jobs[0].score, 50)
        self.assertEqual(1, len(store.list_jobs(source_id="source-a")))

    def test_source_failures_are_isolated(self) -> None:
        service = JobDiscoveryService(adapters={JobSourceType.LEVER: FailingAdapter()})
        source = CompanySource("bad", "Bad Co", JobSourceType.LEVER, identifier="bad")

        result = service.discover([source])

        self.assertEqual((), result.jobs)
        self.assertEqual(1, len(result.errors))
        self.assertIn("temporarily unavailable", result.errors[0].message)

    def test_public_coverage_wording_is_bounded(self) -> None:
        self.assertIn("publicly accessible", PUBLIC_COVERAGE_DESCRIPTION)
        self.assertIn("cannot be guaranteed", PUBLIC_COVERAGE_DESCRIPTION)
        self.assertNotIn("every job", PUBLIC_COVERAGE_DESCRIPTION.casefold())

    def test_json_file_store_survives_second_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            first = JsonFileJobStore(path)
            first.replace_for_source("source-a", [job()])

            second = JsonFileJobStore(path)

            loaded = second.list_jobs(source_id="source-a")
            self.assertEqual(1, len(loaded))
            self.assertEqual("Senior Python Engineer", loaded[0].title)

    def test_cross_source_signature_deduplication_keeps_richer_record(self) -> None:
        sparse = job(source_id="lever", source_type=JobSourceType.LEVER, external_id="l1", description="")
        rich = job(source_id="greenhouse", external_id="g1")

        result = deduplicate_jobs([sparse, rich])

        self.assertEqual(1, len(result))
        self.assertEqual("greenhouse", result[0].source_id)


if __name__ == "__main__":
    unittest.main()
