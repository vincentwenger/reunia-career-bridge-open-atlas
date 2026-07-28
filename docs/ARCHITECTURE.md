# Career Bridge Architecture

## Goal

Create one Career Bridge product that reuses mature capabilities from Réunia and Resume Taylor while keeping integration reversible, auditable, and centered on a candidate's job application.

## Layering

1. **Domain (`career_bridge/domain`)** — stable career concepts and invariants.
2. **Application (`career_bridge/application`)** — orchestration independent of Flask, AWS, SQLite, Redis, or OpenAI.
3. **Ports (`career_bridge/ports.py`)** — contracts implemented by product adapters.
4. **Adapters (future)** — thin wrappers around existing services in `products/`.
5. **Delivery (future)** — Career Bridge routes, UI, workers, and deployment wiring.

Dependencies point inward. Imported products are not dependencies of the domain package.

## Shared aggregate

`JobApplication` is the aggregate root. It connects:

- a candidate profile and reusable career background;
- a selected source resume;
- one target job description;
- a candidate evidence library;
- tailored resume versions and application-scoped scores;
- interview preparation and questions;
- mock interview sessions, recordings, transcripts, and scorecards;
- improvement actions;
- the business application status and status history.

See [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the relationship diagram and invariants.

## Why this is not a meeting-centered system

Réunia meeting, recording, and transcript records become supporting capabilities. A mock interview session belongs to a job application; it does not own the candidate, resume, target job, or application lifecycle.

Resume Taylor workflow state also becomes supporting implementation detail. Its draft and final outputs are normalized as `TailoredResumeVersion` records belonging to the same job application used by interview practice.

## Anti-corruption boundaries

- Réunia meeting or recorder IDs must not become Career Bridge application IDs.
- Resume Taylor workflow/session state must not become the shared persistence schema.
- Existing scores retain their algorithms and are labeled by `ScoreKind`.
- Account preferences remain separate from career-facing candidate data.
- Candidate evidence is reusable, but selected evidence and generated outputs are application-scoped.
- Binary storage keys are opaque to the domain layer.
- OpenAI request details remain inside provider adapters.
- Existing product tables are not renamed or repurposed.

## First vertical slice

1. Authenticate through Réunia.
2. Load or create a candidate profile, career background, source resume, and evidence library.
3. Create a `JobApplication` and target job description.
4. Call Resume Taylor through `ResumeEnginePort`.
5. Save a `TailoredResumeVersion` and normalized job-fit/resume scores.
6. Generate `InterviewPreparation` from the same target job and evidence.
7. Create application-scoped `ImprovementAction` records.

Mock interview recording and transcription are the second slice, attached to this existing job application rather than introduced as a separate meeting workflow.
