from __future__ import annotations

import unittest
from datetime import datetime, timezone

from job_discovery.models import (
    CompanySource,
    DiscoveryScanSchedule,
    DiscoveryScheduleCadence,
    JobSourceType,
)
from job_discovery.ranking import CandidateJobProfile
from job_discovery.scheduling import (
    ExternalJobDiscoveryRunner,
    next_scheduled_run,
    schedule_is_due,
)
from job_discovery.service import DiscoveryResult
from job_discovery.storage import InMemoryDiscoveryStore


def source(owner_id: str = "owner-1") -> CompanySource:
    return CompanySource(
        id="source-1",
        owner_id=owner_id,
        company_name="Example Bank",
        careers_url="https://boards.greenhouse.io/example",
        source_type=JobSourceType.GREENHOUSE,
        source_identifier="example",
    )


class RecordingService:
    def __init__(self) -> None:
        self.calls = []

    def discover(self, sources, *, candidate_profile=None):
        self.calls.append((tuple(sources), candidate_profile))
        return DiscoveryResult(jobs=())


class ExternalJobDiscoveryRunnerTests(unittest.TestCase):
    def test_runs_outside_web_process_for_explicit_owner(self) -> None:
        store = InMemoryDiscoveryStore()
        store.put_company_source(source())
        service = RecordingService()
        profile = CandidateJobProfile(verified_skills=("SQL",))
        runner = ExternalJobDiscoveryRunner(
            store,
            profile_provider=lambda owner_id: profile if owner_id == "owner-1" else None,
            service_factory=lambda _store: service,
        )

        summary = runner.run_owners(["owner-1", "owner-1"])

        self.assertTrue(summary.succeeded)
        self.assertEqual(1, len(summary.owners))
        self.assertEqual(1, summary.owners[0].enabled_sources)
        self.assertEqual(1, len(service.calls))
        self.assertIs(profile, service.calls[0][1])

    def test_requires_an_explicit_owner_registry(self) -> None:
        runner = ExternalJobDiscoveryRunner(
            InMemoryDiscoveryStore(),
            service_factory=lambda _store: RecordingService(),
        )
        with self.assertRaisesRegex(ValueError, "At least one owner_id"):
            runner.run_owners([])

    def test_daily_schedule_waits_until_saved_local_time_then_runs_once(self) -> None:
        schedule = DiscoveryScanSchedule(
            owner_id="owner-1",
            cadence=DiscoveryScheduleCadence.DAILY,
            local_hour=9,
            timezone_name="America/Los_Angeles",
            updated_at="2026-07-30T14:00:00+00:00",
        )
        before = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)  # 08:00 PDT
        after = datetime(2026, 7, 30, 17, 0, tzinfo=timezone.utc)   # 10:00 PDT
        self.assertFalse(schedule_is_due(schedule, now=before))
        self.assertEqual(
            datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc),
            next_scheduled_run(schedule, now=before),
        )
        self.assertTrue(schedule_is_due(schedule, now=after))
        completed = schedule.marked_run(after)
        self.assertFalse(schedule_is_due(completed, now=after))
        self.assertEqual(
            datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc),
            next_scheduled_run(completed, now=after),
        )

    def test_scheduled_runner_skips_manual_and_runs_due_owner(self) -> None:
        store = InMemoryDiscoveryStore()
        store.put_company_source(source())
        service = RecordingService()
        runner = ExternalJobDiscoveryRunner(
            store,
            service_factory=lambda _store: service,
        )
        store.put_scan_schedule(
            DiscoveryScanSchedule(
                owner_id="owner-1",
                cadence=DiscoveryScheduleCadence.MANUAL,
                updated_at="2026-07-30T12:00:00+00:00",
            )
        )
        skipped = runner.run_scheduled_owners(
            ["owner-1"], now="2026-07-30T18:00:00+00:00"
        )
        self.assertFalse(skipped.owners[0].scan_performed)
        self.assertEqual([], service.calls)

        store.put_scan_schedule(
            DiscoveryScanSchedule(
                owner_id="owner-1",
                cadence=DiscoveryScheduleCadence.DAILY,
                local_hour=9,
                timezone_name="America/Los_Angeles",
                updated_at="2026-07-30T12:00:00+00:00",
            )
        )
        performed = runner.run_scheduled_owners(
            ["owner-1"], now="2026-07-30T18:00:00+00:00"
        )
        self.assertTrue(performed.owners[0].scan_performed)
        self.assertEqual(1, len(service.calls))
        self.assertEqual(
            "2026-07-30T18:00:00+00:00",
            store.get_scan_schedule("owner-1").last_run_at,
        )


if __name__ == "__main__":
    unittest.main()
