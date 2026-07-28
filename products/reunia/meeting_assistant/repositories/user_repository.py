from __future__ import annotations

from typing import Any

from meeting_assistant.repositories.base import DynamoRepository


class UserRepository(DynamoRepository):
    def _table(self):
        return self.table("USERS_TABLE_NAME")

    def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        response = self._table().get_item(Key={"user_id": user_id})
        return response.get("Item")

    def create(self, user: dict[str, Any]) -> None:
        self._table().put_item(
            Item=user,
            ConditionExpression="attribute_not_exists(user_id)",
        )

    def list_all(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {}
        while True:
            response = self._table().scan(
                ProjectionExpression=(
                    "user_id, email, full_name, created_at, is_admin"
                ),
                **scan_kwargs,
            )
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        return items

    def update_fields(self, user_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        if not fields:
            return {}

        names = {}
        values = {}
        assignments = []

        for index, (field, value) in enumerate(fields.items()):
            name_token = f"#field_{index}"
            value_token = f":value_{index}"
            names[name_token] = field
            values[value_token] = value
            assignments.append(f"{name_token} = {value_token}")

        response = self._table().update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET " + ", ".join(assignments),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(user_id)",
            ReturnValues="UPDATED_NEW",
        )
        return response.get("Attributes", {})

    def update_settings(self, user_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        response = self._table().update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET settings = :settings",
            ExpressionAttributeValues={":settings": settings},
            ConditionExpression="attribute_exists(user_id)",
            ReturnValues="UPDATED_NEW",
        )
        return response.get("Attributes", {})
