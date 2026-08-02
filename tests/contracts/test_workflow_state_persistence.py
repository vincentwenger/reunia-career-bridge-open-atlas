"""Serialization and optimistic-lock contracts for workflow persistence."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"


class FakeWorkflowTable:
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
            conflict = current is not None and "version" in current
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
                item.pop("expires_at", None)
        else:
            item["workflow_type"] = values[":workflow_type"]
            item["retention_policy"] = values[":retention_policy"]
            item["version"] = values[":new_version"]
            item["updated_at"] = values[":updated_at"]
            item["updated_by_request"] = values[":updated_by_request"]
            if "REMOVE #expires_at" in str(kwargs.get("UpdateExpression") or ""):
                item.pop("expires_at", None)
        self.items[key] = deepcopy(item)
        return {"Attributes": previous} if previous is not None else {}

    def delete_item(self, *, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        previous = self.items.pop(self._key(Key), None)
        if kwargs.get("ReturnValues") == "ALL_OLD" and previous is not None:
            return {"Attributes": deepcopy(previous)}
        return {}


class FakeObjectStore:
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
        self.metadata[object_key] = dict(metadata or {})
        self.metadata[object_key]["content-type"] = content_type

    def get(self, object_key: str) -> bytes:
        return self.items[object_key]

    def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)
        self.items.pop(object_key, None)


class WorkflowStatePersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(RESUME_TAYLOR_ROOT))
        from resume_tailor.profile_io import load_candidate_profile

        cls.profile = load_candidate_profile(
            RESUME_TAYLOR_ROOT / "data" / "candidate_profile.json"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if sys.path and sys.path[0] == str(RESUME_TAYLOR_ROOT):
            sys.path.pop(0)

    def _state(self):
        from resume_tailor.models import (
            CandidateAnswer,
            JobAnalysis,
            SkillSet,
            TailoringProposal,
        )
        from resume_tailor.resume_report import (
            ReportCheck,
            ReportSection,
            ReportSubsection,
            ResumeReport,
        )
        from resume_tailor.web_state import WorkflowState, WorkflowStepSnapshot

        proposal = TailoringProposal(
            professional_summary="Evidence-backed summary",
            skills=SkillSet(hard_skills=["SQL"], tools_software=["Oracle"]),
            bullet_proposals=[],
            evidence_matches=[],
            unsupported_requirements=["Kubernetes"],
        )

        def section(name: str) -> ReportSection:
            return ReportSection(
                name=name,
                intro=f"{name} checks",
                subsections=[
                    ReportSubsection(
                        name="Core",
                        checks=[
                            ReportCheck(
                                label="Verified",
                                status="pass",
                                detail="Supported by candidate evidence.",
                                weight=1.5,
                                score_value=96.5,
                            )
                        ],
                    )
                ],
            )

        report = ResumeReport(
            searchability=section("Searchability"),
            hard_skills=section("Hard skills"),
            soft_skills=section("Soft skills"),
            content_quality=section("Content quality"),
            recruiter_tips=section("Recruiter tips"),
            formatting=section("Formatting"),
            evidence_gaps=section("Evidence gaps"),
        )
        state = WorkflowState(source_profile=self.profile.model_copy(deep=True))
        state.target_title = "Senior Data Engineer"
        state.analysis = JobAnalysis(
            target_title="Senior Data Engineer",
            target_company="Example Bank",
            requirements=[],
        )
        state.draft_proposal = proposal
        state.confirmed_profile = self.profile.model_copy(deep=True)
        state.candidate_answers = [
            CandidateAnswer(
                question_id="q-1",
                question="Have you led migrations?",
                answer_type="yes_no_with_details",
                yes_no=True,
                text="Led a regulatory platform migration.",
            )
        ]
        state.confirmation_draft = {"q-1": "confirmed"}
        state.initial_report = report
        state.updated_report = report
        state.workflow_step_snapshots = {
            "tailor": WorkflowStepSnapshot(
                stage="tailor",
                captured_at="2026-07-30T15:00:00+00:00",
                target_title=state.target_title,
                proposal=proposal,
                profile=state.confirmed_profile,
                candidate_answers=list(state.candidate_answers),
                change_label="Initial tailored version",
            )
        }
        state.final_resume_docx_key = "career-bridge/users/hash/workflows/x/final.docx"
        state.final_resume_pdf_key = "career-bridge/users/hash/workflows/x/final.pdf"
        return state

    def test_serializer_round_trips_models_dataclasses_collections_and_reports(self) -> None:
        from resume_tailor.workflow_serialization import (
            WORKFLOW_STATE_SCHEMA_VERSION,
            deserialize_workflow_state,
            serialize_workflow_state,
            workflow_state_fingerprint,
            workflow_state_from_json_bytes,
            workflow_state_json_bytes,
        )

        state = self._state()
        payload = serialize_workflow_state(state)
        self.assertEqual(payload["schema_version"], WORKFLOW_STATE_SCHEMA_VERSION)
        restored = deserialize_workflow_state(payload)
        restored_from_json = workflow_state_from_json_bytes(
            workflow_state_json_bytes(state)
        )
        self.assertEqual(restored, state)
        self.assertEqual(restored_from_json, state)
        self.assertEqual(
            workflow_state_fingerprint(restored),
            workflow_state_fingerprint(state),
        )
        self.assertEqual(type(restored.initial_report).__name__, "ResumeReport")
        self.assertEqual(
            type(restored.workflow_step_snapshots["tailor"]).__name__,
            "WorkflowStepSnapshot",
        )
        self.assertEqual(type(restored.draft_proposal).__name__, "TailoringProposal")

    def test_serializer_rejects_embedded_document_bytes(self) -> None:
        from resume_tailor.workflow_serialization import (
            WorkflowSerializationError,
            workflow_state_json_bytes,
        )

        state = self._state()
        state.final_resume_bytes = b"docx"
        with self.assertRaisesRegex(
            WorkflowSerializationError, "final_resume_bytes.*object storage"
        ):
            workflow_state_json_bytes(state)

    def test_legacy_ttl_setting_applies_only_to_scratch_workflows(self) -> None:
        from resume_tailor.storage import workflow_ttl_seconds

        config = {"CAREER_BRIDGE_WORKFLOW_TTL_SECONDS": 7200}
        self.assertEqual(
            workflow_ttl_seconds(config, "owner:application:scratch"),
            7200,
        )
        self.assertIsNone(
            workflow_ttl_seconds(config, "owner:application:app-one")
        )
        self.assertIsNone(
            workflow_ttl_seconds(config, "owner:career-foundation:translation")
        )

    def test_memory_store_returns_detached_snapshots_and_detects_stale_saves(self) -> None:
        from resume_tailor.storage import WorkflowConflictError
        from resume_tailor.web_state import InMemoryWorkflowStore

        store = InMemoryWorkflowStore(self._state)
        first = store.load("owner:application:one")
        second = store.load("owner:application:one")
        self.assertIsNot(first.state, second.state)
        first.state.target_title = "First update"
        saved = store.save(
            "owner:application:one",
            first.state,
            expected_version=first.version,
            updated_by_request="REQ-MEMORY-ONE",
        )
        self.assertEqual(saved.version, 1)
        self.assertEqual(saved.updated_by_request, "REQ-MEMORY-ONE")
        second.state.target_title = "Stale update"
        with self.assertRaises(WorkflowConflictError):
            store.save(
                "owner:application:one",
                second.state,
                expected_version=second.version,
                updated_by_request="REQ-MEMORY-TWO",
            )
        self.assertEqual(store.get("owner:application:one").target_title, "First update")

    def test_dynamodb_store_uses_s3_pointer_and_conditional_versions(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore
        from resume_tailor.storage import WorkflowConflictError, WorkflowStore

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
                "CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS": 3600,
                "AWS_REGION": "us-west-2",
            },
            self._state,
            table=table,
            document_store=objects,
            clock=lambda: "2026-07-30T15:30:00+00:00",
            epoch_clock=lambda: 1_786_000_000,
        )
        self.assertIsInstance(store, WorkflowStore)
        workflow_key = "vincent@example.com:application:scratch"
        first = store.load(workflow_key)
        stale = store.load(workflow_key)
        first.state.job_description = "x" * 450_000
        saved = store.save(
            workflow_key,
            first.state,
            expected_version=first.version,
            updated_by_request="REQ-DDB-FIRST",
        )
        self.assertEqual(saved.version, 1)
        self.assertEqual(
            table.update_calls[-1]["ConditionExpression"],
            "attribute_not_exists(#workflow_id)",
        )
        self.assertEqual(store.load(workflow_key).state.job_description, "x" * 450_000)

        self.assertEqual(len(table.items), 1)
        item = next(iter(table.items.values()))
        self.assertNotIn(workflow_key, json.dumps(item, default=str))
        self.assertNotIn("state", item)
        self.assertNotIn("state_json", item)
        self.assertIn("state_json_key", item)
        self.assertIn("/workflow-state/scratch/", item["state_json_key"])
        self.assertLess(len(json.dumps(item, default=str).encode("utf-8")), 4_000)
        self.assertGreater(len(objects.items[item["state_json_key"]]), 400_000)
        self.assertEqual(item["version"], 1)
        self.assertEqual(item["updated_at"], "2026-07-30T15:30:00+00:00")
        self.assertEqual(item["updated_by_request"], "REQ-DDB-FIRST")
        self.assertEqual(saved.updated_by_request, "REQ-DDB-FIRST")
        self.assertEqual(item["workflow_type"], "scratch")
        self.assertEqual(item["retention_policy"], "dynamodb_ttl")
        self.assertEqual(item["expires_at"], 1_786_003_600)

        stale.state.target_title = "Stale browser change"
        object_count = len(objects.items)
        with self.assertRaises(WorkflowConflictError) as conflict:
            store.save(
                workflow_key,
                stale.state,
                expected_version=stale.version,
                updated_by_request="REQ-DDB-STALE",
            )
        self.assertEqual(conflict.exception.actual_version, 1)
        self.assertEqual(
            conflict.exception.actual_updated_by_request,
            "REQ-DDB-FIRST",
        )
        self.assertEqual(len(objects.items), object_count)

        fresh = store.load(workflow_key)
        previous_key = item["state_json_key"]
        fresh.state.target_title = "Fresh browser change"
        second_save = store.save(
            workflow_key,
            fresh.state,
            expected_version=fresh.version,
            updated_by_request="REQ-DDB-SECOND",
        )
        self.assertEqual(second_save.version, 2)
        self.assertEqual(
            table.update_calls[-1]["ConditionExpression"],
            "#version = :expected_version",
        )
        self.assertEqual(second_save.updated_by_request, "REQ-DDB-SECOND")
        self.assertIn(previous_key, objects.deleted)
        self.assertEqual(store.load(workflow_key).state.target_title, "Fresh browser change")

        latest_key = next(iter(table.items.values()))["state_json_key"]
        store.delete(workflow_key)
        self.assertEqual(table.items, {})
        self.assertIn(latest_key, objects.deleted)
        self.assertIsNone(store.peek(workflow_key))

    def test_application_workflow_is_retained_without_dynamodb_ttl(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
                "CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS": 3600,
                "CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS": 0,
            },
            self._state,
            table=table,
            document_store=objects,
            epoch_clock=lambda: 1_786_000_000,
        )
        workflow_key = "vincent@example.com:application:app-one"
        loaded = store.load(workflow_key)
        loaded.state.target_title = "Retained application workflow"
        store.save(
            workflow_key,
            loaded.state,
            expected_version=loaded.version,
            updated_by_request="REQ-APPLICATION-RETAINED",
        )

        item = next(iter(table.items.values()))
        self.assertEqual(item["workflow_type"], "application")
        self.assertEqual(item["retention_policy"], "retained")
        self.assertIn("/workflow-state/application/", item["state_json_key"])
        self.assertNotIn("expires_at", item)

    def test_career_foundation_workflow_is_retained_without_dynamodb_ttl(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
                "CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS": 3600,
                "CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS": 0,
            },
            self._state,
            table=table,
            document_store=objects,
            epoch_clock=lambda: 1_786_000_000,
        )
        workflow_key = "vincent@example.com:career-foundation:translation"
        loaded = store.load(workflow_key)
        loaded.state.target_title = "Reusable translated baseline"
        store.save(
            workflow_key,
            loaded.state,
            expected_version=loaded.version,
            updated_by_request="REQ-FOUNDATION-RETAINED",
        )

        item = next(iter(table.items.values()))
        self.assertEqual(item["workflow_type"], "foundation")
        self.assertEqual(item["retention_policy"], "retained")
        self.assertIn("/workflow-state/foundation/", item["state_json_key"])
        self.assertNotIn("expires_at", item)

    def test_application_workflow_can_use_an_explicit_longer_ttl(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
                "CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS": 30 * 24 * 60 * 60,
            },
            self._state,
            table=table,
            document_store=objects,
            epoch_clock=lambda: 1_786_000_000,
        )
        workflow_key = "owner:application:app-with-retention-window"
        loaded = store.load(workflow_key)
        loaded.state.target_title = "Long-lived application workflow"
        store.save(
            workflow_key,
            loaded.state,
            expected_version=loaded.version,
            updated_by_request="REQ-APPLICATION-TTL",
        )

        item = next(iter(table.items.values()))
        self.assertEqual(item["workflow_type"], "application")
        self.assertEqual(item["retention_policy"], "dynamodb_ttl")
        self.assertEqual(item["expires_at"], 1_788_592_000)

    def test_loading_legacy_application_workflow_removes_blanket_ttl(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore
        from resume_tailor.workflow_serialization import workflow_state_json_bytes

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        workflow_key = "vincent@example.com:application:legacy-app"
        store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
            },
            self._state,
            table=table,
            document_store=objects,
        )
        state = self._state()
        state_key = "career-bridge/workflows/legacy/state.json"
        objects.put(state_key, workflow_state_json_bytes(state), "application/json")
        table.items[store._workflow_id(workflow_key)] = {
            "workflow_id": store._workflow_id(workflow_key),
            "entity_type": "career_bridge_workflow",
            "version": 3,
            "fingerprint": hashlib.sha256(
                workflow_state_json_bytes(state)
            ).hexdigest(),
            "state_json_key": state_key,
            "updated_at": "2026-07-30T15:30:00+00:00",
            "expires_at": 1_786_003_600,
        }

        loaded = store.load(workflow_key)
        self.assertEqual(loaded.version, 4)
        self.assertEqual(loaded.updated_by_request, "SYSTEM-TTL-MIGRATION")
        item = next(iter(table.items.values()))
        self.assertNotIn("expires_at", item)
        self.assertEqual(item["retention_policy"], "retained")
        self.assertEqual(item["version"], 4)
        self.assertEqual(item["updated_by_request"], "SYSTEM-TTL-MIGRATION")


    def test_loading_legacy_scratch_workflow_backfills_request_metadata(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore
        from resume_tailor.workflow_serialization import workflow_state_json_bytes

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        workflow_key = "owner:application:scratch"
        store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
            },
            self._state,
            table=table,
            document_store=objects,
            clock=lambda: "2026-07-30T16:00:00+00:00",
        )
        state = self._state()
        state_key = "career-bridge/workflow-state/scratch/legacy/state.json"
        serialized = workflow_state_json_bytes(state)
        objects.put(state_key, serialized, "application/json")
        table.items[store._workflow_id(workflow_key)] = {
            "workflow_id": store._workflow_id(workflow_key),
            "entity_type": "career_bridge_workflow",
            "workflow_type": "scratch",
            "retention_policy": "dynamodb_ttl",
            "version": 7,
            "fingerprint": hashlib.sha256(serialized).hexdigest(),
            "state_json_key": state_key,
            "updated_at": "2026-07-30T15:00:00+00:00",
            "expires_at": 1_786_003_600,
        }

        loaded = store.load(workflow_key)
        self.assertEqual(loaded.version, 8)
        self.assertEqual(
            loaded.updated_by_request,
            "SYSTEM-CONCURRENCY-MIGRATION",
        )
        item = next(iter(table.items.values()))
        self.assertEqual(item["version"], 8)
        self.assertEqual(
            item["updated_by_request"],
            "SYSTEM-CONCURRENCY-MIGRATION",
        )
        self.assertIn("expires_at", item)

    def test_loading_older_valid_payload_does_not_fail_after_schema_defaults_expand(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore
        from resume_tailor.workflow_serialization import workflow_state_json_bytes

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        workflow_key = "owner:application:scratch"
        store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
            },
            self._state,
            table=table,
            document_store=objects,
        )
        state = self._state()
        payload = json.loads(workflow_state_json_bytes(state).decode("utf-8"))
        # Simulate a document saved before this defaulted field was introduced.
        payload["state"].pop("quality_review_started", None)
        older_serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        state_key = "career-bridge/workflow-state/scratch/legacy-schema/state.json"
        objects.put(state_key, older_serialized, "application/json")
        table.items[store._workflow_id(workflow_key)] = {
            "workflow_id": store._workflow_id(workflow_key),
            "entity_type": "career_bridge_workflow",
            "workflow_type": "scratch",
            "retention_policy": "dynamodb_ttl",
            "version": 2,
            "fingerprint": hashlib.sha256(older_serialized).hexdigest(),
            "state_json_key": state_key,
            "updated_at": "2026-07-30T15:00:00+00:00",
            "updated_by_request": "REQ-OLD-SCHEMA",
            "expires_at": 1_786_003_600,
        }

        loaded = store.load(workflow_key)

        self.assertFalse(loaded.state.quality_review_started)
        self.assertEqual(loaded.version, 2)
        self.assertEqual(loaded.updated_by_request, "REQ-OLD-SCHEMA")

    def test_loading_meaningfully_tampered_payload_still_fails_integrity_check(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore
        from resume_tailor.workflow_serialization import workflow_state_json_bytes

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        workflow_key = "owner:application:scratch"
        store = DynamoDBWorkflowStore(
            {
                "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
                "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
            },
            self._state,
            table=table,
            document_store=objects,
        )
        original = workflow_state_json_bytes(self._state())
        payload = json.loads(original.decode("utf-8"))
        payload["state"]["target_title"] = "Unexpected altered title"
        altered = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        state_key = "career-bridge/workflow-state/scratch/tampered/state.json"
        objects.put(state_key, altered, "application/json")
        table.items[store._workflow_id(workflow_key)] = {
            "workflow_id": store._workflow_id(workflow_key),
            "entity_type": "career_bridge_workflow",
            "workflow_type": "scratch",
            "retention_policy": "dynamodb_ttl",
            "version": 2,
            "fingerprint": hashlib.sha256(original).hexdigest(),
            "state_json_key": state_key,
            "updated_at": "2026-07-30T15:00:00+00:00",
            "updated_by_request": "REQ-TAMPERED",
            "expires_at": 1_786_003_600,
        }

        with self.assertRaisesRegex(
            RuntimeError, "fingerprint does not match"
        ):
            store.load(workflow_key)

    def test_memory_store_expires_only_scratch_workflows_by_default(self) -> None:
        from resume_tailor.web_state import InMemoryWorkflowStore

        now = [1_000.0]
        store = InMemoryWorkflowStore(
            self._state,
            scratch_ttl_seconds=300,
            application_ttl_seconds=0,
            clock=lambda: now[0],
        )
        scratch_key = "owner:application:scratch"
        application_key = "owner:application:app-one"
        foundation_key = "owner:career-foundation:translation"
        scratch = store.load(scratch_key)
        application = store.load(application_key)
        foundation = store.load(foundation_key)
        scratch.state.target_title = "Temporary"
        application.state.target_title = "Durable"
        foundation.state.target_title = "Reusable baseline"
        store.save(
            scratch_key,
            scratch.state,
            expected_version=scratch.version,
            updated_by_request="REQ-SCRATCH",
        )
        store.save(
            application_key,
            application.state,
            expected_version=application.version,
            updated_by_request="REQ-APPLICATION",
        )
        store.save(
            foundation_key,
            foundation.state,
            expected_version=foundation.version,
            updated_by_request="REQ-FOUNDATION",
        )

        now[0] += 301
        self.assertIsNone(store.peek(scratch_key))
        self.assertEqual(store.peek(application_key).target_title, "Durable")
        self.assertEqual(store.peek(foundation_key).target_title, "Reusable baseline")


    def test_existing_version_conflict_is_rejected_across_nodes(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore
        from resume_tailor.storage import WorkflowConflictError

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        config = {
            "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
            "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
        }
        node_a = DynamoDBWorkflowStore(
            config,
            self._state,
            table=table,
            document_store=objects,
            clock=lambda: "2026-07-30T16:10:00+00:00",
        )
        node_b = DynamoDBWorkflowStore(
            config,
            self._state,
            table=table,
            document_store=objects,
            clock=lambda: "2026-07-30T16:11:00+00:00",
        )
        workflow_key = "owner:application:existing-version"
        initial = node_a.load(workflow_key)
        initial.state.target_title = "Version one"
        node_a.save(
            workflow_key,
            initial.state,
            expected_version=initial.version,
            updated_by_request="REQ-SEED",
        )

        request_a = node_a.load(workflow_key)
        request_b = node_b.load(workflow_key)
        self.assertEqual(request_a.version, 1)
        self.assertEqual(request_b.version, 1)
        request_a.state.target_title = "Node A update"
        request_b.state.target_title = "Node B stale update"
        committed = node_a.save(
            workflow_key,
            request_a.state,
            expected_version=request_a.version,
            updated_by_request="REQ-NODE-A-V2",
        )
        self.assertEqual(committed.version, 2)
        self.assertEqual(
            table.update_calls[-1]["ConditionExpression"],
            "#version = :expected_version",
        )

        with self.assertRaises(WorkflowConflictError) as conflict:
            node_b.save(
                workflow_key,
                request_b.state,
                expected_version=request_b.version,
                updated_by_request="REQ-NODE-B-STALE",
            )
        self.assertEqual(conflict.exception.expected_version, 1)
        self.assertEqual(conflict.exception.actual_version, 2)
        self.assertEqual(
            conflict.exception.actual_updated_by_request,
            "REQ-NODE-A-V2",
        )
        latest = node_b.load(workflow_key)
        self.assertEqual(latest.version, 2)
        self.assertEqual(latest.state.target_title, "Node A update")

    def test_identical_concurrent_save_does_not_delete_committed_object(self) -> None:
        from resume_tailor.dynamodb_storage import DynamoDBWorkflowStore
        from resume_tailor.storage import WorkflowConflictError

        table = FakeWorkflowTable()
        objects = FakeObjectStore()
        config = {
            "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "career-bridge-workflows",
            "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
        }
        node_a = DynamoDBWorkflowStore(
            config,
            self._state,
            table=table,
            document_store=objects,
        )
        node_b = DynamoDBWorkflowStore(
            config,
            self._state,
            table=table,
            document_store=objects,
        )
        workflow_key = "owner:application:same-result"
        first = node_a.load(workflow_key)
        second = node_b.load(workflow_key)
        first.state.target_title = "Same result"
        second.state.target_title = "Same result"
        committed = node_a.save(
            workflow_key,
            first.state,
            expected_version=first.version,
            updated_by_request="REQ-NODE-A",
        )
        committed_key = next(iter(table.items.values()))["state_json_key"]
        with self.assertRaises(WorkflowConflictError) as conflict:
            node_b.save(
                workflow_key,
                second.state,
                expected_version=second.version,
                updated_by_request="REQ-NODE-B",
            )
        self.assertEqual(
            conflict.exception.actual_updated_by_request,
            "REQ-NODE-A",
        )
        self.assertIn(committed_key, objects.items)
        self.assertNotIn(committed_key, objects.deleted)
        self.assertEqual(node_b.load(workflow_key).version, committed.version)


if __name__ == "__main__":
    unittest.main(verbosity=2)
