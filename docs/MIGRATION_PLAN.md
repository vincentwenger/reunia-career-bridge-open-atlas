# Incremental Migration Plan

## Phase 1 — Foundation and provenance (completed)

- Preserve source snapshots and Git provenance.
- Inventory reusable capabilities.
- Keep both applications runnable independently.

## Phase 2 — Shared job-application domain (completed)

- Replace the generic career-journey aggregate with `JobApplication`.
- Separate reusable candidate data from application-scoped data.
- Add application status and status history.
- Add validated relationships for resumes, evidence, interview preparation, mock sessions, and improvement actions.
- Update shared ports and orchestration to use application IDs.

## Phase 3 — Low-risk adapters

1. Réunia authentication and account-profile adapters.
2. Resume Taylor candidate-profile, career-background, resume, and evidence adapters.
3. Réunia local/S3 document-storage adapter.
4. Resume Taylor engine adapter in its current runtime.
5. Réunia action-tracking adapter using `ImprovementAction`.

For the first implementation, the Resume Taylor adapter may run only inside its own environment or behind a small internal HTTP boundary. Do not force one dependency environment until the OpenAI SDK versions are reconciled.

## Phase 4 — First Career Bridge workflow

- Add a small shared store for job applications and relationships.
- Link existing records by IDs instead of moving them.
- Implement target-job setup, job-fit assessment, resume generation, and application status.
- Build interview preparation from the same target job, resume, and evidence library.

## Phase 5 — Mock interview practice

- Adapt Réunia recording uploads and background jobs.
- Adapt transcription and transcript persistence.
- Add interview-specific scoring rubrics without changing meeting scoring behavior.
- Store every mock interview session under its `JobApplication`.
- Convert scorecard recommendations into `ImprovementAction` records.

## Phase 6 — Operational consolidation

- Reuse admin support and analytics.
- Add cross-product correlation IDs and per-application AI cost attribution.
- Decide whether to unify the OpenAI client, persistence backends, and deployment only after production evidence supports the migration.

## Database rule

Do not rename or repurpose existing tables. Introduce a small Career Bridge store for shared application IDs, relationship IDs, lifecycle status, and status history. Migrate legacy data only with explicit, reversible scripts.
