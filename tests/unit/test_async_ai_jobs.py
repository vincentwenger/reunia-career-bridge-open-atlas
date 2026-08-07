from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"
if str(RESUME_TAYLOR_ROOT) not in sys.path:
    sys.path.insert(0, str(RESUME_TAYLOR_ROOT))


from career_bridge.async_jobs import (
    AsyncJob,
    AsyncJobStatus,
    AsyncJobType,
    AsyncWorkerHeartbeat,
    DynamoDBAsyncJobStore,
    InMemoryAsyncJobStore,
    async_worker_health_payload,
)
from job_discovery.background_worker import (
    AsyncAIWorker,
    AsyncWorkerHeartbeatLoop,
    candidate_profile_from_payload,
    candidate_profile_payload,
)
from job_discovery.models import CompanySource, DiscoveredJob, JobSourceType
from job_discovery.ranking import CandidateJobProfile
from job_discovery.storage import InMemoryDiscoveryStore


@dataclass
class _FakeResult:
    ranked_jobs: tuple[object, ...]
    analysis_errors: tuple[object, ...] = ()


class _FakeAssessmentService:
    def __init__(self, _store) -> None:
        self.calls: list[str] = []

    def assess_existing_jobs(self, jobs, _profile):
        self.calls.extend(job.id for job in jobs)
        return _FakeResult(ranked_jobs=tuple(object() for _ in jobs))




class _HeartbeatTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict] = {}

    def put_item(self, *, Item, **_kwargs):
        self.items[(Item["owner_id"], Item["storage_key"])] = dict(Item)
        return {}

    def get_item(self, *, Key, **_kwargs):
        item = self.items.get((Key["owner_id"], Key["storage_key"]))
        return {"Item": dict(item)} if item else {}


class _FakeDocumentStore:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get(self, _object_key: str) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _FakeApplicationStore:
    def __init__(self) -> None:
        self.saved: dict | None = None

    def save_interview_preparation(self, owner_id, application_id, **kwargs):
        self.saved = {"owner_id": owner_id, "application_id": application_id, **kwargs}
        return self.saved


class _FakePreparation:
    def model_dump_json(self) -> str:
        return '{"role_summary":"saved"}'


class _FakeResumeAI:
    def __init__(self, _model_name: str, *, reasoning_effort=None) -> None:
        self.reasoning_effort = reasoning_effort

    def create_interview_preparation(self, **_kwargs):
        return _FakePreparation()


class AsyncAIJobTests(unittest.TestCase):
    def test_worker_heartbeat_is_persisted_and_reports_freshness(self) -> None:
        store = InMemoryAsyncJobStore()
        now = datetime(2026, 8, 4, 19, 0, tzinfo=timezone.utc)
        heartbeat = AsyncWorkerHeartbeat(
            worker_id="worker-1",
            started_at=(now - timedelta(minutes=5)).isoformat(),
            last_heartbeat_at=(now - timedelta(seconds=12)).isoformat(),
            state="idle",
            processed_jobs=4,
        )
        store.record_worker_heartbeat(heartbeat)

        stored = store.get_worker_heartbeat()
        self.assertEqual(heartbeat, stored)
        health = async_worker_health_payload(
            stored, max_age_seconds=90, now=now
        )
        self.assertEqual("healthy", health["status"])
        self.assertEqual(12, health["age_seconds"])
        self.assertEqual(4, health["processed_jobs"])

    def test_dynamodb_worker_heartbeat_uses_single_reserved_record(self) -> None:
        table = _HeartbeatTable()
        store = DynamoDBAsyncJobStore(
            {"CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME": "careerbridge_job_discovery"},
            table=table,
        )
        heartbeat = AsyncWorkerHeartbeat(
            worker_id="worker-1",
            started_at="2026-08-04T18:00:00+00:00",
            last_heartbeat_at="2026-08-04T19:00:00+00:00",
            state="working",
            current_job_id="job-1",
            current_job_type="resume_report",
        )
        store.record_worker_heartbeat(heartbeat)

        self.assertEqual(heartbeat, store.get_worker_heartbeat())
        self.assertEqual(1, len(table.items))
        item = next(iter(table.items.values()))
        self.assertEqual("async_worker_heartbeat", item["entity_type"])
        self.assertGreater(item["expires_at"], 0)

    def test_heartbeat_loop_publishes_while_long_work_is_running(self) -> None:
        worker = MagicMock()
        worker.worker_id = "worker-1"
        loop = AsyncWorkerHeartbeatLoop(worker, interval_seconds=5)
        loop._stop.wait = MagicMock(side_effect=[False, True])

        loop._run()

        worker.heartbeat.assert_called_once_with()

    def test_worker_heartbeat_reports_missing_stale_and_stopping(self) -> None:
        now = datetime(2026, 8, 4, 19, 0, tzinfo=timezone.utc)
        self.assertEqual(
            "missing",
            async_worker_health_payload(None, max_age_seconds=90, now=now)["status"],
        )
        stale = AsyncWorkerHeartbeat(
            worker_id="worker-1",
            started_at=(now - timedelta(hours=1)).isoformat(),
            last_heartbeat_at=(now - timedelta(seconds=91)).isoformat(),
        )
        self.assertEqual(
            "stale",
            async_worker_health_payload(stale, max_age_seconds=90, now=now)["status"],
        )
        stopping = AsyncWorkerHeartbeat(
            worker_id="worker-1",
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat_at=now.isoformat(),
            state="stopping",
        )
        self.assertEqual(
            "stopping",
            async_worker_health_payload(stopping, max_age_seconds=90, now=now)["status"],
        )

    def test_candidate_profile_round_trip_preserves_fingerprint(self) -> None:
        original = CandidateJobProfile(
            target_titles=("Data Engineer",),
            verified_skills=("SQL",),
            evidence_statements=("Built durable data pipelines.",),
            preferred_locations=("Portland, OR",),
        )
        restored = candidate_profile_from_payload(candidate_profile_payload(original))
        self.assertEqual(original.fingerprint, restored.fingerprint)
        self.assertEqual(original.target_titles, restored.target_titles)

    def test_large_payloads_must_be_externalized_to_object_storage(self) -> None:
        with self.assertRaisesRegex(ValueError, "store large inputs in object storage"):
            AsyncJob.queued(
                owner_id="owner-1",
                job_type=AsyncJobType.INTERVIEW_PREPARATION,
                payload={"large": "x" * (321 * 1024)},
                total_count=1,
            )

    def test_queue_claim_and_cancel_are_persisted(self) -> None:
        store = InMemoryAsyncJobStore()
        queued = store.create(
            AsyncJob.queued(
                owner_id="owner-1",
                job_type=AsyncJobType.JOB_DISCOVERY_ASSESSMENT,
                payload={"jobs": []},
                total_count=0,
            )
        )
        self.assertEqual(AsyncJobStatus.QUEUED, queued.status)
        claimed = store.claim_next("worker-1")
        self.assertIsNotNone(claimed)
        self.assertEqual(AsyncJobStatus.RUNNING, claimed.status)
        canceled = store.request_cancel("owner-1", queued.id)
        self.assertTrue(canceled.cancel_requested)
        self.assertEqual(AsyncJobStatus.RUNNING, canceled.status)

    def test_worker_processes_each_posting_and_saves_terminal_progress(self) -> None:
        discovery = InMemoryDiscoveryStore()
        source = CompanySource(
            id="source-1",
            owner_id="owner-1",
            company_name="Example",
            careers_url="https://example.com/careers",
            source_type=JobSourceType.GENERIC_JSONLD,
            source_identifier="",
        )
        discovery.put_company_source(source)
        jobs = [
            DiscoveredJob(
                id=f"job-{index}",
                owner_id="owner-1",
                source_id=source.id,
                external_job_id=f"external-{index}",
                company="Example",
                title=f"Data Engineer {index}",
                description="SQL data pipelines",
                canonical_url=f"https://example.com/jobs/{index}",
            )
            for index in range(2)
        ]
        discovery.sync_discovered_jobs(source, jobs)
        profile = CandidateJobProfile(
            target_titles=("Data Engineer",),
            evidence_statements=("Built SQL data pipelines.",),
        )
        async_store = InMemoryAsyncJobStore()
        created = async_store.create(
            AsyncJob.queued(
                owner_id="owner-1",
                job_type=AsyncJobType.JOB_DISCOVERY_ASSESSMENT,
                payload={
                    "candidate_profile": candidate_profile_payload(profile),
                    "jobs": [
                        {"source_id": source.id, "job_id": job.id, "label": job.title}
                        for job in jobs
                    ],
                },
                total_count=2,
            )
        )
        worker = AsyncAIWorker(
            async_store,
            discovery,
            service_factory=lambda store: _FakeAssessmentService(store),
        )
        result = worker.run_once()
        self.assertIsNotNone(result)
        self.assertEqual(created.id, result.id)
        self.assertEqual(AsyncJobStatus.COMPLETED, result.status)
        self.assertEqual(2, result.attempted_count)
        self.assertEqual(2, result.completed_count)
        self.assertTrue(result.status.terminal)
        heartbeat = async_store.get_worker_heartbeat()
        self.assertIsNotNone(heartbeat)
        self.assertEqual("idle", heartbeat.state)
        self.assertEqual(1, heartbeat.processed_jobs)

    def test_queued_cancellation_prevents_worker_claim(self) -> None:
        store = InMemoryAsyncJobStore()
        job = store.create(
            AsyncJob.queued(
                owner_id="owner-1",
                job_type=AsyncJobType.JOB_DISCOVERY_ASSESSMENT,
                payload={"jobs": []},
                total_count=0,
            )
        )
        canceled = store.request_cancel(job.owner_id, job.id)
        self.assertEqual(AsyncJobStatus.CANCELED, canceled.status)
        self.assertIsNone(store.claim_next("worker-1"))

    def test_interview_worker_reads_s3_snapshot_and_saves_application_result(self) -> None:
        snapshot = {
            "application_id": "application-1",
            "company": "Example",
            "role": "Data Engineer",
            "interview_audience": "Hiring manager",
            "job_description": "Build data platforms.",
            "job_description_fingerprint": "job-fingerprint",
            "career_profile_context": {"target_role": "Data Engineer"},
            "evidence_items": [
                {"id": "evidence-1", "text": "Built data pipelines.", "source": "verified profile"}
            ],
            "evidence_fingerprint": "evidence-fingerprint",
            "evidence_source_label": "Verified evidence",
            "evidence_snapshot_json": '{"evidence-1":"Built data pipelines."}',
            "resume_findings_fingerprint": "findings-fingerprint",
            "resume_findings_json": json.dumps(
                {
                    "captured_at": "2026-08-03T00:00:00+00:00",
                    "source_stage": "test",
                }
            ),
            "model_name": "gpt-test",
            "reasoning_effort": "minimal",
        }
        async_store = InMemoryAsyncJobStore()
        queued = async_store.create(
            AsyncJob.queued(
                owner_id="owner-1",
                job_type=AsyncJobType.INTERVIEW_PREPARATION,
                payload={
                    "application_id": "application-1",
                    "snapshot_key": "async-jobs/input.json",
                },
                total_count=1,
            )
        )
        applications = _FakeApplicationStore()
        worker = AsyncAIWorker(
            async_store,
            InMemoryDiscoveryStore(),
            application_store=applications,
            document_store=_FakeDocumentStore(snapshot),
            interview_ai_factory=_FakeResumeAI,
            interview_restrictor=lambda preparation, *_args, **_kwargs: preparation,
        )
        result = worker.run_once()

        self.assertEqual(queued.id, result.id)
        self.assertEqual(AsyncJobStatus.COMPLETED, result.status)
        self.assertEqual(1, result.completed_count)
        self.assertEqual("application-1", applications.saved["application_id"])
        self.assertEqual('{"role_summary":"saved"}', applications.saved["content_json"])


if __name__ == "__main__":
    unittest.main()
