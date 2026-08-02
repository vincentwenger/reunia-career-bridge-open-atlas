# Career Bridge quality-contract overview

This document summarizes the current product contracts. Executable tests remain the source of truth.

## Product structure

Career Foundation is reusable across applications. The remaining tools are grouped into Build Your Application, Prepare and Practice, and Improve and Take Action. The authenticated home page is an action-oriented dashboard rather than a fixed demonstration journey.

## Application boundaries

Each Job Application owns its target role, job description, Application Baseline, tailored resume versions, reports, interview preparation, practice context, actions, status, and outcomes. Creation begins with company, target title, and a posting link or description; tracking details are added later.

## Evidence grounding

Resume generation, Career Translation, Job Discovery, Interview Preparation, Mock Interview feedback, Career Action Plan recommendations, reports, and exports are constrained to Verified Resume Evidence. Unsupported positive claims are removed, downgraded, restored, or blocked.

## Performance contracts

Job Discovery uses a lightweight page shell, asynchronous JSON result fragments, deferred catalog hydration, bulk source/status reads, a prebuilt result index, skeleton states, and phase-level `Server-Timing` metrics. The initial page request does not load active-application documents from S3.

## Storage contracts

- Applications: DynamoDB only.
- Workflow metadata and versions: DynamoDB in production.
- Documents and large snapshots: S3 in production.
- Job Discovery catalog and result data: DynamoDB in production.
- Application deletion cleans related metadata and object-storage files.
- Owner-scoped keys and optimistic concurrency protect isolation and concurrent updates.

## Access contracts

AI Configuration, catalog management, Admin Analytics, incidents, and restricted Live Interview Assistance are server-protected. Normal candidate accounts do not receive model identifiers or configuration controls.

## Validation

Run:

```bash
python tests/run_final_integration_checks.py --require-runtime
```

For evidence-grounding details, see [`no_invented_experience.md`](no_invented_experience.md). For storage details, see [`production_storage_migration_check.md`](production_storage_migration_check.md).
