"""Create the DynamoDB metadata table used by Document Library.

Run from the project root after AWS credentials are configured:
    python scripts/create_knowledge_table.py
"""
from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError


def _configured_table_name() -> str:
    configured = str(os.getenv("KNOWLEDGE_TABLE_NAME") or "").strip()
    if not configured:
        raise RuntimeError(
            "KNOWLEDGE_TABLE_NAME must be set explicitly before running this script."
        )
    return configured


def main() -> None:
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))
    table_name = _configured_table_name()
    dynamodb = boto3.client("dynamodb", region_name=region)

    try:
        dynamodb.describe_table(TableName=table_name)
        print(f"DynamoDB table already exists: {table_name}")
        return
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    dynamodb.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "item_id", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "item_id", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamodb.get_waiter("table_exists").wait(TableName=table_name)
    print(f"Created DynamoDB table: {table_name} ({region})")


if __name__ == "__main__":
    main()
