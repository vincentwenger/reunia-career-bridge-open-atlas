from __future__ import annotations

import unittest

from job_discovery.application_conversion import DiscoveredJobApplicationService
from job_discovery.models import (
    CompanySource,
    DiscoveredJob,
    DiscoveryJobDisposition,
    JobFitSnapshot,
    JobSourceType,
    WorkplaceType,
    discovered_job_id,
)
from job_discovery.storage import InMemoryDiscoveryStore
from products.resume_taylor.resume_tailor.application_tracker import SQLiteApplicationStore


def _source(owner_id: str = "owner-1") -> CompanySource:
    return CompanySource(
        id="source-1",
        owner_id=owner_id,
        company_name="Example Bank",
        careers_url="https://jobs.example.test",
        source_type=JobSourceType.GREENHOUSE,
        source_identifier="example-bank",
    )


def _job(owner_id: str = "owner-1") -> DiscoveredJob:
    source_id = "source-1"
    external_id = "external-123"
    return DiscoveredJob(
        id=discovered_job_id(owner_id, source_id, external_id),
        owner_id=owner_id,
        source_id=source_id,
        external_job_id=external_id,
        company="Example Bank",
        title="Senior Data Engineer",
        location="Portland, OR",
        workplace_type=WorkplaceType.HYBRID,
        employment_type="Full-time",
        salary_text="$150,000-$180,000",
        description="Build regulated data platforms with Python, SQL, and AWS.",
        canonical_url="https://jobs.example.test/external-123",
        first_seen_at="2026-07-30T18:00:00+00:00",
        last_seen_at="2026-07-30T18:00:00+00:00",
        source_type=JobSourceType.GREENHOUSE,
    )


class DiscoveredJobApplicationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.discovery_store = InMemoryDiscoveryStore(
            clock=lambda: "2026-07-30T18:00:00+00:00"
        )
        self.application_store = SQLiteApplicationStore(":memory:")
        self.source = _source()
        self.job = self.discovery_store.sync_discovered_jobs(
            self.source,
            [_job()],
            checked_at="2026-07-30T18:00:00+00:00",
        )[0]
        self.fit = JobFitSnapshot(
            job_id=self.job.id,
            owner_id=self.job.owner_id,
            profile_fingerprint="profile-1",
            description_fingerprint=self.job.description_fingerprint,
            fit_score=82,
            recommendation="Strong match",
            confidence="high",
            supported_requirements=("Python", "SQL"),
            partial_requirements=("AWS",),
            unsupported_requirements=("Kafka",),
            analyzed_at="2026-07-30T18:01:00+00:00",
        )
        self.discovery_store.put_fit_snapshot(self.fit)
        self.service = DiscoveredJobApplicationService(
            self.discovery_store,
            self.application_store,
        )

    def tearDown(self) -> None:
        self.application_store._connection.close()

    def test_save_and_ignore_remain_discovery_specific(self) -> None:
        saved = self.service.save("owner-1", self.source.id, self.job.id)
        self.assertEqual(saved.disposition, DiscoveryJobDisposition.SAVED)
        self.assertEqual([], self.application_store.list_for_owner("owner-1"))

        ignored = self.service.ignore("owner-1", self.source.id, self.job.id)
        self.assertEqual(ignored.disposition, DiscoveryJobDisposition.IGNORED)
        self.assertEqual(
            ignored,
            self.discovery_store.get_job_state(
                "owner-1", self.source.id, self.job.id
            ),
        )
        self.assertEqual([], self.application_store.list_for_owner("owner-1"))

    def test_create_workspace_maps_posting_and_fit_to_application(self) -> None:
        result = self.service.create_application_workspace(
            "owner-1", self.source.id, self.job.id
        )

        self.assertTrue(result.created)
        application = result.application
        self.assertEqual(application.company, self.job.company)
        self.assertEqual(application.role, self.job.title)
        self.assertEqual(application.job_url, self.job.canonical_url)
        self.assertEqual(application.job_description, self.job.description)
        self.assertEqual(application.alignment_score, 82.0)
        self.assertEqual(application.status, "considering")
        self.assertEqual(application.workflow_step, "setup")
        self.assertEqual(application.source_job_id, self.job.id)
        self.assertEqual(
            application.id,
            self.application_store.find_by_source_job("owner-1", self.job.id).id,
        )
        state = self.discovery_store.get_job_state(
            "owner-1", self.source.id, self.job.id
        )
        self.assertEqual(
            state.disposition, DiscoveryJobDisposition.APPLICATION_CREATED
        )
        self.assertEqual(state.application_id, application.id)

    def test_conversion_is_idempotent_and_does_not_duplicate_applications(self) -> None:
        first = self.service.create_application_workspace(
            "owner-1", self.source.id, self.job.id
        )
        second = self.service.create_application_workspace(
            "owner-1", self.source.id, self.job.id
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.application.id, second.application.id)
        self.assertEqual(1, len(self.application_store.list_for_owner("owner-1")))

    def test_owner_scoping_prevents_cross_user_promotion(self) -> None:
        with self.assertRaises(LookupError):
            self.service.create_application_workspace(
                "owner-2", self.source.id, self.job.id
            )
        self.assertEqual([], self.application_store.list_for_owner("owner-2"))


if __name__ == "__main__":
    unittest.main()
