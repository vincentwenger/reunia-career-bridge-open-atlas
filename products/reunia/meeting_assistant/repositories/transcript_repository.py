from __future__ import annotations

from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from flask import current_app

from meeting_assistant.repositories.base import DynamoRepository
from meeting_assistant.utils.exceptions import ResourceNotFoundError
from meeting_assistant.utils.json_parsing import normalize_transcript_item


class TranscriptRepository(DynamoRepository):
    def _table(self):
        return self.table("TRANSCRIPTS_TABLE_NAME")

    def create(self, item: dict[str, Any]) -> None:
        self._table().put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(meeting_id) AND attribute_not_exists(#timestamp)"
            ),
            ExpressionAttributeNames={"#timestamp": "timestamp"},
        )

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        table = self._table()
        query_args = {
            "IndexName": current_app.config["TRANSCRIPTS_USER_INDEX"],
            "KeyConditionExpression": Key("user_id").eq(user_id),
        }
        items: list[dict[str, Any]] = []

        while True:
            response = table.query(**query_args)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_args["ExclusiveStartKey"] = last_key

        return [normalize_transcript_item(item) for item in items]

    def list_summaries_for_user(self, user_id: str) -> list[dict[str, Any]]:
        query_args = {
            "IndexName": current_app.config["TRANSCRIPTS_USER_INDEX"],
            "KeyConditionExpression": Key("user_id").eq(user_id),
            **self._summary_projection(),
        }
        return self._collect("query", query_args)

    def list_all_summaries(self) -> list[dict[str, Any]]:
        return self._collect("scan", self._summary_projection())

    def _collect(
        self,
        operation: str,
        arguments: dict[str, Any],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        request_args = dict(arguments)
        table = self._table()
        while True:
            response = getattr(table, operation)(**request_args)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            request_args["ExclusiveStartKey"] = last_key

    @staticmethod
    def _summary_projection() -> dict[str, Any]:
        return {
            "ProjectionExpression": (
                "#user_id, #meeting_id, #timestamp, #meeting_name, "
                "#prepared_meeting_title"
            ),
            "ExpressionAttributeNames": {
                "#user_id": "user_id",
                "#meeting_id": "meeting_id",
                "#timestamp": "timestamp",
                "#meeting_name": "meeting_name",
                "#prepared_meeting_title": "prepared_meeting_title",
            },
        }

    def get_owned(
        self,
        user_id: str,
        meeting_id: str,
        timestamp: str,
    ) -> dict[str, Any]:
        response = self._table().get_item(
            Key={"meeting_id": meeting_id, "timestamp": timestamp},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item or str(item.get("user_id") or "") != user_id:
            raise ResourceNotFoundError("Meeting not found.")
        return normalize_transcript_item(item)

    def update_owned(
        self,
        user_id: str,
        meeting_id: str,
        timestamp: str,
        fields: dict[str, Any],
    ) -> None:
        names = {
            "#meeting_id": "meeting_id",
            "#timestamp": "timestamp",
            "#owner": "user_id",
        }
        values: dict[str, Any] = {":owner": user_id}
        assignments = []

        for index, (field, value) in enumerate(fields.items()):
            name_token = f"#field_{index}"
            value_token = f":value_{index}"
            names[name_token] = field
            values[value_token] = value
            assignments.append(f"{name_token} = {value_token}")

        try:
            self._table().update_item(
                Key={"meeting_id": meeting_id, "timestamp": timestamp},
                UpdateExpression="SET " + ", ".join(assignments),
                ConditionExpression=(
                    "attribute_exists(#meeting_id) AND attribute_exists(#timestamp) "
                    "AND #owner = :owner"
                ),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ResourceNotFoundError("Meeting not found.") from exc
            raise

    def delete_owned(self, user_id: str, meeting_id: str, timestamp: str) -> None:
        try:
            response = self._table().delete_item(
                Key={"meeting_id": meeting_id, "timestamp": timestamp},
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames={"#owner": "user_id"},
                ExpressionAttributeValues={":owner": user_id},
                ReturnValues="ALL_OLD",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ResourceNotFoundError("Meeting not found.") from exc
            raise

        if not response.get("Attributes"):
            raise ResourceNotFoundError("Meeting not found.")
