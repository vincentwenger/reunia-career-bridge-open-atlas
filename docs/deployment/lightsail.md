# Réunia Career Bridge — AWS Lightsail container

This deployment package contains the merged product:

- Réunia Career Bridge shell at `/` and `/app`
- Resume-tailoring Application Builder at `/applications/`
- Per-application Interview Preparation at `/applications/interview-preparation`
- Lightsail health check at `/health`

The Application Builder uses the direct `/applications/` route and no Nginx sidecar.

## Data-loss warning

> **WARNING: Redeploying or replacing the Lightsail container can erase Application Builder records.**
> This warning applies when the explicit non-durable storage override permits process-memory
> workflow state or local document storage in production. Those parts rely on
> ephemeral container-node storage, while application records remain in DynamoDB.
> Durable DynamoDB/S3 production storage is required by default and should be
> used whenever retention matters.


## Build and run locally

```bash
docker build -t reunia-career-bridge .
docker run --rm -p 8000:8000 --env-file .env reunia-career-bridge
```

## Lightsail endpoint

- Container port: `8000`
- Public endpoint port: `8000`
- Health check path: `/health`
- Scale: `1` for non-durable storage; `1` or greater for validated persistent storage
- Lightsail container **Command**: leave empty
- Gunicorn workers: `1` is the conservative image default; exactly `1` is required only for non-durable storage
- Gunicorn threads: `4` in the current image

The health endpoint exposes the non-secret Application Builder storage mode for
deployment validation:

```json
{
  "status": "ok",
  "services": ["reunia", "application-builder"],
  "application_builder": {
    "workflow_storage": "dynamodb",
    "application_storage": "dynamodb",
    "document_storage": "s3",
    "durability": "persistent",
    "multi_worker_safe": true,
    "multi_node_safe": true
  }
}
```

This metadata documents the configured storage capabilities; it does not detect
the actual Gunicorn worker count or Lightsail service scale.

Operational policy:

| Storage mode | Lightsail scale | Gunicorn workers |
|---|---:|---:|
| Durable DynamoDB/DynamoDB/S3 | 1 or greater | 1 or greater |
| Non-durable validation memory/DynamoDB/local | exactly 1 | exactly 1 |

Leaving the Lightsail **Command** field empty remains recommended in both modes
so the deployed, reviewed image controls the complete startup command.

Use the same environment variables as the existing Réunia deployment. The
Application Builder additionally reads `OPENAI_API_KEY` and these storage settings:

```text
CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND=memory|dynamodb
CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=memory|dynamodb
CAREER_BRIDGE_APPLICATIONS_TABLE_NAME=career-bridge-applications
CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=career-bridge-job-discovery
JOB_DISCOVERY_AI_MODEL=gpt-5-nano
JOB_DISCOVERY_AI_REASONING_EFFORT=minimal
JOB_DISCOVERY_AI_MAX_OUTPUT_TOKENS=4800
JOB_DISCOVERY_AI_TIMEOUT_SECONDS=20
JOB_DISCOVERY_INDEXED_SEARCH_FALLBACK=true
JOB_DISCOVERY_WEB_SEARCH_MODEL=gpt-5-mini
JOB_DISCOVERY_WEB_SEARCH_TIMEOUT_SECONDS=50
CAREER_BRIDGE_WORKFLOWS_TABLE_NAME=career-bridge-workflows
CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS=28800
CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS=0
CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND=local|s3
CAREER_BRIDGE_DOCUMENTS_LOCAL_PATH=/app/instance/career_bridge_documents
CAREER_BRIDGE_DOCUMENTS_BUCKET=career-bridge-documents
CAREER_BRIDGE_DOCUMENTS_PREFIX=career-bridge
CAREER_BRIDGE_DOCUMENTS_KMS_KEY_ID=
CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION=false
```

Development and testing may retain process-memory workflow state and local
document objects, but application records always use DynamoDB.
`ProductionConfig` defaults to `dynamodb`, `dynamodb`, and `s3`. Selecting
non-durable workflow or document adapters in production requires the explicit
non-durable storage override. Storage is selected through `WorkflowStore`, `ApplicationStore`,
and `CareerBridgeObjectStore` protocols rather than route-level types.

For durable application records and documents, configure all of the following:

```text
CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_APPLICATIONS_TABLE_NAME=career-bridge-applications
CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=career-bridge-job-discovery
CAREER_BRIDGE_WORKFLOWS_TABLE_NAME=career-bridge-workflows
CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS=28800
CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS=0
CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND=s3
CAREER_BRIDGE_DOCUMENTS_BUCKET=career-bridge-documents
CAREER_BRIDGE_DOCUMENTS_PREFIX=career-bridge
AWS_REGION=us-west-2
```

The package deliberately does not fall back to a local application database. A
missing application table name stops startup. In production, a missing workflow
table, S3 bucket, or non-S3 document backend also stops startup.

### Production startup gate

With `APP_ENV=production`, `ProductionConfig` defaults to the durable backend
names and `_validate_production_configuration()` requires all of the following
before any Application Builder repository is initialized:

```text
CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND=s3
CAREER_BRIDGE_APPLICATIONS_TABLE_NAME=<explicit table name>
CAREER_BRIDGE_WORKFLOWS_TABLE_NAME=<explicit table name>
CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=<explicit table name>
CAREER_BRIDGE_DOCUMENTS_BUCKET=<explicit bucket name>
```

Production startup fails when application storage is not `dynamodb`, or when workflow/document storage is non-durable without the explicit override, or
when any required table/bucket name is blank. This check runs inside Réunia's
existing production validator and is independent of the Application Builder
factory, so an unsafe process cannot start far enough to serve traffic.

A controlled validation deployment may deliberately bypass only this Career Bridge
persistence gate with:

```text
CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION=true
```

The override is intentionally narrow: Redis, S3 recorder storage, DynamoDB
actions/analytics/support/knowledge storage, secrets, and every other existing
Réunia production safeguard are still required. Startup logs a prominent unsafe
non-durable-storage warning. Such a deployment must remain at one Gunicorn worker and
one Lightsail node and can lose records during redeployment or replacement. Do
not use the override for normal production traffic.

The live deployment validator expects persistent DynamoDB/S3 health metadata by
default. When validating an intentional non-durable validation deployment, pass
`--allow-demo-storage` (or set the same non-durable storage override variable in the validator
environment).

### Career Bridge workflow table

Provision a DynamoDB table with this primary key:

```text
Partition key: workflow_id (String)
```

No sort key or secondary index is required. Enable DynamoDB TTL on the
`expires_at` Number attribute, but note that the repository writes that attribute
only to temporary records:

- Scratch workflow (`...:application:scratch`): `expires_at` is refreshed on
  save using `CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS`, which defaults to
  28,800 seconds (eight hours). A value between eight and 24 hours is suitable
  for normal temporary work.
- Application-linked workflow (`...:application:<application-id>`): no
  `expires_at` attribute by default. It is retained until explicitly deleted
  with the application. Set `CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS` to
  a positive value only when a deliberate longer application-workflow retention
  window is required; zero means retained.

`CAREER_BRIDGE_WORKFLOW_TTL_SECONDS` remains a backward-compatible alias for the
scratch TTL only. It no longer causes application workflows to expire. On first
load, the repository removes legacy blanket `expires_at` values from retained
application workflows. Deploy this migration before an old application workflow
reaches its previous expiry time; DynamoDB may still asynchronously delete an
item that was already past due before the new code had a chance to clear it.

The table stores only a hashed workflow ID, workflow type, retention policy,
optimistic-lock `version`, fingerprint, S3 `state_json_key`, `updated_at`,
`updated_by_request`, and an optional TTL. A stored item therefore includes
metadata such as:

```json
{
  "version": 12,
  "updated_at": "2026-07-30T16:37:00+00:00",
  "updated_by_request": "REQ-1A2B3C4D5E6F"
}
```

Pydantic models,
dataclasses, optional nested values, collections, and report objects are
serialized as canonical versioned JSON in S3. DOCX/PDF bytes are rejected by the
serializer and must already have been replaced by S3 keys.

Every save requires the version loaded at request start and the current request
ID. The DynamoDB `UpdateItem` condition is `attribute_not_exists(workflow_id)` for a
new workflow or `version = :expected_version` for an existing workflow. The same
atomic update increments `version`, refreshes `updated_at`, and stores
`updated_by_request`. Two overlapping tabs, workers, or nodes therefore cannot
silently overwrite one another: the stale request receives HTTP 409 with the
latest saved request reference and instructions to reload and retry.
`updated_by_request` is a correlation identifier, not a user identity or an
authorization decision.

Legacy workflow items missing request-attribution metadata are conditionally
migrated on first load. That migration also increments the item version, so it
cannot race silently with a user write.

The task role requires these workflow-table actions:
`dynamodb:GetItem`, `dynamodb:UpdateItem`, and `dynamodb:DeleteItem`.

### Career Bridge application table

Provision one DynamoDB table with this primary key:

```text
Partition key: owner_id    (String)
Sort key:      storage_key (String)
```

On-demand billing avoids capacity tuning for typical Career Bridge workloads. No
secondary index is required. The repository stores these item families inside
each owner's partition:

```text
APP#<application_id>
RESUME_FINDINGS#<application_id>
INTERVIEW_PREPARATION#<application_id>
IMPACT#<application_id>
```

The application task role or Lightsail AWS credentials require these actions on
the table: `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:DeleteItem`, and
`dynamodb:Query`. Application deletion explicitly removes the three linked
artifact items to implement explicit cascade cleanup. Application,
resume-findings, interview-preparation, and impact items never receive an
`expires_at` attribute and are not subject to DynamoDB TTL.

DynamoDB items do **not** contain uploaded resume bytes, final DOCX/PDF bytes,
or large serialized report payloads. They retain filenames, fingerprints, and
S3 keys such as `resume_docx_key`, `resume_pdf_key`, and `snapshot_json_key`.
Repository reads hydrate bytes only when a caller requests them; list and normal
request setup paths use metadata-only reads. Legacy inline fields remain
readable for migration. The next successful versioned rewrite stores the state
in S3 and removes obsolete inline workflow fields from the DynamoDB item.

### Career Bridge document bucket

Use a private S3 bucket with Block Public Access enabled. Enable bucket
versioning so replacement or accidental modification can be recovered. Default
server-side encryption is applied on every object write (`AES256`); set
`CAREER_BRIDGE_DOCUMENTS_KMS_KEY_ID` to use a customer-managed KMS key instead.

The object namespace hashes owner and workflow identifiers so email addresses,
session keys, and other direct identifiers do not appear in S3 paths:

```text
career-bridge/users/<owner-hash>/applications/<application-id>/...
career-bridge/users/<owner-hash>/workflows/<workflow-hash>/...
career-bridge/workflow-state/scratch/users/<owner-hash>/<workflow-hash>/...
career-bridge/workflow-state/application/users/<owner-hash>/<workflow-hash>/...
```

Stored objects include uploaded Imported Resumes, final DOCX resumes, final PDF
resumes, canonical versioned workflow-state JSON, resume-finding snapshots,
interview-preparation snapshots, and progress snapshot details. Application or
workflow deletion removes linked current objects; S3 versioning can retain
noncurrent versions according to bucket lifecycle policy.

DynamoDB TTL does not invoke repository cleanup, so an expired scratch workflow
can leave its last S3 state document behind. Configure a lifecycle rule for the
`career-bridge/workflow-state/scratch/` prefix with an expiration window longer
than the configured scratch TTL plus DynamoDB's asynchronous deletion delay. For
an eight-to-24-hour scratch TTL, seven days is a conservative default. Do not
apply that rule to `career-bridge/workflow-state/application/`, which contains
retained application workflows. Also configure lifecycle expiration for
noncurrent versions and incomplete multipart uploads according to the required
recovery window.

The application task role or Lightsail AWS credentials also require
`s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` on:

```text
arn:aws:s3:::career-bridge-documents/career-bridge/*
```

When KMS encryption is configured, grant the corresponding encrypt/decrypt/data
key permissions on the selected KMS key. Do not make the bucket public and do
not place credentials in object metadata or object keys.

## Conditional deployment policy

The explicit non-durable validation configuration (`memory` + `dynamodb` + `local`) keeps
workflow state and documents process-local or container-local. Only that
configuration requires the complete
single-process deployment invariant:

```text
Lightsail scale = 1
Lightsail command override = none
Gunicorn workers = 1
Gunicorn threads = 4
```

The Docker image already supplies the safe startup command:

```text
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 ... app:app
```

When storage is not fully durable, Réunia emits a startup warning that reports
all configured storage backends. A non-durable validation configuration reports workflow memory,
DynamoDB applications, and local documents, followed by a note that workflow or
document storage is not fully durable. The message is emitted once per
application process.

Do not enter a custom command in the Lightsail container configuration while the
service uses the non-durable storage defaults. In particular, a command containing
`gunicorn --workers 2` creates two independent processes and splits process-local
workflow state even when the Lightsail service scale remains `1`.
Leave the Lightsail **Command** field empty so the container uses the image's
existing `CMD`.

When all three production settings are enabled (`dynamodb` workflow storage,
`dynamodb` application storage, and `s3` document storage), Application Builder
workflow state is no longer process-local. Detached serialized loads and
conditional version updates make overlapping workers recoverably conflict-safe.
Réunia's production gate also requires Redis, S3, and DynamoDB for its other
shared services. A validated persistent deployment may therefore use more than
one Gunicorn worker or Lightsail node. The current image still defaults to one
worker as a conservative capacity choice, not as a storage requirement.

The Windows deployment script at `scripts/deployment/upload_to_lightsail.bat`:

1. builds and pushes the image;
2. checks the current Lightsail deployment and stops if any container command
   override is present;
3. enforces scale `1` only when
   `CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION=true`; and
4. otherwise preserves and reports the configured persistent-service scale.

The script never supplies a Lightsail command override. It exits with a nonzero
status and a prominent error banner if command or scale inspection fails. In
non-durable validation mode, a failed scale update or any returned scale other than `1` also stops
the deployment.

## Post-deployment validation

Run the standalone validator after the Lightsail deployment is active. It verifies:

1. `/health` returns HTTP 200 with persistent DynamoDB/S3 storage metadata, or
   non-durable metadata only when explicitly allowed;
2. non-durable storage is constrained to Lightsail scale `1` and one Gunicorn worker,
   while persistent storage accepts any positive scale and worker count;
3. the public application container has no Lightsail command override and the
   Docker image `CMD` starts Gunicorn with valid positive worker/thread counts;
4. an authenticated smoke-test application can be created and retrieved through
   the real Application Builder workflow; and
5. this document contains the prominent redeployment data-loss warning.

The test application is removed after successful retrieval unless
`--keep-test-application` is supplied. Use a dedicated low-privilege validation
account rather than a personal account. The password is read from the environment
and is never printed.

Windows Command Prompt example:

```bat
set CAREER_BRIDGE_BASE_URL=https://career.reunia.app
set DEPLOYMENT_VALIDATION_EMAIL=deployment-validator@example.com
set DEPLOYMENT_VALIDATION_PASSWORD=replace-with-the-account-password
scripts\deployment\validate_lightsail_deployment.bat
```

Equivalent direct Python invocation after setting the same credential
environment variables:

```bash
python scripts/deployment/validate_lightsail_deployment.py \
  --base-url https://career.reunia.app
```

The defaults are region `us-west-2` and service name
`reunia-career-bridge`. Override them with `AWS_REGION`,
`LIGHTSAIL_SERVICE`, `--region`, or `--service-name`. The validator requires a
configured AWS CLI identity with permission to call
`lightsail:GetContainerServices`.

Interview Preparation records always use the DynamoDB `ApplicationStore`. They
include a snapshot of
the exact verified evidence used for generation. A workspace is marked for
regeneration when the company, target role, job description, or verified
evidence changes. Generation requires either completed candidate evidence
confirmation in the active workflow or an attached evidence-reviewed Final
Resume.

## Restricted Live Interview Assistance

Live Interview Assistance is restored but hidden from standard accounts. Access
is enforced by the server for the page, live stream, audio-chunk endpoint, and
cancellation endpoint.

- Administrators configured through `ADMIN_USER_IDS` or `is_admin=true` always have access.
- Approved groups are configured with the comma-separated
  `LIVE_INTERVIEW_ASSISTANCE_GROUPS` variable. Its default groups are
  `career_bridge_beta` and `career_coaches`. A user's DynamoDB record must contain
  a matching `groups` list.
- Specific users can be allowlisted with the comma-separated
  `LIVE_INTERVIEW_ASSISTANCE_USER_IDS` variable.
- In **Admin Analytics → Users**, an administrator can set an individual account
  to **Enabled**, **Disabled**, or **Inherit**. Individual choices are stored in
  `features.live_interview_assistance`.

For authorized users, microphone, speaker, and clipboard behavior continues to
come from **Settings → Live interview assistance**. Disabled sources are rejected
before transcription so they do not create OpenAI transcription usage.

## Application Builder 500/503 storage troubleshooting

Career Translation, Job Applications, Resume Workflow, and Resume Reports all
use the Career Bridge workflow/application persistence boundary. A deployment
can pass the configuration-name startup gate while still failing its first live
DynamoDB read when a named table is absent, exists in another region, has the
wrong primary key, or the deployed credentials cannot access it.

Before building or redeploying, run:

```bat
scripts\deployment\provision_career_bridge_storage.bat
```

The command reads the current Lightsail container environment, creates only
missing Career Bridge resources, and validates these schemas:

```text
Applications: owner_id (partition key), storage_key (sort key)
Workflows:    workflow_id (partition key), TTL attribute expires_at
Discovery:    owner_id (partition key), storage_key (sort key)
Documents:    private S3 bucket with public access blocked and versioning enabled
```

Existing resources are never replaced. A table with an incompatible key schema
causes the command to stop and identify the exact resource that must be replaced
or renamed. The upload script now runs the same storage preflight before building
the image. The live deployment validator also requests all four navbar-backed
workspaces after sign-in, so a deployment with inaccessible storage cannot pass
validation merely because `/health` responds.

The deployed IAM identity still needs the application runtime permissions listed
in the table and bucket sections above. The local AWS identity running the
provisioning command additionally needs permission to describe and create the
resources and to configure DynamoDB TTL, S3 public-access blocking, and bucket
versioning.
