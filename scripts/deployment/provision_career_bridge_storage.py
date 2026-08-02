#!/usr/bin/env python3
"""Check or provision AWS storage required by Réunia Career Bridge.

The script intentionally uses the AWS CLI already required by the Lightsail
upload workflow. It can read resource names from the current Lightsail
container environment, verify DynamoDB key schemas, create missing tables, turn
on workflow TTL, and configure a private versioned S3 document bucket.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Sequence

DEFAULT_REGION = "us-west-2"
DEFAULT_SERVICE_NAME = "reunia-career-bridge"
DEFAULT_APPLICATIONS_TABLE = "career-bridge-applications"
DEFAULT_WORKFLOWS_TABLE = "career-bridge-workflows"
DEFAULT_DISCOVERY_TABLE = "career-bridge-job-discovery"


class ProvisioningFailure(RuntimeError):
    """Raised when an AWS storage prerequisite cannot be verified or created."""


@dataclass(frozen=True)
class StorageNames:
    region: str
    applications_table: str
    workflows_table: str
    discovery_table: str
    documents_bucket: str


@dataclass(frozen=True)
class AwsCommandResult:
    returncode: int
    stdout: str
    stderr: str


def _run_command(arguments: Sequence[str]) -> AwsCommandResult:
    completed = subprocess.run(
        list(arguments),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return AwsCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def _aws(
    aws_cli: str,
    region: str,
    arguments: Sequence[str],
    *,
    allow_failure: bool = False,
) -> AwsCommandResult:
    command = [aws_cli, *arguments, "--region", region]
    result = _run_command(command)
    if result.returncode and not allow_failure:
        detail = result.stderr or result.stdout or "AWS CLI returned no details."
        raise ProvisioningFailure(f"AWS command failed: {' '.join(command)}\n{detail}")
    return result


def _aws_json(
    aws_cli: str,
    region: str,
    arguments: Sequence[str],
    *,
    allow_failure: bool = False,
) -> tuple[AwsCommandResult, dict[str, Any]]:
    result = _aws(
        aws_cli,
        region,
        [*arguments, "--output", "json"],
        allow_failure=allow_failure,
    )
    if result.returncode:
        return result, {}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProvisioningFailure(
            f"AWS CLI returned invalid JSON for {' '.join(arguments)}."
        ) from exc
    if not isinstance(payload, dict):
        raise ProvisioningFailure(
            f"AWS CLI returned an unexpected response for {' '.join(arguments)}."
        )
    return result, payload


def _lightsail_environment(
    aws_cli: str,
    region: str,
    service_name: str,
    container_name: str | None,
) -> dict[str, str]:
    result, payload = _aws_json(
        aws_cli,
        region,
        [
            "lightsail",
            "get-container-services",
            "--service-name",
            service_name,
        ],
        allow_failure=True,
    )
    if result.returncode:
        return {}
    services = payload.get("containerServices") or []
    if not services:
        return {}
    service = services[0] if isinstance(services[0], dict) else {}
    deployment = service.get("currentDeployment") or {}
    containers = deployment.get("containers") or {}
    if not isinstance(containers, dict) or not containers:
        return {}
    selected = container_name
    if not selected:
        public_endpoint = service.get("publicEndpoint") or {}
        selected = str(public_endpoint.get("containerName") or "").strip()
    if not selected or selected not in containers:
        selected = next(iter(containers))
    container = containers.get(selected) or {}
    environment = container.get("environment") or {}
    if not isinstance(environment, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in environment.items()
        if str(key).strip() and value is not None
    }


def _resolve_names(args: argparse.Namespace) -> StorageNames:
    preliminary_region = (
        args.region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or DEFAULT_REGION
    ).strip()
    remote_environment = _lightsail_environment(
        args.aws_cli,
        preliminary_region,
        args.service_name,
        args.container_name,
    )

    def value(cli_value: str | None, key: str, default: str = "") -> str:
        return str(
            cli_value
            or os.environ.get(key)
            or remote_environment.get(key)
            or default
        ).strip()

    region = value(args.region, "AWS_REGION", preliminary_region)
    bucket = value(args.documents_bucket, "CAREER_BRIDGE_DOCUMENTS_BUCKET")
    if not bucket and not args.applications_only:
        raise ProvisioningFailure(
            "CAREER_BRIDGE_DOCUMENTS_BUCKET is missing. Set it in the local "
            "environment, the Lightsail container environment, or pass "
            "--documents-bucket. S3 bucket names are globally unique, so the "
            "script does not invent one."
        )
    return StorageNames(
        region=region,
        applications_table=value(
            args.applications_table,
            "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME",
            DEFAULT_APPLICATIONS_TABLE,
        ),
        workflows_table=value(
            args.workflows_table,
            "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME",
            DEFAULT_WORKFLOWS_TABLE,
        ),
        discovery_table=value(
            args.discovery_table,
            "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME",
            DEFAULT_DISCOVERY_TABLE,
        ),
        documents_bucket=bucket,
    )


def _is_not_found(result: AwsCommandResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}".casefold()
    return any(
        marker in text
        for marker in (
            "resourcenotfoundexception",
            "not found",
            "nosuchbucket",
            "404",
        )
    )


def _expected_key_schema(partition_key: str, sort_key: str | None = None) -> dict[str, str]:
    expected = {partition_key: "HASH"}
    if sort_key:
        expected[sort_key] = "RANGE"
    return expected


def _table_key_schema(table: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    key_schema = {
        str(item.get("AttributeName") or ""): str(item.get("KeyType") or "")
        for item in table.get("KeySchema") or []
        if isinstance(item, dict)
    }
    attribute_types = {
        str(item.get("AttributeName") or ""): str(item.get("AttributeType") or "")
        for item in table.get("AttributeDefinitions") or []
        if isinstance(item, dict)
    }
    return key_schema, attribute_types


def _verify_or_create_table(
    aws_cli: str,
    names: StorageNames,
    table_name: str,
    *,
    partition_key: str,
    sort_key: str | None,
    create_missing: bool,
) -> None:
    result, payload = _aws_json(
        aws_cli,
        names.region,
        ["dynamodb", "describe-table", "--table-name", table_name],
        allow_failure=True,
    )
    if result.returncode:
        if not _is_not_found(result):
            raise ProvisioningFailure(
                f"Could not inspect DynamoDB table {table_name!r}: "
                f"{result.stderr or result.stdout}"
            )
        if not create_missing:
            raise ProvisioningFailure(
                f"DynamoDB table {table_name!r} does not exist in {names.region}. "
                "Run again with --create-missing."
            )
        definitions = [
            f"AttributeName={partition_key},AttributeType=S",
        ]
        schema = [f"AttributeName={partition_key},KeyType=HASH"]
        if sort_key:
            definitions.append(f"AttributeName={sort_key},AttributeType=S")
            schema.append(f"AttributeName={sort_key},KeyType=RANGE")
        _aws(
            aws_cli,
            names.region,
            [
                "dynamodb",
                "create-table",
                "--table-name",
                table_name,
                "--billing-mode",
                "PAY_PER_REQUEST",
                "--attribute-definitions",
                *definitions,
                "--key-schema",
                *schema,
            ],
        )
        _aws(
            aws_cli,
            names.region,
            ["dynamodb", "wait", "table-exists", "--table-name", table_name],
        )
        _, payload = _aws_json(
            aws_cli,
            names.region,
            ["dynamodb", "describe-table", "--table-name", table_name],
        )
        print(f"CREATED DynamoDB table: {table_name}")

    table = payload.get("Table") or {}
    actual_schema, attribute_types = _table_key_schema(table)
    expected_schema = _expected_key_schema(partition_key, sort_key)
    expected_types = {key: "S" for key in expected_schema}
    if actual_schema != expected_schema or any(
        attribute_types.get(key) != expected_type
        for key, expected_type in expected_types.items()
    ):
        raise ProvisioningFailure(
            f"DynamoDB table {table_name!r} has the wrong primary key. "
            f"Expected {expected_schema} with String attributes; found "
            f"{actual_schema} with {attribute_types}. Create a dedicated table "
            "with the documented schema and update the Lightsail environment."
        )
    status = str(table.get("TableStatus") or "")
    if status != "ACTIVE":
        raise ProvisioningFailure(
            f"DynamoDB table {table_name!r} is {status or 'not active'}."
        )
    print(f"READY DynamoDB table: {table_name}")


def _enable_workflow_ttl(aws_cli: str, names: StorageNames) -> None:
    result, payload = _aws_json(
        aws_cli,
        names.region,
        [
            "dynamodb",
            "describe-time-to-live",
            "--table-name",
            names.workflows_table,
        ],
        allow_failure=True,
    )
    if result.returncode:
        raise ProvisioningFailure(
            f"Could not inspect TTL on {names.workflows_table!r}: "
            f"{result.stderr or result.stdout}"
        )
    description = payload.get("TimeToLiveDescription") or {}
    status = str(description.get("TimeToLiveStatus") or "")
    attribute = str(description.get("AttributeName") or "")
    if status in {"ENABLED", "ENABLING"} and attribute == "expires_at":
        print(f"READY workflow TTL: {names.workflows_table}.expires_at")
        return
    if status in {"ENABLED", "ENABLING"} and attribute != "expires_at":
        raise ProvisioningFailure(
            f"Workflow table TTL is already configured on {attribute!r}; expected "
            "'expires_at'."
        )
    _aws(
        aws_cli,
        names.region,
        [
            "dynamodb",
            "update-time-to-live",
            "--table-name",
            names.workflows_table,
            "--time-to-live-specification",
            "Enabled=true,AttributeName=expires_at",
        ],
    )
    print(f"ENABLED workflow TTL: {names.workflows_table}.expires_at")


def _verify_or_create_bucket(
    aws_cli: str,
    names: StorageNames,
    *,
    create_missing: bool,
) -> None:
    head = _aws(
        aws_cli,
        names.region,
        ["s3api", "head-bucket", "--bucket", names.documents_bucket],
        allow_failure=True,
    )
    if head.returncode:
        if not create_missing:
            raise ProvisioningFailure(
                f"S3 bucket {names.documents_bucket!r} is missing or inaccessible. "
                "Run again with --create-missing after confirming the bucket name."
            )
        create_arguments = [
            "s3api",
            "create-bucket",
            "--bucket",
            names.documents_bucket,
        ]
        if names.region != "us-east-1":
            create_arguments.extend(
                [
                    "--create-bucket-configuration",
                    f"LocationConstraint={names.region}",
                ]
            )
        _aws(aws_cli, names.region, create_arguments)
        print(f"CREATED S3 bucket: {names.documents_bucket}")

    _aws(
        aws_cli,
        names.region,
        [
            "s3api",
            "put-public-access-block",
            "--bucket",
            names.documents_bucket,
            "--public-access-block-configuration",
            "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true",
        ],
    )
    _aws(
        aws_cli,
        names.region,
        [
            "s3api",
            "put-bucket-versioning",
            "--bucket",
            names.documents_bucket,
            "--versioning-configuration",
            "Status=Enabled",
        ],
    )
    print(f"READY private versioned S3 bucket: {names.documents_bucket}")


def _print_environment(names: StorageNames) -> None:
    print("\nUse these Lightsail environment values:")
    print("CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=dynamodb")
    print("CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND=dynamodb")
    print("CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb")
    print("CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND=s3")
    print(f"CAREER_BRIDGE_APPLICATIONS_TABLE_NAME={names.applications_table}")
    print(f"CAREER_BRIDGE_WORKFLOWS_TABLE_NAME={names.workflows_table}")
    print(f"CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME={names.discovery_table}")
    print(f"CAREER_BRIDGE_DOCUMENTS_BUCKET={names.documents_bucket}")
    print(f"AWS_REGION={names.region}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aws-cli", default="aws")
    parser.add_argument("--region")
    parser.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    parser.add_argument("--container-name")
    parser.add_argument("--applications-table")
    parser.add_argument("--workflows-table")
    parser.add_argument("--discovery-table")
    parser.add_argument("--documents-bucket")
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create missing tables/bucket. Existing resources are never replaced.",
    )
    parser.add_argument(
        "--applications-only",
        action="store_true",
        help=(
            "Validate/create only the mandatory DynamoDB application table. "
            "Use for a controlled non-durable validation deployment with memory workflows and "
            "local documents."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        names = _resolve_names(args)
        _verify_or_create_table(
            args.aws_cli,
            names,
            names.applications_table,
            partition_key="owner_id",
            sort_key="storage_key",
            create_missing=args.create_missing,
        )
        if not args.applications_only:
            _verify_or_create_table(
                args.aws_cli,
                names,
                names.workflows_table,
                partition_key="workflow_id",
                sort_key=None,
                create_missing=args.create_missing,
            )
            _enable_workflow_ttl(args.aws_cli, names)
            _verify_or_create_table(
                args.aws_cli,
                names,
                names.discovery_table,
                partition_key="owner_id",
                sort_key="storage_key",
                create_missing=args.create_missing,
            )
            _verify_or_create_bucket(
                args.aws_cli,
                names,
                create_missing=args.create_missing,
            )
            _print_environment(names)
        else:
            print("\nUse this mandatory Lightsail environment value:")
            print("CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=dynamodb")
            print(
                f"CAREER_BRIDGE_APPLICATIONS_TABLE_NAME={names.applications_table}"
            )
            print(f"AWS_REGION={names.region}")
    except ProvisioningFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("\nSUCCESS: Career Bridge AWS storage is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
