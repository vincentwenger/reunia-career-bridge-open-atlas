"""Preview or delete every DynamoDB record owned by one application user.

The script reads the same ``.env`` file and table-name environment variables as
the application. Deletion is deliberately opt-in: without ``--delete`` it only
reports what it would remove.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]

TABLES: tuple[str, ...] = (
    "TRANSCRIPTS_TABLE_NAME",
    "ACTIONS_TABLE_NAME",
    "ANALYTICS_TABLE_NAME",
    "MEETING_SHARES_TABLE_NAME",
    "LIVE_QA_TABLE_NAME",
    "SUPPORT_REQUESTS_TABLE_NAME",
    "KNOWLEDGE_TABLE_NAME",
    # Delete the login record last so a partial failure leaves the account
    # available for another cleanup attempt.
    "USERS_TABLE_NAME",
)


@dataclass(frozen=True)
class TableMatch:
    table_name: str
    keys: tuple[dict[str, Any], ...]
    lookup_method: str
    missing: bool = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or delete records whose user_id exactly matches one user "
            "across every configured application DynamoDB table."
        )
    )
    parser.add_argument(
        "user_id",
        nargs="?",
        help="Exact user ID/email to clean up. You will be prompted if omitted.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Perform the deletions after previewing them. The default is dry-run.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the typed confirmation. Valid only together with --delete.",
    )
    parser.add_argument(
        "--region",
        help="AWS region override. Defaults to AWS_REGION/AWS_DEFAULT_REGION.",
    )
    parser.add_argument(
        "--profile",
        help="Optional AWS shared-credentials profile name.",
    )
    args = parser.parse_args(argv)
    if args.yes and not args.delete:
        parser.error("--yes can only be used together with --delete")
    return args


def configured_tables() -> list[str]:
    names: list[str] = []
    missing_variables: list[str] = []
    for variable in TABLES:
        configured = str(os.getenv(variable) or "").strip()
        if not configured:
            missing_variables.append(variable)
            continue
        if configured not in names:
            names.append(configured)

    if missing_variables:
        missing = ", ".join(missing_variables)
        raise RuntimeError(
            "Explicit DynamoDB table names are required. Missing environment "
            f"variable(s): {missing}."
        )
    return names


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code") or "")


def _index_for_user_id(description: dict[str, Any]) -> str | None | bool:
    """Return False for the base table, an index name for a GSI, or None."""
    primary = description.get("KeySchema", [])
    if any(
        item.get("AttributeName") == "user_id" and item.get("KeyType") == "HASH"
        for item in primary
    ):
        return False

    for index in description.get("GlobalSecondaryIndexes", []):
        if any(
            item.get("AttributeName") == "user_id" and item.get("KeyType") == "HASH"
            for item in index.get("KeySchema", [])
        ):
            return str(index["IndexName"])
    return None


def _projection(key_names: Iterable[str]) -> tuple[str, dict[str, str]]:
    unique_names = list(dict.fromkeys([*key_names, "user_id"]))
    aliases = {f"#field_{index}": name for index, name in enumerate(unique_names)}
    return ", ".join(aliases), aliases


def collect_table_matches(client: Any, table_name: str, user_id: str) -> TableMatch:
    try:
        description = client.describe_table(TableName=table_name)["Table"]
    except ClientError as exc:
        if _error_code(exc) == "ResourceNotFoundException":
            return TableMatch(table_name, (), "not found", missing=True)
        raise

    key_names = [item["AttributeName"] for item in description.get("KeySchema", [])]
    if not key_names:
        raise RuntimeError(f"DynamoDB returned no key schema for table {table_name!r}.")

    projection, aliases = _projection(key_names)
    user_alias = next(alias for alias, name in aliases.items() if name == "user_id")
    common: dict[str, Any] = {
        "TableName": table_name,
        "ProjectionExpression": projection,
        "ExpressionAttributeNames": aliases,
        "ExpressionAttributeValues": {":user_id": {"S": user_id}},
    }

    index_name = _index_for_user_id(description)
    if index_name is False:
        operation = client.query
        request = {
            **common,
            "KeyConditionExpression": f"{user_alias} = :user_id",
            "ConsistentRead": True,
        }
        method = "partition-key query"
    elif index_name:
        operation = client.query
        request = {
            **common,
            "IndexName": index_name,
            "KeyConditionExpression": f"{user_alias} = :user_id",
        }
        method = f"index query ({index_name})"
    else:
        operation = client.scan
        request = {
            **common,
            "FilterExpression": f"{user_alias} = :user_id",
            "ConsistentRead": True,
        }
        method = "filtered table scan"

    keys: list[dict[str, Any]] = []
    while True:
        response = operation(**request)
        for item in response.get("Items", []):
            keys.append({name: item[name] for name in key_names})
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        request["ExclusiveStartKey"] = last_key

    return TableMatch(table_name, tuple(keys), method)


def delete_matches(client: Any, match: TableMatch, user_id: str) -> tuple[int, int]:
    deleted = 0
    changed = 0
    for key in match.keys:
        try:
            client.delete_item(
                TableName=match.table_name,
                Key=key,
                ConditionExpression="#user_id = :user_id",
                ExpressionAttributeNames={"#user_id": "user_id"},
                ExpressionAttributeValues={":user_id": {"S": user_id}},
            )
            deleted += 1
        except ClientError as exc:
            if _error_code(exc) == "ConditionalCheckFailedException":
                changed += 1
                continue
            raise
    return deleted, changed


def _display_key(key: dict[str, Any]) -> str:
    simple = {
        name: next(iter(value.values())) if len(value) == 1 else value
        for name, value in key.items()
    }
    return json.dumps(simple, ensure_ascii=False, sort_keys=True)


def _prompt_for_user_id() -> str:
    if not sys.stdin.isatty():
        raise ValueError("user_id is required when input is not interactive")
    return input("User ID/email to clean up: ").strip()


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    args = parse_args(argv)
    try:
        tables = configured_tables()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        user_id = str(args.user_id or "").strip() or _prompt_for_user_id()
    except (EOFError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not user_id:
        print("Error: user_id cannot be empty.", file=sys.stderr)
        return 2

    region = (
        args.region
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-west-2"
    )
    try:
        session = boto3.Session(profile_name=args.profile, region_name=region)
        client = session.client("dynamodb")
    except BotoCoreError as exc:
        print(f"Error creating the AWS client: {exc}", file=sys.stderr)
        return 1

    print(f"User ID: {user_id}")
    print(f"AWS region: {region}")
    print(f"Mode: {'DELETE' if args.delete else 'DRY RUN'}")
    print()

    matches: list[TableMatch] = []
    try:
        for table_name in tables:
            match = collect_table_matches(client, table_name, user_id)
            matches.append(match)
            if match.missing:
                print(f"[missing] {table_name}")
            else:
                print(
                    f"[{len(match.keys):>5}] {table_name} "
                    f"via {match.lookup_method}"
                )
                for key in match.keys[:5]:
                    print(f"        {_display_key(key)}")
                if len(match.keys) > 5:
                    print(f"        ... and {len(match.keys) - 5} more")
    except (BotoCoreError, ClientError, RuntimeError) as exc:
        print(f"\nPreview failed; nothing was deleted: {exc}", file=sys.stderr)
        return 1

    total = sum(len(match.keys) for match in matches)
    print(f"\nTotal matching records: {total}")
    if not args.delete:
        print("Dry run only. Re-run with --delete to remove these records.")
        return 0
    if total == 0:
        print("Nothing to delete.")
        return 0

    if not args.yes:
        expected = f"DELETE {user_id}"
        print("\nDeletion is permanent and may affect production data.")
        try:
            confirmation = input(f"Type {expected!r} to continue: ").strip()
        except EOFError:
            confirmation = ""
        if confirmation != expected:
            print("Confirmation did not match. Nothing was deleted.")
            return 2

    deleted_total = 0
    changed_total = 0
    try:
        for match in matches:
            deleted, changed = delete_matches(client, match, user_id)
            deleted_total += deleted
            changed_total += changed
            if deleted or changed:
                suffix = f", {changed} changed/skipped" if changed else ""
                print(f"Deleted {deleted} from {match.table_name}{suffix}")
    except (BotoCoreError, ClientError) as exc:
        print(
            f"\nDeletion stopped after {deleted_total} records; cleanup may be partial: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"\nDeleted {deleted_total} records for {user_id}.")
    if changed_total:
        print(
            f"Skipped {changed_total} records because their user_id changed after preview."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
