"""Shared storage contracts and production-migration durability tests.

The behavioral suites exercise the DynamoDB application repository and both
workflow implementations. Additional tests exercise cross-instance durability, legacy inline-item
migration, S3 externalization, owner isolation, cascade cleanup, and optimistic
concurrency without contacting live AWS resources.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"
if str(RESUME_TAYLOR_ROOT) not in sys.path:
    sys.path.insert(0, str(RESUME_TAYLOR_ROOT))


class FakeApplicationTable:
    """Owner-partitioned in-memory replacement for a DynamoDB table."""

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


class FakeWorkflowTable:
    """Conditional-write DynamoDB fake shared by independent store instances."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.update_calls: list[dict[str, Any]] = []

    @staticmethod
    def _key(value: dict[str, Any]) -> str:
        return str(value["workflow_id"])

    def get_item(self, *, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        item = self.items.get(self._key(Key))
        return {"Item": deepcopy(item)} if item is not None else {}

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(deepcopy(kwargs))
        key = self._key(kwargs["Key"])
        current = self.items.get(key)
        values = kwargs["ExpressionAttributeValues"]
        condition = str(kwargs.get("ConditionExpression") or "")
        if "attribute_not_exists" in condition:
            conflict = current is not None
        else:
            expected = int(values[":expected_version"])
            conflict = current is None or int(current.get("version") or 0) != expected
        if conflict:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "stale workflow version",
                    }
                },
                "UpdateItem",
            )

        previous = deepcopy(current) if current is not None else None
        item = dict(current or {})
        item.update(kwargs["Key"])
        if ":entity_type" in values:
            item.update(
                {
                    "entity_type": values[":entity_type"],
                    "workflow_type": values[":workflow_type"],
                    "retention_policy": values[":retention_policy"],
                    "version": values[":new_version"],
                    "fingerprint": values[":fingerprint"],
                    "state_json_key": values[":state_json_key"],
                    "updated_at": values[":updated_at"],
                    "updated_by_request": values[":updated_by_request"],
                }
            )
            if ":expires_at" in values:
                item["expires_at"] = values[":expires_at"]
        else:
            item.update(
                {
                    "workflow_type": values[":workflow_type"],
                    "retention_policy": values[":retention_policy"],
                    "version": values[":new_version"],
                    "updated_at": values[":updated_at"],
                    "updated_by_request": values[":updated_by_request"],
                }
            )
        update_expression = str(kwargs.get("UpdateExpression") or "")
        if "#legacy_state_json" in update_expression:
            item.pop("state_json", None)
        if "#legacy_state" in update_expression:
            item.pop("state", None)
        if "#expires_at" in update_expression.split("REMOVE", 1)[-1]:
            item.pop("expires_at", None)
        self.items[key] = deepcopy(item)
        return {"Attributes": previous} if previous is not None else {}

    def delete_item(self, *, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        previous = self.items.pop(self._key(Key), None)
        if kwargs.get("ReturnValues") == "ALL_OLD" and previous is not None:
            return {"Attributes": deepcopy(previous)}
        return {}


class FakeObjectStore:
    """Private S3 stand-in that records object writes and deletions."""

    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.metadata: dict[str, dict[str, str]] = {}

    def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.items[object_key] = bytes(content)
        self.metadata[object_key] = {
            **dict(metadata or {}),
            "content-type": content_type,
        }

    def get(self, object_key: str) -> bytes:
        return self.items[object_key]

    def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)
        self.items.pop(object_key, None)


class IncrementingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 30, 17, 0, tzinfo=timezone.utc)

    def __call__(self) -> str:
        value = self.current.isoformat()
        self.current += timedelta(seconds=1)
        return value


class ApplicationStoreContractMixin:
    """One complete ApplicationStore behavior suite used by both adapters."""

    store: Any

    def test_complete_application_store_contract(self) -> None:
        first = self.store.create(
            "owner-a",
            company=" Example Bank ",
            role=" Senior Engineer ",
            status="interview",
            resume_fingerprint="source-fingerprint",
        )
        second = self.store.create(
            "owner-a",
            company="Second Company",
            role="Auditor",
        )
        private = self.store.create(
            "owner-b",
            company="Private Company",
            role="Private Role",
        )

        self.assertEqual(first.status, "interviewing")
        self.assertEqual(self.store.get("owner-a", first.id).id, first.id)
        self.assertIsNone(self.store.get("owner-b", first.id))
        self.assertIsNone(self.store.get("owner-a", private.id))
        self.assertEqual(
            {item.id for item in self.store.list_for_owner("owner-a")},
            {first.id, second.id},
        )
        self.assertEqual(
            self.store.find_snapshot(
                "owner-a",
                resume_fingerprint="source-fingerprint",
                company="example bank",
                role="SENIOR ENGINEER",
            ).id,
            first.id,
        )

        updated = self.store.update(
            "owner-a",
            first.id,
            company="Example Bank USA",
            role="Lead Engineer",
            job_url="https://example.com/jobs/lead",
            application_date="2026-07-30",
            status="offered",
            screening_received=False,
            interview_received=False,
            offer_received=False,
            notes="Offer received",
            next_follow_up_date="2026-08-02",
            interview_readiness=88.26,
            next_action="Review offer",
            upcoming_event_date="2026-08-03",
            upcoming_event_type="interview",
            job_description="Updated description",
        )
        self.assertEqual(updated.company, "Example Bank USA")
        self.assertTrue(updated.screening_received)
        self.assertTrue(updated.interview_received)
        self.assertTrue(updated.offer_received)
        self.assertEqual(updated.interview_readiness, 88.3)

        progressed = self.store.update_builder_progress(
            "owner-a",
            first.id,
            workflow_step="quality",
            resume_version="Tailored Resume",
            status="preparing",
            original_resume_key="career-bridge/users/hash/original/resume.docx",
        )
        self.assertEqual(progressed.workflow_step, "quality")
        self.assertEqual(progressed.resume_version, "Tailored Resume")
        self.assertTrue(progressed.original_resume_key)

        attached = self.store.attach_resume_snapshot(
            "owner-a",
            first.id,
            resume_version="Final Resume",
            resume_style="bank",
            alignment_score=91.24,
            overall_score=87.76,
            resume_filename="resume.docx",
            resume_bytes=b"final-docx",
            resume_fingerprint="final-fingerprint",
            resume_pdf_filename="resume.pdf",
            resume_pdf_bytes=b"final-pdf",
        )
        self.assertEqual(attached.resume_bytes, b"final-docx")
        self.assertEqual(
            self.store.get("owner-a", first.id).resume_bytes,
            b"final-docx",
        )
        self.assertIsNone(
            self.store.get(
                "owner-a", first.id, include_resume_bytes=False
            ).resume_bytes
        )

        findings = self.store.save_resume_findings(
            "owner-a",
            first.id,
            snapshot_json='{"unsupported": 2}',
            fingerprint=" findings-v1 ",
        )
        self.assertEqual(findings.fingerprint, "findings-v1")
        self.assertEqual(
            self.store.get_resume_findings("owner-a", first.id).payload(),
            {"unsupported": 2},
        )

        preparation = self.store.save_interview_preparation(
            "owner-a",
            first.id,
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
        self.assertEqual(
            self.store.get_interview_preparation(
                "owner-a", first.id
            ).payload()["questions"],
            ["Why us?"],
        )

        impact = self.store.save_impact_snapshot(
            "owner-a",
            first.id,
            {
                "credentials_identified": 3,
                "baseline_alignment_score": 61.24,
                "current_alignment_score": 82.76,
                "alignment_improvement": 21.52,
                "verified_resume_ready": True,
                "explanation": "Evidence grounded",
            },
        )
        self.assertEqual(impact["current_alignment_score"], 82.8)
        self.assertEqual(
            self.store.get_impact_snapshot("owner-a", first.id)["application_id"],
            first.id,
        )
        self.assertEqual(
            self.store.list_impact_snapshots("owner-a")[0]["application_id"],
            first.id,
        )

        self.assertTrue(self.store.delete("owner-a", first.id))
        self.assertIsNone(self.store.get("owner-a", first.id))
        self.assertIsNone(self.store.get_resume_findings("owner-a", first.id))
        self.assertIsNone(
            self.store.get_interview_preparation("owner-a", first.id)
        )
        self.assertIsNone(self.store.get_impact_snapshot("owner-a", first.id))
        self.assertFalse(self.store.delete("owner-a", first.id))


class DynamoDBApplicationStoreContractTests(
    ApplicationStoreContractMixin, unittest.TestCase
):
    def setUp(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBApplicationStore

        self.table = FakeApplicationTable()
        self.objects = FakeObjectStore()
        ids = iter(["app-one", "app-two", "app-three", "app-four"])
        self.store = DynamoDBApplicationStore(
            {
                "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "applications",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
            },
            table=self.table,
            document_store=self.objects,
            id_factory=lambda: next(ids),
            clock=IncrementingClock(),
        )


class WorkflowStoreContractMixin:
    """One complete WorkflowStore behavior suite used by both adapters."""

    store: Any

    def test_complete_workflow_store_contract(self) -> None:
        from resume_tailor.storage import WorkflowConflictError

        self.assertNotEqual(self.store.new_id(), self.store.new_id())
        workflow_key = "owner-a:application:app-one"
        first = self.store.load(workflow_key)
        stale = self.store.load(workflow_key)
        self.assertEqual(first.version, 0)
        self.assertIsNot(first.state, stale.state)

        first.state.target_title = "Senior Engineer"
        saved = self.store.save(
            workflow_key,
            first.state,
            expected_version=first.version,
            updated_by_request="REQ-CONTRACT-ONE",
        )
        self.assertEqual(saved.version, 1)
        self.assertEqual(saved.updated_by_request, "REQ-CONTRACT-ONE")
        self.assertEqual(self.store.get(workflow_key).target_title, "Senior Engineer")
        self.assertEqual(self.store.peek(workflow_key).target_title, "Senior Engineer")

        stale.state.target_title = "Stale change"
        with self.assertRaises(WorkflowConflictError):
            self.store.save(
                workflow_key,
                stale.state,
                expected_version=stale.version,
                updated_by_request="REQ-CONTRACT-STALE",
            )

        reset = self.store.reset(workflow_key)
        self.assertEqual(reset.target_title, "")
        self.store.delete(workflow_key)
        self.assertIsNone(self.store.peek(workflow_key))


class WorkflowContractBase:
    @classmethod
    def setUpClass(cls) -> None:
        from resume_tailor.profile_io import load_candidate_profile

        cls.profile = load_candidate_profile(
            ROOT / "tests" / "fixtures" / "candidate_profile.json"
        )

    @classmethod
    def state_factory(cls):
        from resume_tailor.web_state import WorkflowState

        return WorkflowState(source_profile=cls.profile.model_copy(deep=True))


class MemoryWorkflowStoreContractTests(
    WorkflowContractBase, WorkflowStoreContractMixin, unittest.TestCase
):
    def setUp(self) -> None:
        from resume_tailor.web_state import InMemoryWorkflowStore

        self.store = InMemoryWorkflowStore(self.state_factory)


class DynamoDBWorkflowStoreContractTests(
    WorkflowContractBase, WorkflowStoreContractMixin, unittest.TestCase
):
    def setUp(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore

        self.table = FakeWorkflowTable()
        self.objects = FakeObjectStore()
        self.store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
            },
            self.state_factory,
            table=self.table,
            document_store=self.objects,
        )


class StorageMigrationAndDurabilityTests(WorkflowContractBase, unittest.TestCase):
    def setUp(self) -> None:
        from resume_tailor.dynamodb_storage import (
            DynamoDBApplicationStore,
            DynamoDBWorkflowStore,
        )

        self.application_table = FakeApplicationTable()
        self.workflow_table = FakeWorkflowTable()
        self.objects = FakeObjectStore()
        self.application_config = {
            "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "applications",
            "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
        }
        self.workflow_config = {
            "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "workflows",
            "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
        }
        self._app_ids = iter(["app-one", "app-two", "app-three"])
        self.application_factory: Callable[[], Any] = lambda: DynamoDBApplicationStore(
            self.application_config,
            table=self.application_table,
            document_store=self.objects,
            id_factory=lambda: next(self._app_ids),
            clock=IncrementingClock(),
        )
        self.workflow_factory: Callable[[], Any] = lambda: DynamoDBWorkflowStore(
            self.workflow_config,
            self.state_factory,
            table=self.workflow_table,
            document_store=self.objects,
        )

    def test_state_survives_simulated_process_restart(self) -> None:
        applications_a = self.application_factory()
        workflows_a = self.workflow_factory()
        application = applications_a.create(
            "owner-a", company="Durable Co", role="Engineer"
        )
        loaded = workflows_a.load(f"owner-a:application:{application.id}")
        loaded.state.target_title = "Durable Engineer"
        workflows_a.save(
            f"owner-a:application:{application.id}",
            loaded.state,
            expected_version=loaded.version,
            updated_by_request="REQ-PROCESS-A",
        )

        del applications_a, workflows_a
        applications_b = self.application_factory()
        workflows_b = self.workflow_factory()
        self.assertEqual(
            applications_b.get("owner-a", application.id).company,
            "Durable Co",
        )
        self.assertEqual(
            workflows_b.get(f"owner-a:application:{application.id}").target_title,
            "Durable Engineer",
        )

    @unittest.skipUnless(
        importlib.util.find_spec("flask") is not None,
        "Flask is not installed in this validation environment",
    )
    def test_state_written_by_flask_instance_a_is_read_by_instance_b(self) -> None:
        from flask import Flask

        app_a = Flask("career-bridge-instance-a")
        app_a.extensions["career_bridge_application_store"] = self.application_factory()
        app_a.extensions["career_bridge_workflow_store"] = self.workflow_factory()
        with app_a.app_context():
            application = app_a.extensions[
                "career_bridge_application_store"
            ].create("owner-a", company="Shared Co", role="Architect")
            workflow_key = f"owner-a:application:{application.id}"
            loaded = app_a.extensions["career_bridge_workflow_store"].load(
                workflow_key
            )
            loaded.state.target_title = "Principal Architect"
            app_a.extensions["career_bridge_workflow_store"].save(
                workflow_key,
                loaded.state,
                expected_version=loaded.version,
                updated_by_request="REQ-FLASK-A",
            )

        # Instance B is created only after A has committed, mirroring a new
        # worker/node coming online or an application-factory restart.
        app_b = Flask("career-bridge-instance-b")
        app_b.extensions["career_bridge_application_store"] = self.application_factory()
        app_b.extensions["career_bridge_workflow_store"] = self.workflow_factory()
        with app_b.app_context():
            self.assertEqual(
                app_b.extensions["career_bridge_application_store"].get(
                    "owner-a", application.id
                ).company,
                "Shared Co",
            )
            self.assertEqual(
                app_b.extensions["career_bridge_workflow_store"].get(
                    workflow_key
                ).target_title,
                "Principal Architect",
            )

    @unittest.skipUnless(
        importlib.util.find_spec("flask") is not None,
        "Flask is not installed in this validation environment",
    )
    def test_application_builder_initializer_preserves_state_across_app_instances(self) -> None:
        from flask import Flask
        from unittest.mock import patch

        builder_path = RESUME_TAYLOR_ROOT / "app.py"
        spec = importlib.util.spec_from_file_location(
            "career_bridge_storage_contract_builder", builder_path
        )
        if spec is None or spec.loader is None:
            self.fail("Could not load the Application Builder module")
        builder = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(builder)
        except ImportError as exc:
            self.skipTest(f"Application Builder runtime dependency unavailable: {exc}")

        config = {
            "TESTING": True,
            "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "dynamodb",
            "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "dynamodb",
            "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "s3",
            "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "applications",
            "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "workflows",
            "CAREER_BRIDGE_DOCUMENTS_BUCKET": "documents",
        }

        def initialize(name: str) -> Flask:
            app = Flask(name)
            app.config.update(config)
            with (
                patch.object(builder, "create_document_store", return_value=self.objects),
                patch.object(
                    builder,
                    "create_application_store",
                    side_effect=lambda *_args, **_kwargs: self.application_factory(),
                ),
                patch.object(
                    builder,
                    "create_workflow_store",
                    side_effect=lambda *_args, **_kwargs: self.workflow_factory(),
                ),
            ):
                builder.init_application_builder(app)
            return app

        app_a = initialize("builder-instance-a")
        application_store_a = app_a.extensions["career_bridge_application_store"]
        workflow_store_a = app_a.extensions["career_bridge_workflow_store"]
        application = application_store_a.create(
            "owner-a", company="Initialized Co", role="Engineer"
        )
        workflow_key = f"owner-a:application:{application.id}"
        loaded = workflow_store_a.load(workflow_key)
        loaded.state.target_title = "Initialized Engineer"
        workflow_store_a.save(
            workflow_key,
            loaded.state,
            expected_version=loaded.version,
            updated_by_request="REQ-INITIALIZER-A",
        )

        app_b = initialize("builder-instance-b")
        self.assertEqual(
            app_b.extensions["career_bridge_application_store"].get(
                "owner-a", application.id
            ).company,
            "Initialized Co",
        )
        self.assertEqual(
            app_b.extensions["career_bridge_workflow_store"].get(
                workflow_key
            ).target_title,
            "Initialized Engineer",
        )

    def test_large_resume_is_written_to_s3_not_dynamodb(self) -> None:
        store = self.application_factory()
        large_resume = b"R" * (600 * 1024)
        application = store.create(
            "owner-large",
            company="Large Resume Co",
            role="Engineer",
            resume_filename="large-resume.docx",
            resume_bytes=large_resume,
            resume_fingerprint="large-resume-v1",
        )
        item = self.application_table.items[
            ("owner-large", f"APP#{application.id}")
        ]
        self.assertNotIn("resume_bytes", item)
        self.assertTrue(item["resume_docx_key"])
        self.assertEqual(self.objects.get(item["resume_docx_key"]), large_resume)
        self.assertLess(len(json.dumps(item, default=str).encode("utf-8")), 20_000)

    def test_concurrent_workflow_updates_detect_stale_versions(self) -> None:
        from resume_tailor.storage import WorkflowConflictError

        node_a = self.workflow_factory()
        node_b = self.workflow_factory()
        workflow_key = "owner-a:application:app-one"
        seed = node_a.load(workflow_key)
        seed.state.target_title = "Seed"
        node_a.save(
            workflow_key,
            seed.state,
            expected_version=seed.version,
            updated_by_request="REQ-SEED",
        )
        request_a = node_a.load(workflow_key)
        request_b = node_b.load(workflow_key)
        request_a.state.target_title = "Node A"
        request_b.state.target_title = "Node B"
        node_a.save(
            workflow_key,
            request_a.state,
            expected_version=request_a.version,
            updated_by_request="REQ-NODE-A",
        )
        with self.assertRaises(WorkflowConflictError) as conflict:
            node_b.save(
                workflow_key,
                request_b.state,
                expected_version=request_b.version,
                updated_by_request="REQ-NODE-B",
            )
        self.assertEqual(conflict.exception.actual_version, 2)
        self.assertEqual(
            conflict.exception.actual_updated_by_request,
            "REQ-NODE-A",
        )

    def test_owner_isolation_blocks_cross_owner_application_reads(self) -> None:
        store = self.application_factory()
        application = store.create(
            "owner-a", company="Private Co", role="Private Role"
        )
        self.assertIsNone(store.get("owner-b", application.id))
        self.assertEqual(store.list_for_owner("owner-b"), [])
        self.assertFalse(store.delete("owner-b", application.id))
        self.assertIsNotNone(store.get("owner-a", application.id))

    def test_deleting_application_deletes_all_referenced_s3_objects(self) -> None:
        store = self.application_factory()
        application = store.create(
            "owner-a",
            company="Cleanup Co",
            role="Engineer",
            resume_filename="resume.docx",
            resume_bytes=b"docx",
            resume_pdf_filename="resume.pdf",
            resume_pdf_bytes=b"pdf",
        )
        original_key = "career-bridge/users/hash/original/original.docx"
        self.objects.put(original_key, b"original", "application/octet-stream")
        store.update_builder_progress(
            "owner-a",
            application.id,
            workflow_step="tailor",
            original_resume_key=original_key,
        )
        store.save_resume_findings(
            "owner-a",
            application.id,
            snapshot_json='{"large": "report"}',
            fingerprint="findings",
        )
        store.save_interview_preparation(
            "owner-a",
            application.id,
            content_json='{"questions": []}',
            job_description_fingerprint="job",
            evidence_fingerprint="evidence",
            evidence_source_label="resume",
            evidence_snapshot_json='{"ids": []}',
            resume_findings_fingerprint="findings",
            resume_findings_snapshot_json='{"large": "report"}',
            model_name="test",
        )
        store.save_impact_snapshot(
            "owner-a", application.id, {"explanation": "snapshot"}
        )
        referenced = set(self.objects.items)
        self.assertTrue(store.delete("owner-a", application.id))
        self.assertTrue(referenced.issubset(set(self.objects.deleted)))

    def test_legacy_inline_application_resume_is_migrated_on_rewrite(self) -> None:
        store = self.application_factory()
        application = store.create(
            "owner-a", company="Legacy Co", role="Engineer"
        )
        key = ("owner-a", f"APP#{application.id}")
        self.application_table.items[key]["resume_filename"] = "legacy.docx"
        self.application_table.items[key]["resume_bytes"] = b"legacy-inline"
        self.assertEqual(
            store.get("owner-a", application.id).resume_bytes,
            b"legacy-inline",
        )

        store.attach_resume_snapshot(
            "owner-a",
            application.id,
            resume_version="Migrated",
            resume_style="professional",
            alignment_score=80,
            overall_score=80,
            resume_filename="migrated.docx",
            resume_bytes=b"migrated-s3",
            resume_fingerprint="migrated",
        )
        rewritten = self.application_table.items[key]
        self.assertNotIn("resume_bytes", rewritten)
        self.assertEqual(
            self.objects.get(rewritten["resume_docx_key"]),
            b"migrated-s3",
        )

    def test_legacy_inline_workflow_state_is_removed_on_s3_rewrite(self) -> None:
        from resume_tailor.workflow_serialization import serialize_workflow_state

        store = self.workflow_factory()
        workflow_key = "owner-a:application:legacy-app"
        workflow_id = store._workflow_id(workflow_key)
        state = self.state_factory()
        state.target_title = "Legacy inline"
        self.workflow_table.items[workflow_id] = {
            "workflow_id": workflow_id,
            "entity_type": "career_bridge_workflow",
            "workflow_type": "application",
            "retention_policy": "retained",
            "version": 1,
            "state_json": serialize_workflow_state(state),
            "updated_at": "2026-07-30T16:00:00+00:00",
            "updated_by_request": "REQ-LEGACY",
        }
        loaded = store.load(workflow_key)
        loaded.state.target_title = "Migrated to S3"
        store.save(
            workflow_key,
            loaded.state,
            expected_version=loaded.version,
            updated_by_request="REQ-MIGRATE",
        )
        rewritten = self.workflow_table.items[workflow_id]
        self.assertNotIn("state_json", rewritten)
        self.assertNotIn("state", rewritten)
        self.assertTrue(rewritten["state_json_key"])
        self.assertEqual(store.get(workflow_key).target_title, "Migrated to S3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
