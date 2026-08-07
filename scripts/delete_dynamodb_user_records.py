#!/usr/bin/env python3
"""Preview or delete DynamoDB records belonging to one Career Bridge user.

The default mode is read-only. Pass ``--delete`` to enable deletion. Unless
``--yes`` is also supplied, destructive mode requires typing an exact
confirmation phrase.

Career Bridge workflow records use hashed identifiers rather than storing the
owner directly. This utility derives those identifiers so Career Foundation and
application workflow records are included. When the Career Bridge documents
bucket is configured, destructive mode also removes that user's Career Bridge
S3 objects unless ``--keep-s3`` is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ProfileNotFound
from dotenv import load_dotenv

CANONICAL_TABLE_PREFIX = "careerbridge_"
WORKFLOW_TABLE_DEFAULT = "careerbridge_workflows"
DOCUMENT_BUCKET_ENV = "CAREER_BRIDGE_DOCUMENTS_BUCKET"
DOCUMENT_PREFIX_ENV = "CAREER_BRIDGE_DOCUMENTS_PREFIX"
DEFAULT_DOCUMENT_PREFIX = "career-bridge"

# Current Career Bridge tables. Environment values override these defaults.
# The async-job table normally shares careerbridge_job_discovery and is deduped.
TABLE_ENV_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("USERS_TABLE_NAME", "careerbridge_users"),
    ("TRANSCRIPTS_TABLE_NAME", "careerbridge_transcripts"),
    ("ACTIONS_TABLE_NAME", "careerbridge_actions"),
    ("ANALYTICS_TABLE_NAME", "careerbridge_app_analytics"),
    ("SUPPORT_REQUESTS_TABLE_NAME", "careerbridge_support_requests"),
    ("KNOWLEDGE_TABLE_NAME", "careerbridge_knowledge"),
    ("CAREER_BRIDGE_APPLICATIONS_TABLE_NAME", "careerbridge_applications"),
    ("CAREER_BRIDGE_WORKFLOWS_TABLE_NAME", "careerbridge_workflows"),
    ("CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME", "careerbridge_job_discovery"),
    ("CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME", "careerbridge_job_discovery"),
)

# Retired tables are inspected only when their environment variables are still
# configured. They are not recreated or assumed to exist.
OPTIONAL_TABLE_ENV_VARS: tuple[str, ...] = (
)

# These top-level attributes identify ownership in current and historical data.
# Exact equality is required; arbitrary text fields are never searched.
IDENTITY_FIELDS: tuple[str, ...] = (
    "user_id",
    "owner_id",
    "job_owner_id",  # async queue ticket stored under a reserved owner partition
    "account_id",
    "created_by_user_id",
    "submitted_by_user_id",
    "user_email",
    "owner_email",
    "email",
)

USER_ALIAS_FIELDS: tuple[str, ...] = (
    "user_id",
    "email",
    "user_email",
    "owner_email",
)

SUMMARY_FIELDS: tuple[str, ...] = (
    "record_type",
    "entity_type",
    "storage_key",
    "application_id",
    "meeting_id",
    "job_id",
    "action_id",
    "item_id",
    "request_id",
    "session_key",
    "derived_workflow_key",
    "title",
    "name",
    "filename",
    "created_at",
)


@dataclass(frozen=True)
class TableSpec:
    name: str
    key_names: tuple[str, ...]
    hash_key: str
    indexes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RecordMatch:
    table_name: str
    key: dict[str, Any]
    matched_fields: tuple[str, ...]
    item: dict[str, Any]


class CleanupError(RuntimeError):
    """Raised when safe record discovery or deletion cannot continue."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or delete all Career Bridge DynamoDB records and configured "
            "S3 objects associated with one exact user ID or email."
        )
    )
    parser.add_argument("user_id", help="Exact Career Bridge user ID or email.")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the records after previewing them. Default is preview only.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation. Requires --delete.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional .env file. Process environment variables take precedence.",
    )
    parser.add_argument(
        "--region",
        help="AWS Region. Defaults to AWS_REGION/AWS_DEFAULT_REGION/us-west-2.",
    )
    parser.add_argument(
        "--profile",
        help="Optional named AWS CLI/profile credential set.",
    )
    parser.add_argument(
        "--table",
        action="append",
        default=[],
        help=(
            "Additional canonical careerbridge_ table to inspect. May be repeated."
        ),
    )
    parser.add_argument(
        "--discover-tables",
        action="store_true",
        help=(
            "Also list DynamoDB tables and inspect every existing careerbridge_ table. "
            "Requires dynamodb:ListTables."
        ),
    )
    parser.add_argument(
        "--bucket",
        help=(
            "Career Bridge S3 document bucket. Defaults to "
            f"{DOCUMENT_BUCKET_ENV}."
        ),
    )
    parser.add_argument(
        "--keep-s3",
        action="store_true",
        help=(
            "Preserve Career Bridge S3 objects. By default --delete removes the "
            "user-scoped Career Bridge document prefixes when a bucket is configured."
        ),
    )
    args = parser.parse_args(argv)
    if args.yes and not args.delete:
        parser.error("--yes requires --delete")
    user_id = str(args.user_id or "").strip()
    if not user_id:
        parser.error("user_id cannot be empty")
    args.user_id = user_id
    return args


def load_environment(explicit_path: Path | None) -> Path | None:
    candidates: list[Path] = []
    if explicit_path is not None:
        candidates.append(explicit_path.expanduser())
    else:
        candidates.extend(
            [
                Path.cwd() / ".env",
                Path(__file__).resolve().parents[1] / ".env",
            ]
        )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            if explicit_path is not None:
                raise CleanupError(f"Environment file not found: {resolved}")
            continue
        load_dotenv(resolved, override=False)
        return resolved
    return None


def normalize_aliases(values: Iterable[Any]) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        aliases.add(normalized)
        if "@" in normalized:
            aliases.add(normalized.lower())
    return aliases


def resolve_configured_table_names(extra_tables: Sequence[str]) -> list[str]:
    names: "OrderedDict[str, None]" = OrderedDict()

    for env_name, default_name in TABLE_ENV_DEFAULTS:
        configured = str(os.getenv(env_name) or "").strip()
        table_name = configured or default_name
        if table_name:
            names[table_name] = None

    for env_name in OPTIONAL_TABLE_ENV_VARS:
        table_name = str(os.getenv(env_name) or "").strip()
        if table_name:
            names[table_name] = None

    # Include any additional configured Career Bridge table variable without
    # requiring this maintenance script to be updated for every new subsystem.
    for env_name, raw_value in sorted(os.environ.items()):
        if not env_name.endswith("TABLE_NAME"):
            continue
        table_name = str(raw_value or "").strip()
        if table_name.startswith(CANONICAL_TABLE_PREFIX):
            names[table_name] = None

    for raw_table_name in extra_tables:
        table_name = str(raw_table_name or "").strip()
        if table_name:
            names[table_name] = None

    invalid = [name for name in names if not name.startswith(CANONICAL_TABLE_PREFIX)]
    if invalid:
        joined = ", ".join(sorted(invalid))
        raise CleanupError(
            "Refusing to inspect noncanonical DynamoDB table name(s): "
            f"{joined}. Expected the {CANONICAL_TABLE_PREFIX!r} prefix."
        )
    return list(names)


def build_session(region: str, profile: str | None):
    kwargs: dict[str, Any] = {"region_name": region}
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)


def discover_prefixed_tables(client: Any) -> list[str]:
    discovered: list[str] = []
    kwargs: dict[str, Any] = {}
    while True:
        response = client.list_tables(**kwargs)
        discovered.extend(
            str(name)
            for name in response.get("TableNames", [])
            if str(name).startswith(CANONICAL_TABLE_PREFIX)
        )
        last_name = response.get("LastEvaluatedTableName")
        if not last_name:
            break
        kwargs["ExclusiveStartTableName"] = last_name
    return sorted(set(discovered))


def describe_table(client: Any, table_name: str) -> TableSpec | None:
    try:
        table = client.describe_table(TableName=table_name)["Table"]
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code") or "")
        if code == "ResourceNotFoundException":
            return None
        raise

    key_schema = table.get("KeySchema", [])
    key_names = tuple(str(entry["AttributeName"]) for entry in key_schema)
    hash_keys = [
        str(entry["AttributeName"])
        for entry in key_schema
        if entry.get("KeyType") == "HASH"
    ]
    if not key_names or not hash_keys:
        raise CleanupError(f"Table {table_name} has an unsupported or missing key schema.")

    indexes: list[tuple[str, str]] = []
    for index in table.get("GlobalSecondaryIndexes", []) or []:
        index_hash = next(
            (
                str(entry["AttributeName"])
                for entry in index.get("KeySchema", [])
                if entry.get("KeyType") == "HASH"
            ),
            "",
        )
        index_name = str(index.get("IndexName") or "")
        if index_hash and index_name:
            indexes.append((index_name, index_hash))

    return TableSpec(
        name=table_name,
        key_names=key_names,
        hash_key=hash_keys[0],
        indexes=tuple(indexes),
    )


def identity_match_fields(item: Mapping[str, Any], aliases: set[str]) -> tuple[str, ...]:
    matched: list[str] = []
    for field in IDENTITY_FIELDS:
        raw_value = item.get(field)
        value = str(raw_value or "").strip()
        if value in aliases or ("@" in value and value.lower() in aliases):
            matched.append(field)
    return tuple(matched)


def record_key(item: Mapping[str, Any], spec: TableSpec) -> dict[str, Any]:
    missing = [name for name in spec.key_names if name not in item]
    if missing:
        raise CleanupError(
            f"Matched item in {spec.name} is missing key attribute(s): "
            + ", ".join(missing)
        )
    return {name: item[name] for name in spec.key_names}


def stable_key(key_value: Mapping[str, Any]) -> str:
    return json.dumps(dict(key_value), sort_keys=True, default=str, separators=(",", ":"))


def workflow_id_for_key(workflow_key: str) -> str:
    return hashlib.sha256(workflow_key.encode("utf-8")).hexdigest()


def application_ids_from_matches(matches: Sequence[RecordMatch]) -> set[str]:
    application_ids: set[str] = set()
    for match in matches:
        application_id = str(match.item.get("application_id") or "").strip()
        if application_id:
            application_ids.add(application_id)
        storage_key = str(match.item.get("storage_key") or "").strip()
        for prefix in (
            "APP#",
            "APPLICATION_MATERIALS#",
            "RESUME_FINDINGS#",
            "INTERVIEW_PREPARATION#",
            "IMPACT#",
        ):
            if storage_key.startswith(prefix):
                derived = storage_key[len(prefix) :].strip()
                if derived:
                    application_ids.add(derived)
                break
    return application_ids


def derived_workflow_keys(
    aliases: set[str], application_ids: Iterable[str]
) -> list[tuple[str, str]]:
    keys: dict[str, str] = {}
    normalized_application_ids = sorted(
        {str(value or "").strip() for value in application_ids if str(value or "").strip()}
    )
    for owner_id in sorted(aliases):
        workflow_keys = [
            f"{owner_id}:career-foundation:translation",
            f"{owner_id}:application:scratch",
            *(
                f"{owner_id}:application:{application_id}"
                for application_id in normalized_application_ids
            ),
        ]
        for workflow_key in workflow_keys:
            keys[workflow_id_for_key(workflow_key)] = workflow_key
    return sorted(keys.items())


def find_derived_workflow_matches(
    table: Any,
    spec: TableSpec,
    aliases: set[str],
    application_ids: Iterable[str],
) -> list[RecordMatch]:
    """Load hashed workflow records that do not contain an owner attribute."""

    if spec.hash_key != "workflow_id" or spec.key_names != ("workflow_id",):
        return []

    matches: list[RecordMatch] = []
    for workflow_id, workflow_key in derived_workflow_keys(aliases, application_ids):
        response = table.get_item(
            Key={"workflow_id": workflow_id},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            continue
        normalized_item = dict(item)
        matches.append(
            RecordMatch(
                table_name=spec.name,
                key=record_key(normalized_item, spec),
                matched_fields=("derived_workflow_id",),
                item={**normalized_item, "derived_workflow_key": workflow_key},
            )
        )
    return sorted(matches, key=lambda match: stable_key(match.key))


def document_owner_namespace(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:32]


def user_document_prefixes(aliases: set[str], configured_prefix: str) -> list[str]:
    root = str(configured_prefix or DEFAULT_DOCUMENT_PREFIX).strip("/") or DEFAULT_DOCUMENT_PREFIX
    prefixes: set[str] = set()
    for owner_id in aliases:
        namespace = document_owner_namespace(owner_id)
        prefixes.add(f"{root}/users/{namespace}/")
        for retention_class in ("scratch", "application", "foundation"):
            prefixes.add(
                f"{root}/workflow-state/{retention_class}/users/{namespace}/"
            )
    return sorted(prefixes)


def extract_document_object_keys(value: Any, configured_prefix: str) -> set[str]:
    root = str(configured_prefix or DEFAULT_DOCUMENT_PREFIX).strip("/") or DEFAULT_DOCUMENT_PREFIX
    accepted_prefix = f"{root}/"
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                visit(nested)
            return
        if isinstance(item, str):
            candidate = item.strip()
            if candidate.startswith(accepted_prefix):
                found.add(candidate)

    visit(value)
    return found


def expand_referenced_s3_objects(
    s3_client: Any,
    bucket: str,
    initial_keys: Iterable[str],
    configured_prefix: str,
) -> tuple[list[str], list[str]]:
    """Follow JSON workflow documents to discover nested document keys."""

    found = set(initial_keys)
    pending = list(found)
    inspected: set[str] = set()
    warnings: list[str] = []
    while pending:
        object_key = pending.pop()
        if object_key in inspected or not object_key.lower().endswith(".json"):
            continue
        inspected.add(object_key)
        try:
            response = s3_client.get_object(Bucket=bucket, Key=object_key)
            content = response["Body"].read()
            payload = json.loads(content.decode("utf-8"))
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code") or "")
            if code in {"AccessDenied", "NoSuchKey", "404", "NotFound"}:
                warnings.append(
                    f"Could not inspect nested S3 references in {object_key}: {code}"
                )
                continue
            raise
        except (BotoCoreError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
            warnings.append(
                f"Could not inspect nested S3 references in {object_key}: {exc}"
            )
            continue

        nested_keys = extract_document_object_keys(payload, configured_prefix)
        for nested_key in nested_keys - found:
            found.add(nested_key)
            pending.append(nested_key)
    return sorted(found), warnings


def list_s3_objects(
    s3_client: Any, bucket: str, prefixes: Sequence[str]
) -> list[str]:
    found: set[str] = set()
    for prefix in prefixes:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        while True:
            response = s3_client.list_objects_v2(**kwargs)
            for item in response.get("Contents", []) or []:
                key = str(item.get("Key") or "").strip()
                if key:
                    found.add(key)
            token = response.get("NextContinuationToken")
            if not token:
                break
            kwargs["ContinuationToken"] = token
    return sorted(found)


def delete_s3_objects(s3_client: Any, bucket: str, object_keys: Sequence[str]) -> int:
    deleted = 0
    for start in range(0, len(object_keys), 1000):
        batch = list(object_keys[start : start + 1000])
        if not batch:
            continue
        response = s3_client.delete_objects(
            Bucket=bucket,
            Delete={
                "Objects": [{"Key": key} for key in batch],
                "Quiet": True,
            },
        )
        errors = response.get("Errors", []) or []
        if errors:
            details = "; ".join(
                f"{error.get('Key')}: {error.get('Code')}" for error in errors
            )
            raise CleanupError(f"S3 deletion failed for {len(errors)} object(s): {details}")
        deleted += len(batch)
    return deleted


def _query_all(table: Any, *, key_name: str, aliases: set[str], index_name: str | None = None):
    for alias in sorted(aliases):
        kwargs: dict[str, Any] = {"KeyConditionExpression": Key(key_name).eq(alias)}
        if index_name:
            kwargs["IndexName"] = index_name
        while True:
            response = table.query(**kwargs)
            yield from response.get("Items", [])
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key


def _secondary_identity_filter(aliases: set[str]):
    expression = None
    values = sorted(aliases)
    for field in IDENTITY_FIELDS:
        candidate = Attr(field).is_in(values)
        expression = candidate if expression is None else expression | candidate
    return expression


def _scan_matching(table: Any, aliases: set[str]):
    kwargs: dict[str, Any] = {
        "FilterExpression": _secondary_identity_filter(aliases),
    }
    while True:
        response = table.scan(**kwargs)
        yield from response.get("Items", [])
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key


def _query_async_queue_tickets(table: Any, aliases: set[str]):
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": (
            Key("owner_id").eq("__CAREER_BRIDGE_ASYNC_QUEUE__")
            & Key("storage_key").begins_with("ASYNC#QUEUED#")
        ),
        "FilterExpression": Attr("job_owner_id").is_in(sorted(aliases)),
    }
    while True:
        response = table.query(**kwargs)
        yield from response.get("Items", [])
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key


def find_table_matches(table: Any, spec: TableSpec, aliases: set[str]) -> list[RecordMatch]:
    found: dict[str, RecordMatch] = {}

    def consider(item: Mapping[str, Any]) -> None:
        normalized_item = dict(item)
        matched_fields = identity_match_fields(normalized_item, aliases)
        if not matched_fields:
            return
        key_value = record_key(normalized_item, spec)
        found[stable_key(key_value)] = RecordMatch(
            table_name=spec.name,
            key=key_value,
            matched_fields=matched_fields,
            item=normalized_item,
        )

    # Query primary and secondary indexes when their partition key directly
    # represents the user. The scan below remains necessary for records such as
    # async queue tickets, whose owner partition is reserved but job_owner_id is
    # the real user.
    queried_sources: set[tuple[str, str | None]] = set()
    if spec.hash_key in IDENTITY_FIELDS:
        queried_sources.add((spec.hash_key, None))
    for index_name, index_hash in spec.indexes:
        if index_hash in IDENTITY_FIELDS:
            queried_sources.add((index_hash, index_name))

    for key_name, index_name in sorted(
        queried_sources, key=lambda value: (value[0], value[1] or "")
    ):
        for item in _query_all(
            table,
            key_name=key_name,
            aliases=aliases,
            index_name=index_name,
        ):
            consider(item)

    # Durable async queue tickets live under a reserved owner partition, so a
    # normal owner_id query does not find them. Query that bounded partition
    # directly rather than scanning the shared Job Discovery table.
    if spec.hash_key == "owner_id" and "storage_key" in spec.key_names:
        for item in _query_async_queue_tickets(table, aliases):
            consider(item)

    configured_users_table = str(
        os.getenv("USERS_TABLE_NAME") or "careerbridge_users"
    ).strip()
    # Scan only when no user-owned key/index can be queried, or when resolving
    # an email alias from the users table. This avoids full scans of the large
    # application, workflow, transcript, knowledge, and discovery tables.
    if (
        (not queried_sources or spec.name == configured_users_table)
        and spec.hash_key != "workflow_id"
    ):
        for item in _scan_matching(table, aliases):
            consider(item)

    return sorted(found.values(), key=lambda match: stable_key(match.key))


def expand_aliases_from_user_records(
    dynamodb: Any,
    client: Any,
    table_names: Sequence[str],
    aliases: set[str],
) -> set[str]:
    users_table = next(
        (name for name in table_names if name == str(os.getenv("USERS_TABLE_NAME") or "careerbridge_users")),
        None,
    )
    if not users_table:
        return aliases
    spec = describe_table(client, users_table)
    if spec is None:
        return aliases
    matches = find_table_matches(dynamodb.Table(users_table), spec, aliases)
    values: list[Any] = list(aliases)
    for match in matches:
        for field in USER_ALIAS_FIELDS:
            values.append(match.item.get(field))
    return normalize_aliases(values)


def item_summary(match: RecordMatch) -> str:
    values: list[str] = []
    for field in SUMMARY_FIELDS:
        value = match.item.get(field)
        normalized = str(value or "").strip()
        if not normalized:
            continue
        if field in match.key and match.key[field] == value:
            continue
        values.append(f"{field}={normalized[:100]}")
        if len(values) >= 4:
            break
    return ", ".join(values)


def print_preview(
    *,
    user_id: str,
    aliases: set[str],
    table_names: Sequence[str],
    missing_tables: Sequence[str],
    matches: Sequence[RecordMatch],
    region: str,
    env_path: Path | None,
    s3_bucket: str,
    s3_prefixes: Sequence[str],
    s3_objects: Sequence[str],
    keep_s3: bool,
) -> None:
    print("Career Bridge user cleanup")
    print(f"Region: {region}")
    print(f"Requested identity: {user_id}")
    if aliases != {user_id}:
        print("Exact identity aliases: " + ", ".join(sorted(aliases)))
    print(f"Environment file: {env_path if env_path else 'not found; using process environment/defaults'}")
    print("Mode: preview")
    print()

    grouped: dict[str, list[RecordMatch]] = {name: [] for name in table_names}
    for match in matches:
        grouped.setdefault(match.table_name, []).append(match)

    for table_name in table_names:
        if table_name in missing_tables:
            print(f"[SKIP] {table_name}: table does not exist")
            continue
        table_matches = grouped.get(table_name, [])
        print(f"[{len(table_matches):4d}] {table_name}")
        for match in table_matches:
            key_text = ", ".join(f"{name}={value}" for name, value in match.key.items())
            matched_text = ", ".join(match.matched_fields)
            summary = item_summary(match)
            detail = f" | {summary}" if summary else ""
            print(f"       key: {key_text} | matched: {matched_text}{detail}")

    print()
    print(f"Total matching DynamoDB records: {len(matches)}")
    if keep_s3:
        print("S3 mode: preserved by --keep-s3")
    elif not s3_bucket:
        print(
            f"S3 mode: skipped because {DOCUMENT_BUCKET_ENV} / --bucket is not configured"
        )
    else:
        print(f"S3 bucket: {s3_bucket}")
        print(f"User-scoped S3 prefixes: {len(s3_prefixes)}")
        print(f"Matching S3 objects: {len(s3_objects)}")
        for object_key in s3_objects:
            print(f"       s3://{s3_bucket}/{object_key}")


def confirm_deletion(user_id: str) -> bool:
    phrase = f"DELETE {user_id}"
    print()
    print("DESTRUCTIVE OPERATION")
    print(f"Type exactly: {phrase}")
    try:
        entered = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return entered == phrase


def delete_matches(dynamodb: Any, matches: Sequence[RecordMatch], users_table_name: str) -> int:
    grouped: dict[str, list[RecordMatch]] = {}
    for match in matches:
        grouped.setdefault(match.table_name, []).append(match)

    # Delete the account record last. If another table fails, the user can still
    # sign in while the operator investigates the incomplete cleanup.
    table_order = sorted(grouped, key=lambda name: (name == users_table_name, name))
    deleted = 0
    for table_name in table_order:
        table = dynamodb.Table(table_name)
        with table.batch_writer() as batch:
            for match in grouped[table_name]:
                batch.delete_item(Key=match.key)
                deleted += 1
        print(f"Deleted {len(grouped[table_name])} record(s) from {table_name}")
    return deleted


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        env_path = load_environment(args.env_file)
        region = str(
            args.region
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or "us-west-2"
        ).strip()
        table_names = resolve_configured_table_names(args.table)
        session = build_session(region, args.profile)
        client = session.client("dynamodb")
        dynamodb = session.resource("dynamodb")

        if args.discover_tables:
            for table_name in discover_prefixed_tables(client):
                if table_name not in table_names:
                    table_names.append(table_name)

        aliases = normalize_aliases([args.user_id])
        aliases = expand_aliases_from_user_records(
            dynamodb,
            client,
            table_names,
            aliases,
        )

        all_matches: list[RecordMatch] = []
        table_specs: dict[str, TableSpec] = {}
        missing_tables: list[str] = []
        errors: list[str] = []
        for table_name in table_names:
            try:
                spec = describe_table(client, table_name)
                if spec is None:
                    missing_tables.append(table_name)
                    continue
                table_specs[table_name] = spec
                all_matches.extend(
                    find_table_matches(dynamodb.Table(table_name), spec, aliases)
                )
            except (BotoCoreError, ClientError, CleanupError) as exc:
                errors.append(f"{table_name}: {exc}")

        # Workflow records intentionally contain only a hashed workflow_id, so
        # identity-field queries cannot find them. Derive the known Foundation,
        # scratch, and application workflow IDs from the exact account aliases
        # and application records found above. This is what prevents a deleted
        # account's Baseline Resume from reappearing after the same email signs up.
        configured_workflow_table = str(
            os.getenv("CAREER_BRIDGE_WORKFLOWS_TABLE_NAME")
            or WORKFLOW_TABLE_DEFAULT
        ).strip()
        workflow_specs = [
            (table_name, spec)
            for table_name, spec in table_specs.items()
            if table_name == configured_workflow_table
            or (spec.hash_key == "workflow_id" and spec.key_names == ("workflow_id",))
        ]
        application_ids = application_ids_from_matches(all_matches)
        for workflow_table_name, workflow_spec in workflow_specs:
            try:
                all_matches.extend(
                    find_derived_workflow_matches(
                        dynamodb.Table(workflow_table_name),
                        workflow_spec,
                        aliases,
                        application_ids,
                    )
                )
            except (BotoCoreError, ClientError, CleanupError) as exc:
                errors.append(f"{workflow_table_name} derived workflows: {exc}")

        deduped_matches: dict[tuple[str, str], RecordMatch] = {}
        for match in all_matches:
            deduped_matches[(match.table_name, stable_key(match.key))] = match
        all_matches = sorted(
            deduped_matches.values(),
            key=lambda match: (match.table_name, stable_key(match.key)),
        )

        s3_bucket = str(args.bucket or os.getenv(DOCUMENT_BUCKET_ENV) or "").strip()
        document_prefix = str(
            os.getenv(DOCUMENT_PREFIX_ENV) or DEFAULT_DOCUMENT_PREFIX
        ).strip("/") or DEFAULT_DOCUMENT_PREFIX
        s3_prefixes = user_document_prefixes(aliases, document_prefix)
        s3_objects = sorted(
            {
                object_key
                for match in all_matches
                for object_key in extract_document_object_keys(
                    match.item, document_prefix
                )
            }
        )
        s3_client = None
        warnings: list[str] = []
        if s3_bucket and not args.keep_s3:
            try:
                s3_client = session.client("s3")
                try:
                    s3_objects = sorted(
                        set(s3_objects)
                        | set(list_s3_objects(s3_client, s3_bucket, s3_prefixes))
                    )
                except ClientError as exc:
                    code = str(exc.response.get("Error", {}).get("Code") or "")
                    if code == "AccessDenied":
                        warnings.append(
                            "The AWS identity cannot list the document bucket; "
                            "cleanup will use object keys referenced by DynamoDB and "
                            "workflow JSON instead."
                        )
                    else:
                        raise
                s3_objects, nested_warnings = expand_referenced_s3_objects(
                    s3_client,
                    s3_bucket,
                    s3_objects,
                    document_prefix,
                )
                warnings.extend(nested_warnings)
            except (BotoCoreError, ClientError, CleanupError) as exc:
                errors.append(f"S3 bucket {s3_bucket}: {exc}")

        print_preview(
            user_id=args.user_id,
            aliases=aliases,
            table_names=table_names,
            missing_tables=missing_tables,
            matches=all_matches,
            region=region,
            env_path=env_path,
            s3_bucket=s3_bucket,
            s3_prefixes=s3_prefixes,
            s3_objects=s3_objects,
            keep_s3=args.keep_s3,
        )

        if warnings:
            print()
            print("Inspection warnings:")
            for warning in warnings:
                print(f"  - {warning}")

        if errors:
            print()
            print("Inspection errors:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            if args.delete:
                print(
                    "Deletion was not started because every configured data store must "
                    "be inspected successfully first.",
                    file=sys.stderr,
                )
            return 3

        if not args.delete:
            if all_matches or s3_objects:
                print()
                print(
                    "Preview complete. Re-run with --delete after reviewing every key."
                )
            return 0

        if not all_matches and not s3_objects:
            print("No matching records or S3 objects were found; nothing was deleted.")
            return 0

        if not args.yes and not confirm_deletion(args.user_id):
            print("Confirmation did not match. No records were deleted.")
            return 4

        deleted_s3 = 0
        if s3_objects and s3_client is not None:
            deleted_s3 = delete_s3_objects(s3_client, s3_bucket, s3_objects)
            print(f"Deleted {deleted_s3} object(s) from S3 bucket {s3_bucket}")

        users_table_name = str(
            os.getenv("USERS_TABLE_NAME") or "careerbridge_users"
        ).strip()
        deleted = delete_matches(dynamodb, all_matches, users_table_name)
        print()
        print(f"Deletion complete: {deleted} DynamoDB record(s) removed.")
        if args.keep_s3:
            print("Career Bridge S3 objects were preserved by --keep-s3.")
        elif s3_bucket:
            print(f"Career Bridge S3 objects removed: {deleted_s3}.")
        else:
            print(
                f"WARNING: {DOCUMENT_BUCKET_ENV} was not configured, so S3 objects "
                "could not be inspected or removed. The hashed workflow records were "
                "still deleted, so the Baseline Resume will not reload for a recreated account."
            )
        return 0

    except (CleanupError, NoCredentialsError, ProfileNotFound) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except (BotoCoreError, ClientError) as exc:
        print(f"AWS ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
