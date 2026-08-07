from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "delete_dynamodb_user_records.py"
BAT_PATH = ROOT / "scripts" / "delete_dynamodb_user_records.bat"
README_PATH = ROOT / "README.md"
SCRIPTS_README_PATH = ROOT / "scripts" / "README.md"

spec = importlib.util.spec_from_file_location("delete_dynamodb_user_records", SCRIPT_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeTable:
    def __init__(
        self,
        query_items=None,
        query_responses=None,
        scan_items=None,
        get_items=None,
    ):
        self.query_items = list(query_items or [])
        self.query_responses = [list(items) for items in (query_responses or [])]
        self.scan_items = list(scan_items or [])
        self.get_items = dict(get_items or {})
        self.query_calls = []
        self.scan_calls = []
        self.get_calls = []

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.query_responses:
            return {"Items": self.query_responses.pop(0)}
        return {"Items": list(self.query_items)}

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        return {"Items": list(self.scan_items)}

    def get_item(self, **kwargs):
        self.get_calls.append(kwargs)
        workflow_id = str((kwargs.get("Key") or {}).get("workflow_id") or "")
        item = self.get_items.get(workflow_id)
        return {"Item": dict(item)} if item else {}


class FakeBatch:
    def __init__(self, table_name: str, deleted: list[tuple[str, dict]]):
        self.table_name = table_name
        self.deleted = deleted

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def delete_item(self, *, Key):
        self.deleted.append((self.table_name, dict(Key)))


class FakeDeleteTable:
    def __init__(self, table_name: str, deleted: list[tuple[str, dict]]):
        self.table_name = table_name
        self.deleted = deleted

    def batch_writer(self):
        return FakeBatch(self.table_name, self.deleted)


class FakeDynamo:
    def __init__(self):
        self.deleted: list[tuple[str, dict]] = []

    def Table(self, table_name: str):
        return FakeDeleteTable(table_name, self.deleted)


class DeleteDynamoDbUserRecordsContractTests(TestCase):
    def test_default_table_names_are_canonical_and_complete(self):
        with patch.dict(os.environ, {}, clear=True):
            names = module.resolve_configured_table_names([])

        self.assertIn("careerbridge_users", names)
        self.assertIn("careerbridge_transcripts", names)
        self.assertIn("careerbridge_actions", names)
        self.assertIn("careerbridge_app_analytics", names)
        self.assertIn("careerbridge_support_requests", names)
        self.assertIn("careerbridge_knowledge", names)
        self.assertIn("careerbridge_applications", names)
        self.assertIn("careerbridge_workflows", names)
        self.assertIn("careerbridge_job_discovery", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(name.startswith("careerbridge_") for name in names))

    def test_noncanonical_table_names_are_rejected(self):
        with patch.dict(
            os.environ,
            {"CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "career-bridge-applications"},
            clear=True,
        ):
            with self.assertRaises(module.CleanupError):
                module.resolve_configured_table_names([])

    def test_owner_partition_and_async_queue_ticket_are_both_matched(self):
        user_record = {
            "owner_id": "person@example.com",
            "storage_key": "ASYNC#JOB#job-1",
            "job_id": "job-1",
        }
        queue_ticket = {
            "owner_id": "__CAREER_BRIDGE_ASYNC_QUEUE__",
            "storage_key": "ASYNC#QUEUED#2026#person@example.com#job-1",
            "job_owner_id": "person@example.com",
            "job_id": "job-1",
        }
        unrelated = {
            "owner_id": "another@example.com",
            "storage_key": "APPLICATION#other",
        }
        table = FakeTable(
            query_responses=[[user_record], [queue_ticket]],
            scan_items=[unrelated],
        )
        table_spec = module.TableSpec(
            name="careerbridge_job_discovery",
            key_names=("owner_id", "storage_key"),
            hash_key="owner_id",
            indexes=(),
        )

        matches = module.find_table_matches(
            table,
            table_spec,
            {"person@example.com"},
        )

        self.assertEqual(2, len(matches))
        self.assertEqual(
            {"owner_id", "job_owner_id"},
            {field for match in matches for field in match.matched_fields},
        )
        self.assertEqual(2, len(table.query_calls))
        self.assertEqual(0, len(table.scan_calls))


    def test_hashed_foundation_workflow_is_discovered_without_owner_field(self):
        workflow_key = "person@example.com:career-foundation:translation"
        workflow_id = module.workflow_id_for_key(workflow_key)
        table = FakeTable(
            get_items={
                workflow_id: {
                    "workflow_id": workflow_id,
                    "entity_type": "career_bridge_workflow",
                    "state_json_key": (
                        "career-bridge/workflow-state/foundation/users/hash/state.json"
                    ),
                }
            }
        )
        spec = module.TableSpec(
            name="careerbridge_workflows",
            key_names=("workflow_id",),
            hash_key="workflow_id",
            indexes=(),
        )

        matches = module.find_derived_workflow_matches(
            table,
            spec,
            {"person@example.com"},
            [],
        )

        self.assertEqual(1, len(matches))
        self.assertEqual({"workflow_id": workflow_id}, matches[0].key)
        self.assertEqual(("derived_workflow_id",), matches[0].matched_fields)
        self.assertEqual(workflow_key, matches[0].item["derived_workflow_key"])
        self.assertGreaterEqual(len(table.get_calls), 2)

    def test_workflow_table_is_not_scanned_for_missing_identity_fields(self):
        table = FakeTable(
            scan_items=[
                {
                    "workflow_id": "opaque",
                    "state_json_key": "career-bridge/workflow-state/foundation/x.json",
                }
            ]
        )
        spec = module.TableSpec(
            name="careerbridge_workflows",
            key_names=("workflow_id",),
            hash_key="workflow_id",
            indexes=(),
        )

        matches = module.find_table_matches(
            table,
            spec,
            {"person@example.com"},
        )

        self.assertEqual([], matches)
        self.assertEqual([], table.scan_calls)

    def test_user_s3_prefixes_include_foundation_workflow_state(self):
        aliases = {"person@example.com"}
        namespace = module.document_owner_namespace("person@example.com")

        prefixes = module.user_document_prefixes(aliases, "career-bridge")

        self.assertIn(f"career-bridge/users/{namespace}/", prefixes)
        self.assertIn(
            f"career-bridge/workflow-state/foundation/users/{namespace}/",
            prefixes,
        )

    def test_document_keys_are_extracted_from_nested_workflow_state(self):
        payload = {
            "state_json_key": "career-bridge/workflow-state/foundation/a.json",
            "nested": [
                {"source_resume_key": "career-bridge/users/u/resumes/source.docx"},
                {"storage_key": "APP#not-an-s3-key"},
            ],
        }

        keys = module.extract_document_object_keys(payload, "career-bridge")

        self.assertEqual(
            {
                "career-bridge/workflow-state/foundation/a.json",
                "career-bridge/users/u/resumes/source.docx",
            },
            keys,
        )

    def test_user_table_is_deleted_last(self):
        dynamo = FakeDynamo()
        matches = [
            module.RecordMatch(
                table_name="careerbridge_users",
                key={"user_id": "person@example.com"},
                matched_fields=("user_id",),
                item={"user_id": "person@example.com"},
            ),
            module.RecordMatch(
                table_name="careerbridge_applications",
                key={"owner_id": "person@example.com", "storage_key": "APPLICATION#1"},
                matched_fields=("owner_id",),
                item={"owner_id": "person@example.com", "storage_key": "APPLICATION#1"},
            ),
        ]

        deleted_count = module.delete_matches(
            dynamo,
            matches,
            "careerbridge_users",
        )

        self.assertEqual(2, deleted_count)
        self.assertEqual("careerbridge_applications", dynamo.deleted[0][0])
        self.assertEqual("careerbridge_users", dynamo.deleted[-1][0])

    def test_batch_wrapper_and_documentation_exist(self):
        batch = BAT_PATH.read_text(encoding="utf-8")
        self.assertIn("delete_dynamodb_user_records.py", batch)
        self.assertIn("%*", batch)

        root_readme = README_PATH.read_text(encoding="utf-8")
        scripts_readme = SCRIPTS_README_PATH.read_text(encoding="utf-8")
        for content in (root_readme, scripts_readme):
            self.assertIn("delete_dynamodb_user_records.bat", content)
            self.assertIn("--delete", content)
            self.assertIn("S3", content)
