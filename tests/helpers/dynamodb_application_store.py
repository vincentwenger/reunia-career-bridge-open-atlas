"""Shared DynamoDB application-store test fixtures."""

from __future__ import annotations

from typing import Callable

from products.resume_taylor.resume_tailor.dynamodb_storage import DynamoDBApplicationStore
from products.resume_taylor.resume_tailor.testing_dynamodb import InMemoryApplicationTable


class InMemoryObjectStore:
    """Small S3-compatible object-store test double."""

    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> None:
        del content_type, metadata
        self.items[object_key] = bytes(content)

    def get(self, object_key: str) -> bytes:
        return self.items[object_key]

    def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)
        self.items.pop(object_key, None)


def make_application_store(
    *,
    table: InMemoryApplicationTable | None = None,
    documents: InMemoryObjectStore | None = None,
    id_factory: Callable[[], str] | None = None,
    clock: Callable[[], str] | None = None,
) -> DynamoDBApplicationStore:
    """Build the production DynamoDB adapter against isolated in-memory resources."""

    return DynamoDBApplicationStore(
        {
            "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME": "test-career-bridge-applications",
            "CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge",
        },
        table=table or InMemoryApplicationTable(),
        document_store=documents or InMemoryObjectStore(),
        id_factory=id_factory,
        clock=clock,
    )
