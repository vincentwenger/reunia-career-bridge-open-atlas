from __future__ import annotations

import boto3
from flask import current_app


class DynamoRepository:
    def table(self, config_key: str):
        dynamodb = boto3.resource(
            "dynamodb",
            region_name=current_app.config["AWS_REGION"],
        )
        return dynamodb.Table(current_app.config[config_key])
