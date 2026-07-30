# Production storage migration verification

## Result

**Passed.** Normal production startup now uses only the durable Career Bridge
storage combination:

- Application records: DynamoDB
- Uploaded and generated documents: S3
- Active workflow metadata and optimistic versions: DynamoDB
- Canonical serialized workflow state: S3 through the DynamoDB-backed workflow repository

`ProductionConfig` defaults to `dynamodb`, `dynamodb`, and `s3`. The central
production validator rejects `sqlite`, `memory`, or `local` unless
`CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION=true` is deliberately enabled.

## Verified behavior

- `DynamoDBApplicationStore` implements the complete application-store contract.
- Application DynamoDB items exclude resume bytes and retain S3 object keys and metadata.
- `DynamoDBWorkflowStore` loads and saves detached, schema-versioned workflow state.
- Scratch workflows receive DynamoDB TTL through `expires_at`.
- Application-linked workflows are retained by default until explicit deletion.
- Every workflow update conditionally matches the loaded `version`, increments it,
  and records `updated_at` plus `updated_by_request`.
- Concurrent stale updates return a recoverable conflict instead of overwriting state.
- Application deletion cleans the referenced S3 objects.
- Owner-scoped partitioning prevents cross-owner application access.

## Local adapters

`InMemoryWorkflowStore` and `SQLiteApplicationStore` remain in the codebase only
for contract tests, local development, and an explicitly acknowledged demo
configuration. They are not the defaults selected by `ProductionConfig` and are
rejected by normal production validation.

## Automated validation

- Full unittest discovery: 138 tests, 0 failures, 16 dependency-related skips.
- Production storage migration contract: 5/5 passed.
- Application Builder architecture validator: 7/7 passed.
- Python compilation: passed.
