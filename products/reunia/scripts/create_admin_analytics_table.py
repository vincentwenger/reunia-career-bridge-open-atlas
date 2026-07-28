"""Create or upgrade the DynamoDB table used by Réunia admin analytics.

Run from the project root after AWS credentials are configured:
    python scripts/create_admin_analytics_table.py
"""
from __future__ import annotations

import os
import time

import boto3
from botocore.exceptions import ClientError


INDEX_NAME = os.getenv("ANALYTICS_DATE_INDEX", "analytics_date-index")


def _configured_table_name() -> str:
    configured = str(os.getenv("ANALYTICS_TABLE_NAME") or "").strip()
    if not configured:
        raise RuntimeError(
            "ANALYTICS_TABLE_NAME must be set explicitly before running this script."
        )
    return configured


def main() -> None:
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))
    table_name = _configured_table_name()
    dynamodb = boto3.client("dynamodb", region_name=region)

    try:
        description = dynamodb.describe_table(TableName=table_name)["Table"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        _create_table(dynamodb, table_name)
        print(f"Created DynamoDB table: {table_name} ({region})")
        return

    indexes = {
        item.get("IndexName")
        for item in description.get("GlobalSecondaryIndexes", [])
    }
    if INDEX_NAME in indexes:
        print(f"DynamoDB table and date index already exist: {table_name}")
        return

    print(f"Adding analytics date index {INDEX_NAME} to {table_name}...")
    dynamodb.update_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "session_key", "AttributeType": "S"},
            {"AttributeName": "analytics_date", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexUpdates=[
            {
                "Create": {
                    "IndexName": INDEX_NAME,
                    "KeySchema": [
                        {"AttributeName": "analytics_date", "KeyType": "HASH"},
                        {"AttributeName": "session_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            }
        ],
    )
    dynamodb.get_waiter("table_exists").wait(TableName=table_name)
    _wait_for_index(dynamodb, table_name)
    print(f"Added analytics date index: {INDEX_NAME}")


def _create_table(dynamodb, table_name: str) -> None:
    dynamodb.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "session_key", "AttributeType": "S"},
            {"AttributeName": "analytics_date", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "session_key", "KeyType": "HASH"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": INDEX_NAME,
                "KeySchema": [
                    {"AttributeName": "analytics_date", "KeyType": "HASH"},
                    {"AttributeName": "session_key", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamodb.get_waiter("table_exists").wait(TableName=table_name)
    _wait_for_index(dynamodb, table_name)


def _wait_for_index(dynamodb, table_name: str) -> None:
    waiter = dynamodb.get_waiter("table_exists")
    while True:
        waiter.wait(TableName=table_name)
        table = dynamodb.describe_table(TableName=table_name)["Table"]
        for index in table.get("GlobalSecondaryIndexes", []):
            if index.get("IndexName") == INDEX_NAME and index.get("IndexStatus") == "ACTIVE":
                return
        time.sleep(5)


if __name__ == "__main__":
    main()
