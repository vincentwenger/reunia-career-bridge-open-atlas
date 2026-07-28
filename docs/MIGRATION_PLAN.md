# Incremental Migration Plan

## Phase 1 — Foundation (completed here)

- Preserve source snapshots and provenance.
- Inventory reusable capabilities.
- Define shared domain and ports.
- Keep both applications runnable independently.

## Phase 2 — Low-risk adapters

1. Réunia authentication adapter.
2. Réunia user-profile adapter.
3. Réunia local/S3 document-storage adapter.
4. Réunia action-tracking adapter.
5. Resume Taylor engine adapter in its existing runtime.

For the first implementation, the Resume Taylor adapter may run in-process only inside its own environment or behind a small internal HTTP boundary. Do not force one dependency environment until the OpenAI SDK versions are reconciled.

## Phase 3 — First Career Bridge workflow

- Create a new Career Bridge application shell.
- Add a journey repository with its own table or store.
- Link existing records by IDs instead of moving them.
- Implement job setup, job-fit assessment, resume generation, and follow-up tasks.

## Phase 4 — Interview practice

- Adapt Réunia recording uploads and background jobs.
- Adapt transcription and transcript persistence.
- Add interview-specific scoring rubrics without changing meeting scoring behavior.
- Store mock-interview sessions under the Career Bridge journey.

## Phase 5 — Operational consolidation

- Reuse admin support and analytics.
- Add cross-product correlation IDs and cost attribution.
- Decide whether to unify the OpenAI client, persistence backends, and deployment only after production evidence supports the migration.

## Database rule

Do not rename or repurpose existing tables. Introduce a small Career Bridge store for shared IDs and relationships, then migrate data only with explicit, reversible scripts.
