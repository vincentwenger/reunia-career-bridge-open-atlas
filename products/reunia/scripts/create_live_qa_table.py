"""Create the DynamoDB table used by the Live Questions & Answers feed.

Run from the project root after AWS credentials are configured:
    python scripts/create_live_qa_table.py

The application uses:
  * partition key: user_id (String)
  * sort key: entry_id (String)
  * TTL attribute: expires_at (Number, Unix epoch seconds)
"""
from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError


def ensure_ttl(dynamodb, table_name: str) -> None:
    ttl = dynamodb.describe_time_to_live(TableName=table_name).get(
        "TimeToLiveDescription",
        {},
    )
    status = ttl.get("TimeToLiveStatus", "DISABLED")
    attribute_name = ttl.get("AttributeName")

    if status in {"ENABLED", "ENABLING"} and attribute_name == "expires_at":
        print(f"DynamoDB TTL is already enabled on expires_at for: {table_name}")
        return

    if status in {"ENABLED", "ENABLING"} and attribute_name != "expires_at":
        raise RuntimeError(
            f"Table {table_name} already has TTL enabled on {attribute_name!r}; "
            "the application requires 'expires_at'."
        )

    if status == "DISABLING":
        raise RuntimeError(
            f"TTL is currently being disabled for {table_name}. Wait for that "
            "operation to finish, then run this script again."
        )

    dynamodb.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={
            "Enabled": True,
            "AttributeName": "expires_at",
        },
    )
    print(f"Enabled DynamoDB TTL on expires_at for: {table_name}")


def _configured_table_name() -> str:
    configured = str(os.getenv("LIVE_QA_TABLE_NAME") or "").strip()
    if not configured:
        raise RuntimeError(
            "LIVE_QA_TABLE_NAME must be set explicitly before running this script."
        )
    return configured


def main() -> None:
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))
    table_name = _configured_table_name()
    dynamodb = boto3.client("dynamodb", region_name=region)

    try:
        description = dynamodb.describe_table(TableName=table_name)["Table"]
        key_schema = {
            item["AttributeName"]: item["KeyType"]
            for item in description.get("KeySchema", [])
        }
        expected_schema = {"user_id": "HASH", "entry_id": "RANGE"}
        if key_schema != expected_schema:
            raise RuntimeError(
                f"DynamoDB table {table_name} has key schema {key_schema}; "
                f"expected {expected_schema}."
            )
        print(f"DynamoDB table already exists: {table_name}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

        dynamodb.create_table(
            TableName=table_name,
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "entry_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "entry_id", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = dynamodb.get_waiter("table_exists")
        waiter.wait(TableName=table_name)
        print(f"Created DynamoDB table: {table_name} ({region})")

    ensure_ttl(dynamodb, table_name)


if __name__ == "__main__":
    main()
