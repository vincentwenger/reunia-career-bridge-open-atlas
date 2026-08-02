# Réunia Career Bridge

Réunia Career Bridge is a Flask application for managing a job search from reusable career evidence through application materials, interview practice, and outcome tracking.

## Product areas

- **Foundation** — Career Profile, Baseline Resume, and Career Evidence Library.
- **Jobs & Applications** — discover roles and manage one application workspace per target job.
- **Prepare and Practice** — Interview Preparation and Adaptive Mock Interview.
- **Progress** — manage the Career Action Plan and review Progress & Outcomes.

Baseline Resume creates a reusable **Baseline Resume**. Each job application then receives its own **Application Baseline**, Job-Aligned Resume, and Final Resume. Generated claims remain constrained to Verified Resume Evidence.

## Repository layout

```text
app.py                     Production WSGI entry point
career_bridge/             Shared domain, navigation, and application abstractions
job_discovery/             Source adapters, normalization, ranking, and discovery storage
products/resume_taylor/    Application Builder, resume, translation, and interview-preparation code
products/reunia/           Shared account, dashboard, evidence, practice, review, and analytics code
tests/                     Unit, contract, integration, regression, and browser tests
docs/                      Product, deployment, and validation documentation
reports/validation/        Generated validation output; not canonical documentation
scripts/deployment/        Deployment and validation utilities
```

Resume Taylor templates have one canonical location: `products/resume_taylor/templates/application_builder/`. Do not add parallel copies at the template root.

## Job Discovery

Job Discovery collects publicly accessible postings from supported applicant-tracking systems and Schema.org job pages. It applies domain restrictions, public-address checks, robots and source-policy checks, bounded requests, normalization, deduplication, and posting-age rules.

The shared catalog is managed by administrators or configured catalog managers. Users keep private search preferences, saved and ignored jobs, fit snapshots, and application workspaces. Job Discovery never applies to an employer automatically; a user must explicitly create an internal application workspace.

The initial page renders a lightweight shell and accessible skeleton. A private JSON request loads the selected result page, while detailed strengths, gaps, and evidence provenance load only when requested. See [`docs/job_discovery.md`](docs/job_discovery.md).

## Storage

Production requires durable storage:

```text
CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_APPLICATIONS_TABLE_NAME=careerbridge_applications
CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_WORKFLOWS_TABLE_NAME=careerbridge_workflows
CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=careerbridge_job_discovery
CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND=s3
CAREER_BRIDGE_DOCUMENTS_BUCKET=<bucket-name>
AWS_REGION=us-west-2
```

Application metadata is stored in DynamoDB. Uploaded resumes, generated DOCX/PDF files, large workflow snapshots, resume findings, interview-preparation snapshots, and progress snapshot details are stored in object storage. Application-linked workflows are retained until explicit deletion; temporary workflows may use a TTL.

The legacy-named `CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION` override permits explicitly non-durable workflow or document storage for controlled validation only. It never permits application records to leave DynamoDB and must not be enabled for a normal production deployment.

See [`docs/deployment/lightsail.md`](docs/deployment/lightsail.md) and [`docs/validation/production_storage_migration_check.md`](docs/validation/production_storage_migration_check.md).

## Safety and privacy boundaries

- No automatic employer submission or auto-apply behavior.
- No unsupported resume or interview claims.
- Contact details are excluded from normal proposal-generation context.
- AI Configuration is administrator-only.
- Live Interview Assistance is restricted and is not part of the standard candidate workflow.
- Reusable Career Evidence Library documents default to retention until the user deletes them.

## Tests

Run all discoverable tests:

```bash
python -m unittest discover -s tests -t . -v
```

Run the dependency-aware final checks:

```bash
python tests/run_final_integration_checks.py \
  --json-output reports/validation/final-integration-check-results.json
```

Use `--require-runtime` in CI or deployment validation when missing Flask or browser dependencies must fail the command. See [`tests/README.md`](tests/README.md).
