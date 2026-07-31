"""Contract and behavior tests for the DynamoDB ApplicationStore adapter."""

from __future__ import annotations

import ast
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"
DYNAMODB_STORAGE = RESUME_TAYLOR_ROOT / "resume_tailor" / "dynamodb_storage.py"
APPLICATION_TRACKER = RESUME_TAYLOR_ROOT / "resume_tailor" / "application_tracker.py"


class FakeDynamoTable:
    """Small in-memory stand-in for the boto3 DynamoDB Table resource."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _key(value: dict[str, Any]) -> tuple[str, str]:
        return str(value["owner_id"]), str(value["storage_key"])

    def put_item(self, *, Item: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        key = self._key(Item)
        if kwargs.get("ConditionExpression") and key in self.items:
            raise AssertionError("Conditional create attempted to overwrite an item")
        self.items[key] = deepcopy(Item)
        return {}

    def get_item(self, *, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        item = self.items.get(self._key(Key))
        return {"Item": deepcopy(item)} if item is not None else {}

    def delete_item(self, *, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        item = self.items.pop(self._key(Key), None)
        if kwargs.get("ReturnValues") == "ALL_OLD" and item is not None:
            return {"Attributes": deepcopy(item)}
        return {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        values = kwargs["ExpressionAttributeValues"]
        owner_id = str(values[":owner_id"])
        prefix = str(values[":prefix"])
        items = [
            deepcopy(item)
            for (stored_owner, storage_key), item in self.items.items()
            if stored_owner == owner_id and storage_key.startswith(prefix)
        ]
        items.sort(key=lambda item: str(item["storage_key"]))
        return {"Items": items}


class FakeObjectStore:
    """In-memory stand-in for private S3 object storage."""

    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> None:
        del content_type, metadata
        self.items[object_key] = bytes(content)

    def get(self, object_key: str) -> bytes:
        return self.items[object_key]

    def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)
        self.items.pop(object_key, None)


class DynamoDBApplicationStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(RESUME_TAYLOR_ROOT))
        from resume_tailor.dynamodb_storage import DynamoDBApplicationStore
        from resume_tailor.storage import ApplicationStore

        cls.store_class = DynamoDBApplicationStore
        cls.protocol = ApplicationStore

    @classmethod
    def tearDownClass(cls) -> None:
        if sys.path and sys.path[0] == str(RESUME_TAYLOR_ROOT):
            sys.path.pop(0)

    def setUp(self) -> None:
        self.table = FakeDynamoTable()
        self.documents = FakeObjectStore()
        self.timestamps = iter(
            [
                "2026-07-30T14:00:00+00:00",
                "2026-07-30T14:01:00+00:00",
                "2026-07-30T14:02:00+00:00",
                "2026-07-30T14:03:00+00:00",
                "2026-07-30T14:04:00+00:00",
                "2026-07-30T14:05:00+00:00",
                "2026-07-30T14:06:00+00:00",
                "2026-07-30T14:07:00+00:00",
                "2026-07-30T14:08:00+00:00",
                "2026-07-30T14:09:00+00:00",
            ]
        )
        ids = iter(["app-one", "app-two", "app-three"])
        self.store = self.store_class(
            {
                "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "career-bridge-applications",
                "AWS_REGION": "us-west-2",
            },
            table=self.table,
            document_store=self.documents,
            id_factory=lambda: next(ids),
            clock=lambda: next(self.timestamps),
        )

    def test_public_api_matches_sqlite_and_protocol(self) -> None:
        def public_methods(path: Path, class_name: str) -> set[str]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            cls = next(
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            return {
                node.name
                for node in cls.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
            }

        dynamo_methods = public_methods(DYNAMODB_STORAGE, "DynamoDBApplicationStore")
        sqlite_methods = public_methods(APPLICATION_TRACKER, "SQLiteApplicationStore")
        self.assertEqual(dynamo_methods, sqlite_methods)
        self.assertIsInstance(self.store, self.protocol)

    def test_crud_builder_progress_snapshot_lookup_and_owner_isolation(self) -> None:
        first = self.store.create(
            "owner-1",
            company=" Example Bank ",
            role=" Senior Engineer ",
            job_url="https://example.com/jobs/1",
            status="interview",
            resume_fingerprint="fingerprint-1",
        )
        second = self.store.create(
            "owner-1",
            company="Another Company",
            role="Auditor",
            status="draft",
        )
        self.store.create(
            "owner-2",
            company="Private Company",
            role="Private Role",
            status="offered",
        )

        self.assertEqual(first.status, "interviewing")
        self.assertTrue(first.screening_received)
        self.assertTrue(first.interview_received)
        self.assertEqual(self.store.get("owner-1", first.id), first)
        self.assertIsNone(self.store.get("owner-2", first.id))
        self.assertEqual(
            [item.id for item in self.store.list_for_owner("owner-1")],
            [first.id, second.id],
        )
        self.assertEqual(
            self.store.find_snapshot(
                "owner-1",
                resume_fingerprint="fingerprint-1",
                company="example bank",
                role="SENIOR ENGINEER",
            ).id,
            first.id,
        )

        updated = self.store.update(
            "owner-1",
            first.id,
            company="Example Bank USA",
            role="Lead Engineer",
            job_url="not-a-url",
            application_date="2026-07-15",
            status="offered",
            screening_received=False,
            interview_received=False,
            offer_received=False,
            notes="Follow up with recruiter",
            next_follow_up_date="2026-08-01",
            interview_readiness=88.26,
            next_action="Review offer",
            upcoming_event_date="2026-08-02",
            upcoming_event_type="interview",
            job_description="Updated description",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.company, "Example Bank USA")
        self.assertEqual(updated.job_url, "")
        self.assertEqual(updated.interview_readiness, 88.3)
        self.assertTrue(updated.screening_received)
        self.assertTrue(updated.interview_received)
        self.assertTrue(updated.offer_received)

        progressed = self.store.update_builder_progress(
            "owner-1",
            first.id,
            workflow_step="quality",
            resume_version="Tailored Resume",
            status="preparing",
            original_resume_key="career-bridge/users/hash/original-resume/resume.pdf",
        )
        self.assertEqual(progressed.workflow_step, "quality")
        self.assertEqual(
            progressed.original_resume_key,
            "career-bridge/users/hash/original-resume/resume.pdf",
        )
        self.assertEqual(progressed.resume_version, "Tailored Resume")

        attached = self.store.attach_resume_snapshot(
            "owner-1",
            second.id,
            resume_version="Final Resume",
            resume_style="bank",
            alignment_score=91.24,
            overall_score=87.76,
            resume_filename="resume.docx",
            resume_bytes=b"docx-bytes",
            resume_fingerprint="resume-final",
            resume_pdf_filename="resume.pdf",
            resume_pdf_bytes=b"pdf-bytes",
        )
        self.assertEqual(attached.workflow_step, "evidence_export")
        self.assertEqual(attached.status, "ready_to_apply")
        self.assertEqual(attached.resume_bytes, b"docx-bytes")
        self.assertTrue(attached.resume_docx_key)
        self.assertTrue(attached.resume_pdf_key)
        self.assertEqual(self.documents.get(attached.resume_docx_key), b"docx-bytes")
        self.assertEqual(self.documents.get(attached.resume_pdf_key), b"pdf-bytes")
        stored_application = self.table.items[("owner-1", f"APP#{second.id}")]
        self.assertNotIn("resume_bytes", stored_application)
        self.assertEqual(stored_application["resume_docx_key"], attached.resume_docx_key)
        self.assertEqual(
            self.store.get("owner-1", second.id).resume_bytes, b"docx-bytes"
        )
        self.assertIsNone(
            self.store.get(
                "owner-1", second.id, include_resume_bytes=False
            ).resume_bytes
        )
        self.assertEqual(attached.alignment_score, 91.2)

        self.assertIsNone(
            self.store.update_builder_progress(
                "owner-1", "missing", workflow_step="review"
            )
        )
        self.assertFalse(self.store.delete("owner-1", "missing"))

    def test_linked_artifacts_round_trip_and_delete_cascade(self) -> None:
        application = self.store.create(
            "owner-1",
            company="Example",
            role="Engineer",
        )
        findings = self.store.save_resume_findings(
            "owner-1",
            application.id,
            snapshot_json='{"unsupported": 2}',
            fingerprint=" findings-v1 ",
        )
        self.assertEqual(findings.fingerprint, "findings-v1")
        self.assertEqual(findings.payload(), {"unsupported": 2})

        preparation = self.store.save_interview_preparation(
            "owner-1",
            application.id,
            content_json='{"questions": ["Why us?"]}',
            job_description_fingerprint="job-v1",
            evidence_fingerprint="evidence-v1",
            evidence_source_label=" Final resume ",
            evidence_snapshot_json='{"ids": ["e1"]}',
            resume_findings_fingerprint="findings-v1",
            resume_findings_snapshot_json='{"unsupported": 2}',
            model_name="gpt-test",
        )
        self.assertEqual(preparation.evidence_source_label, "Final resume")
        self.assertEqual(preparation.payload()["questions"], ["Why us?"])

        impact = self.store.save_impact_snapshot(
            "owner-1",
            application.id,
            {
                "credentials_identified": 3,
                "terminology_clarified": 4,
                "unsupported_claims_prevented": 2,
                "relevant_experience_recovered": 5,
                "baseline_alignment_score": 61.24,
                "current_alignment_score": 82.76,
                "alignment_improvement": 21.52,
                "verified_resume_ready": True,
                "explanation": "Evidence grounded",
            },
        )
        self.assertEqual(impact["baseline_alignment_score"], 61.2)
        self.assertEqual(impact["current_alignment_score"], 82.8)
        self.assertEqual(impact["alignment_improvement"], 21.5)
        self.assertEqual(impact["details"]["explanation"], "Evidence grounded")
        self.assertEqual(
            self.store.list_impact_snapshots("owner-1")[0]["application_id"],
            application.id,
        )
        findings_item = self.table.items[(
            "owner-1", f"RESUME_FINDINGS#{application.id}"
        )]
        preparation_item = self.table.items[(
            "owner-1", f"INTERVIEW_PREPARATION#{application.id}"
        )]
        impact_item = self.table.items[("owner-1", f"IMPACT#{application.id}")]
        self.assertNotIn("snapshot_json", findings_item)
        self.assertIn("snapshot_json_key", findings_item)
        self.assertNotIn("content_json", preparation_item)
        self.assertNotIn("evidence_snapshot_json", preparation_item)
        self.assertNotIn("resume_findings_snapshot_json", preparation_item)
        self.assertNotIn("details_json", impact_item)
        for stored_item in self.table.items.values():
            self.assertNotIn("expires_at", stored_item)
        referenced_objects = set(self.documents.items)

        self.assertTrue(self.store.delete("owner-1", application.id))
        self.assertTrue(referenced_objects.issubset(set(self.documents.deleted)))
        self.assertIsNone(self.store.get("owner-1", application.id))
        self.assertIsNone(self.store.get_resume_findings("owner-1", application.id))
        self.assertIsNone(
            self.store.get_interview_preparation("owner-1", application.id)
        )
        self.assertIsNone(self.store.get_impact_snapshot("owner-1", application.id))

    def test_source_job_link_supports_duplicate_safe_lookup_and_cleanup(self) -> None:
        created = self.store.create(
            "owner-1",
            company="Example Bank",
            role="Engineer",
            source_job_id="discovered-job-1",
        )
        self.assertEqual(
            created.id,
            self.store.find_by_source_job("owner-1", "discovered-job-1").id,
        )
        self.assertIn(
            ("owner-1", "SOURCE_JOB#discovered-job-1"),
            self.table.items,
        )
        self.assertIsNone(
            self.store.find_by_source_job("owner-2", "discovered-job-1")
        )

        self.assertTrue(self.store.delete("owner-1", created.id))
        self.assertNotIn(
            ("owner-1", "SOURCE_JOB#discovered-job-1"),
            self.table.items,
        )

    def test_large_documents_and_reports_do_not_approach_dynamodb_item_limit(self) -> None:
        large_resume = b"R" * (450 * 1024)
        large_report = "{" + '"report":"' + ("x" * (450 * 1024)) + '"}'
        application = self.store.create(
            "owner-large",
            company="Large Artifact Co",
            role="Engineer",
            resume_filename="resume.docx",
            resume_bytes=large_resume,
            resume_fingerprint="large-resume",
        )
        self.store.save_resume_findings(
            "owner-large",
            application.id,
            snapshot_json=large_report,
            fingerprint="large-report",
        )

        application_item = self.table.items[(
            "owner-large", f"APP#{application.id}"
        )]
        findings_item = self.table.items[(
            "owner-large", f"RESUME_FINDINGS#{application.id}"
        )]
        self.assertNotIn("resume_bytes", application_item)
        self.assertNotIn("snapshot_json", findings_item)
        self.assertLess(len(str(application_item).encode("utf-8")), 20 * 1024)
        self.assertLess(len(str(findings_item).encode("utf-8")), 20 * 1024)
        self.assertEqual(self.store.get("owner-large", application.id).resume_bytes, large_resume)
        self.assertEqual(
            self.store.get_resume_findings("owner-large", application.id).snapshot_json,
            large_report,
        )

    def test_linked_artifacts_require_an_existing_application(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.store.save_resume_findings(
                "owner-1",
                "missing",
                snapshot_json="{}",
                fingerprint="none",
            )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.store.save_interview_preparation(
                "owner-1",
                "missing",
                content_json="{}",
                job_description_fingerprint="",
                evidence_fingerprint="",
                evidence_source_label="",
                evidence_snapshot_json="{}",
                resume_findings_fingerprint="",
                resume_findings_snapshot_json="{}",
                model_name="",
            )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.store.save_impact_snapshot("owner-1", "missing", {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
