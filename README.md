# Réunia Career Bridge

Réunia Career Bridge combines the Réunia career workspace with the Application Builder in one Flask application.

## Repository layout

```text
app.py                     Production WSGI entry point
career_bridge/             Shared domain and application abstractions
job_discovery/             Public job-source adapters, normalization, deduplication, ranking, and storage
products/                  Product implementations and web assets
tests/                     All automated tests, fixtures, validators, and test runners
docs/                      Deployment and validation documentation
reports/validation/        Generated validation output
scripts/deployment/        Deployment utilities
```



## Public job discovery

The `job_discovery` package collects publicly accessible postings from configured Greenhouse, Lever, Ashby, and generic Schema.org JSON-LD sources through one `JobSource` protocol. The bounded generic crawler honors robots.txt, uses a descriptive user agent, applies timeouts and per-host rate limits, and caches responses. The feature does not promise literally every job: internal, unlisted, removed, protected, or otherwise inaccessible positions cannot be guaranteed.

Collected postings are stored separately as `CompanySource`, `DiscoveredJob`, and `JobFitSnapshot` records. They do not become Job Applications until the user explicitly chooses to track or start an application. Production uses a dedicated DynamoDB table configured with `CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb` and `CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME`; the existing application-store contract remains unchanged.

See [`docs/job_discovery.md`](docs/job_discovery.md) for configuration and adapter behavior.

## Application Builder storage boundary

Application Builder routes depend on the `WorkflowStore` and `ApplicationStore`
protocols in `products/resume_taylor/resume_tailor/storage.py`. Adapter selection
is controlled by:

```text
CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND=memory|dynamodb
CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=sqlite|dynamodb
```

The workflow adapter supports process memory or a versioned DynamoDB repository.
Both adapters serialize and detach state on load/save, so routes cannot rely on
shared mutable Python references. Application records can use SQLite or the
production DynamoDB repository. Document bytes and large JSON snapshots use a
separate `CareerBridgeObjectStore` selected with:

```text
CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND=local|s3
CAREER_BRIDGE_DOCUMENTS_BUCKET=career-bridge-documents
CAREER_BRIDGE_DOCUMENTS_PREFIX=career-bridge
```

DynamoDB application storage requires `CAREER_BRIDGE_APPLICATIONS_TABLE_NAME`.
DynamoDB workflow storage requires `CAREER_BRIDGE_WORKFLOWS_TABLE_NAME`. Either
DynamoDB adapter requires S3 document storage, and startup fails rather than
falling back to SQLite, memory, or ephemeral local files when configuration is
incomplete.

When `APP_ENV=production`, `ProductionConfig` selects DynamoDB/DynamoDB/S3 as
the baseline and Réunia requires the complete durable combination:

```text
CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND=s3
CAREER_BRIDGE_APPLICATIONS_TABLE_NAME=...
CAREER_BRIDGE_WORKFLOWS_TABLE_NAME=...
CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=...
CAREER_BRIDGE_DOCUMENTS_BUCKET=...
```

The in-memory workflow and SQLite application repositories are therefore not
production defaults and are never selected in a normal production startup. They
remain only as development/testing adapters and for the narrow, explicit
`CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION=true` override. That override
does not relax any other Réunia production checks and emits a prominent warning
that the deployment is demo-only, single-worker/single-node, and vulnerable to
container-replacement data loss.

The application table uses `owner_id` (String) as its partition key and
`storage_key` (String) as its sort key. DynamoDB retains searchable metadata,
S3 object keys, fingerprints, and filenames. Uploaded source resumes, final DOCX
and PDF resumes, resume findings, interview-preparation snapshots, and impact
snapshot details are stored outside DynamoDB. Existing legacy inline DynamoDB
items remain readable so they can be migrated safely.

The workflow table uses `workflow_id` (String) as its partition key. The value is
a SHA-256 digest of the browser workflow key, so owner/session identifiers are
not stored directly. Scratch workflows receive a sliding DynamoDB TTL through
`expires_at`; application-linked workflows omit `expires_at` and are retained by
default until explicit deletion. The configurable scratch TTL is
`CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS` (eight hours by default), while
`CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS=0` means retained. DynamoDB
retains `workflow_type`, `retention_policy`, `version`, `fingerprint`,
`state_json_key`, `updated_at`, and `updated_by_request`; canonical serialized
workflow state is stored in S3. Every mutation must supply the version loaded at
request start and the current Réunia request ID. DynamoDB conditionally matches
that version, increments it, and records the request ID in the same atomic
update. An overlapping browser request receives a recoverable HTTP 409 response
and must reload the latest state.
Scratch workflow JSON uses the separate S3 prefix
`career-bridge/workflow-state/scratch/` so a lifecycle rule can clean objects
left behind when DynamoDB TTL deletes their metadata.

## Run the tests

Run every discoverable unit, integration, regression, contract, and browser test:

```bash
python -m unittest discover -s tests -t . -v
```

Run the final integration-quality orchestration:

```bash
python tests/run_final_integration_checks.py \
  --json-output reports/validation/final-integration-check-results.json
```

Use `--require-runtime` in CI or deployment validation when missing Flask/browser dependencies must fail the command.

More details are in [`tests/README.md`](tests/README.md) and [`docs/validation/`](docs/validation/).
