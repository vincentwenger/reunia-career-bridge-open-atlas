# Production storage migration verification

## Result

**Passed.** Application records now use only DynamoDB in development, testing,
and production configuration. The local application database adapter and its
path configuration have been removed.

Production continues to require:

- Application records: DynamoDB
- Uploaded and generated documents: S3
- Active workflow metadata and optimistic versions: DynamoDB
- Canonical serialized workflow state: S3

The production demo override may permit process-memory workflows or local
documents, but it cannot downgrade application record storage away from
DynamoDB.

## Verified behavior

- `DynamoDBApplicationStore` implements the complete `ApplicationStore` contract.
- Application items contain searchable metadata and object-storage keys rather
  than large document bytes.
- Owner-scoped partitioning prevents cross-owner access.
- Conditional source-job links prevent duplicate application workspaces.
- Application deletion explicitly removes related metadata and document objects.
- Flask tests exercise the production DynamoDB adapter with an in-memory table
  resource; no local database engine is used.
