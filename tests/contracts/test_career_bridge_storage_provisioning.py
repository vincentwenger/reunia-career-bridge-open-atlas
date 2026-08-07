"""Dependency-free contracts for the Career Bridge AWS storage preflight."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deployment" / "provision_career_bridge_storage.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "career_bridge_storage_provisioning", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load storage provisioning script")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


provisioner = _load_script()


class StorageProvisioningContractTests(unittest.TestCase):
    def test_deployment_materials_do_not_reference_legacy_table_names(self) -> None:
        files = (
            ROOT / "README.md",
            ROOT / "docs" / "deployment" / "lightsail.md",
            ROOT / "docs" / "job_discovery.md",
            ROOT / "scripts" / "deployment" / "upload_to_lightsail.bat",
            ROOT / "scripts" / "deployment" / "provision_career_bridge_storage.bat",
            SCRIPT,
        )
        legacy_names = (
            "career-bridge-applications",
            "career-bridge-workflows",
            "career-bridge-job-discovery",
        )
        for file_path in files:
            content = file_path.read_text(encoding="utf-8")
            for legacy_name in legacy_names:
                with self.subTest(file=file_path.name, legacy_name=legacy_name):
                    self.assertNotIn(legacy_name, content)

    def test_expected_table_key_schemas_are_explicit(self) -> None:
        self.assertEqual(
            provisioner._expected_key_schema("owner_id", "storage_key"),
            {"owner_id": "HASH", "storage_key": "RANGE"},
        )
        self.assertEqual(
            provisioner._expected_key_schema("workflow_id"),
            {"workflow_id": "HASH"},
        )

    def test_table_schema_parser_reads_key_and_attribute_types(self) -> None:
        schema, types = provisioner._table_key_schema(
            {
                "KeySchema": [
                    {"AttributeName": "owner_id", "KeyType": "HASH"},
                    {"AttributeName": "storage_key", "KeyType": "RANGE"},
                ],
                "AttributeDefinitions": [
                    {"AttributeName": "owner_id", "AttributeType": "S"},
                    {"AttributeName": "storage_key", "AttributeType": "S"},
                ],
            }
        )
        self.assertEqual(schema, {"owner_id": "HASH", "storage_key": "RANGE"})
        self.assertEqual(types, {"owner_id": "S", "storage_key": "S"})

    def test_resource_names_can_be_read_from_lightsail_environment(self) -> None:
        args = provisioner.build_parser().parse_args([])
        remote = {
            "AWS_REGION": "us-east-2",
            "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "careerbridge_apps_test",
            "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME": "careerbridge_flows_test",
            "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME": "careerbridge_jobs_test",
            "CAREER_BRIDGE_DOCUMENTS_BUCKET": "documents-example",
        }
        with patch.object(provisioner, "_lightsail_environment", return_value=remote), patch.dict(
            provisioner.os.environ, {}, clear=True
        ):
            names = provisioner._resolve_names(args)
        self.assertEqual(names.region, "us-east-2")
        self.assertEqual(names.applications_table, "careerbridge_apps_test")
        self.assertEqual(names.workflows_table, "careerbridge_flows_test")
        self.assertEqual(names.discovery_table, "careerbridge_jobs_test")
        self.assertEqual(names.documents_bucket, "documents-example")

    def test_default_table_names_use_the_canonical_prefix(self) -> None:
        self.assertEqual(
            provisioner.DEFAULT_APPLICATIONS_TABLE, "careerbridge_applications"
        )
        self.assertEqual(
            provisioner.DEFAULT_WORKFLOWS_TABLE, "careerbridge_workflows"
        )
        self.assertEqual(
            provisioner.DEFAULT_DISCOVERY_TABLE, "careerbridge_job_discovery"
        )

    def test_legacy_hyphenated_table_names_are_rejected_before_aws_calls(self) -> None:
        args = provisioner.build_parser().parse_args(["--applications-only"])
        remote = {
            "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "career-bridge-applications",
        }
        with patch.object(
            provisioner, "_lightsail_environment", return_value=remote
        ), patch.dict(provisioner.os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                provisioner.ProvisioningFailure,
                "can recreate duplicate tables",
            ):
                provisioner._resolve_names(args)

    def test_applications_only_mode_does_not_require_bucket(self) -> None:
        args = provisioner.build_parser().parse_args(["--applications-only"])
        with patch.object(provisioner, "_lightsail_environment", return_value={}), patch.dict(
            provisioner.os.environ, {}, clear=True
        ):
            names = provisioner._resolve_names(args)
        self.assertEqual(names.documents_bucket, "")
        self.assertEqual(
            names.applications_table, provisioner.DEFAULT_APPLICATIONS_TABLE
        )

    def test_async_job_table_enables_expires_at_ttl(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("_enable_discovery_ttl(args.aws_cli, names)", source)
        self.assertIn("Enabled=true,AttributeName=expires_at", source)

    def test_bucket_name_is_never_invented(self) -> None:
        args = provisioner.build_parser().parse_args([])
        with patch.object(provisioner, "_lightsail_environment", return_value={}), patch.dict(
            provisioner.os.environ, {}, clear=True
        ):
            with self.assertRaisesRegex(
                provisioner.ProvisioningFailure,
                "CAREER_BRIDGE_DOCUMENTS_BUCKET",
            ):
                provisioner._resolve_names(args)


if __name__ == "__main__":
    unittest.main()
