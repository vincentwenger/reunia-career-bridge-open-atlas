from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError


def _configured_table_name() -> str:
    configured = str(os.getenv("MEETING_SHARES_TABLE_NAME") or "").strip()
    if not configured:
        raise RuntimeError(
            "MEETING_SHARES_TABLE_NAME must be set explicitly before running this script."
        )
    return configured


def main() -> None:
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))
    table_name = _configured_table_name()
    dynamodb = boto3.client("dynamodb", region_name=region)

    table_exists = True
    try:
        dynamodb.describe_table(TableName=table_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        table_exists = False

    if not table_exists:
        dynamodb.create_table(
            TableName=table_name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "share_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "share_id", "KeyType": "HASH"},
            ],
        )
        waiter = dynamodb.get_waiter("table_exists")
        waiter.wait(TableName=table_name)
        print(f"Created table: {table_name}")
    else:
        print(f"Table already exists: {table_name}")

    ttl_status = dynamodb.describe_time_to_live(TableName=table_name).get(
        "TimeToLiveDescription",
        {},
    ).get("TimeToLiveStatus", "DISABLED")
    if ttl_status not in {"ENABLED", "ENABLING"}:
        dynamodb.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={
                "Enabled": True,
                "AttributeName": "expires_at_epoch",
            },
        )
        print("Enabled TTL on expires_at_epoch")
    else:
        print("TTL is already enabled")


if __name__ == "__main__":
    main()
