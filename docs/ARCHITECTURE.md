# Career Bridge Architecture

## Goal

Create a Career Bridge product that can reuse mature capabilities from Réunia and Resume Taylor while keeping the first integration reversible and auditable.

## Layering

1. **Domain (`career_bridge/domain`)** — stable career concepts and invariants.
2. **Application (`career_bridge/application`)** — orchestration independent of Flask, AWS, SQLite, Redis, or OpenAI.
3. **Ports (`career_bridge/ports.py`)** — contracts implemented by product adapters.
4. **Adapters (future)** — thin wrappers around existing services in `products/`.
5. **Delivery (future)** — Career Bridge routes, UI, workers, and deployment wiring.

Dependencies point inward. Imported products are not dependencies of the domain package.

## Shared aggregate

`CareerJourney` is the central aggregate. It connects a user and target role to:

- source and generated documents;
- verified candidate evidence;
- job-fit, resume, readiness, and communication scores;
- mock or real interview sessions and transcripts;
- follow-up actions;
- support and operational references.

This model gives both products a common vocabulary without forcing their current storage records into one table.

## Anti-corruption boundaries

- Réunia meeting IDs must not become Career Bridge journey IDs.
- Resume Taylor workflow state must not become the shared persistence schema.
- Existing scores retain their own algorithms and are labeled by `ScoreKind`.
- Existing account settings and candidate evidence remain separate models.
- Binary storage keys are opaque to the domain layer.
- OpenAI request details remain inside provider adapters.

## First vertical slice

The safest first end-to-end slice is:

1. Authenticate through Réunia.
2. Create a `CareerJourney` for a job.
3. Store a job description and source resume through the Réunia storage adapter.
4. Call Resume Taylor through `ResumeEnginePort`.
5. Normalize its job-fit and resume scores.
6. Create interview-preparation actions in the Réunia action adapter.

Audio recording and mock-interview transcription can be the second slice after the account, storage, and resume boundaries are proven.
