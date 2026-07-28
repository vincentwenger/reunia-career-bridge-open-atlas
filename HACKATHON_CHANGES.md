# Hackathon Changes Development Log

This log records work added after the two original applications were tagged.

## 2026-07-28 — Foundation and provenance

### Added

- Created separate Git root commits for the imported Réunia and Resume Taylor archives.
- Added annotated tags `reunia-original-import` and `resume-tailor-original-import`.
- Preserved both root histories on dedicated import branches and merged them with unrelated-history preservation.
- Added `PREEXISTING_COMPONENTS.md` with a capability-level inventory and reuse decisions.
- Added this development log.
- Added SHA-256 archive and imported-tree manifests under `provenance/`.

### Shared Career Bridge architecture

- Added a dependency-free `career_bridge/` package.
- Added a shared domain model for users, career journeys, documents, candidate evidence, resume artifacts, interview sessions, transcripts, scores, scorecards, actions, and support cases.
- Added explicit lifecycle enums and validation, including controlled journey-stage transitions and 0–100 score constraints.
- Added technology-neutral ports for authentication, profiles, storage, OpenAI, recording, transcription, scoring, actions, admin support, resume generation, and Career Bridge persistence.
- Added a module registry mapping every requested reusable capability to its pre-existing source files and intended adapter strategy.
- Added a composition container so modules can be migrated independently.
- Added a small coordinator for journey creation, lifecycle movement, and follow-up action creation without depending on Flask or a particular database.

### Intentionally not changed

- No UI or route integration.
- No shared database schema or data migration.
- No changes inside either imported product snapshot.
- No OpenAI SDK upgrade or dependency unification.
- No authentication migration.
- No production deployment configuration changes.

## Next recommended log entries

Future commits should append adapter-level changes here, including the exact legacy files reused, new tests, schema migrations, and any behavior differences introduced for Career Bridge.

## 2026-07-28 — Job application aggregate

### Architectural change

- Replaced the shared `CareerJourney` aggregate with `JobApplication` as the central domain object.
- Replaced generic journey stages with an explicit job-application lifecycle and auditable status history.
- Removed journey terminology from shared ports, application orchestration, and current architecture documentation.

### Shared data model

- Added `CandidateProfile`, distinct from authentication/account preferences.
- Added normalized `CareerBackground`, `CareerExperience`, and `EducationRecord` models.
- Added reusable `Resume`, `EvidenceLibrary`, and `EvidenceItem` models.
- Added application-owned `TargetJobDescription` and `TailoredResumeVersion` models.
- Added `InterviewPreparation`, `InterviewQuestion`, and `MockInterviewSession` models.
- Replaced generic career actions with application-scoped `ImprovementAction` records.
- Made `Score` application-scoped and linked support cases optionally to an application.
- Added `JobApplicationBundle` to validate a fully hydrated application graph across resume and interview capabilities.

### Contracts and tests

- Updated resume generation, scoring, audio, transcription, action tracking, and repository ports to use job applications.
- Updated the coordinator to create applications, transition application status, and save improvement actions.
- Added shared tests for aggregate relationships, lifecycle transitions, graph consistency, score scope, and coordinator behavior.
- Added `docs/DOMAIN_MODEL.md` with the shared relationship model, lifecycle, legacy mappings, and anti-leakage rules.

### Intentionally not changed

- No imported Réunia or Resume Taylor product code was modified.
- No legacy route, meeting table, resume workflow table, or database object was merged.
- No adapter or production migration was introduced in this change.

## 2026-07-28 — Career Bridge navigation

### Shared information architecture

- Added a framework-neutral eight-section navigation definition under `career_bridge/presentation/`.
- Mapped each product workspace to the `JobApplication` aggregate relationships it owns or presents.
- Added validation that every referenced relationship remains a real `JobApplication` field.
- Kept Help & Support outside aggregate ownership while preserving it as the eighth user workspace.

### Réunia delivery adapter

- Replaced the signed-in meeting-oriented navigation with:
  1. Career Profile
  2. Application Builder
  3. Interview Preparation
  4. Mock Interview
  5. Interview Review
  6. Career Action Plan
  7. Progress
  8. Help & Support
- Reused the existing Réunia routes as delivery adapters during incremental migration rather than merging routes or databases.
- Changed the signed-in brand subtitle to `AI CAREER BRIDGE`.
- Moved Administration out of the account dropdown into a separate navigation control.
- Preserved both UI visibility checks and server-side administrator authorization.
- Added responsive styles and English/French navigation strings.

### Route mapping during migration

- Career Profile → existing authenticated profile workspace.
- Application Builder → current authenticated application home until the Resume Taylor adapter is mounted.
- Interview Preparation → existing materials/context/knowledge workspace.
- Mock Interview → existing recording workflow.
- Interview Review → existing review and scorecard workflow.
- Career Action Plan → existing action tracking workflow.
- Progress → existing user analytics workflow.
- Help & Support → existing support workflow.

### Intentionally not changed

- No legacy route was deleted.
- No meeting, resume, action, or analytics database object was renamed.
- No Resume Taylor Flask routes were mounted into Réunia.
- No administrator authorization rule was weakened.

## 2026-07-28 — Réunia feature repurposing

- Added the canonical legacy-to-Career Bridge feature map in `career_bridge/presentation/feature_mapping.py`.
- Added clean career-facing pages and preferred `/api/career/*` adapter aliases while retaining imported routes for migration compatibility.
- Repurposed Meeting Preparation into Interview Preparation centered on the company, target role, interviewers, likely questions, and verified evidence.
- Repurposed Meeting Materials, AI Context, Knowledge Search, and Meeting Package into Application Materials, Career Profile, Career Evidence Library, and one Application Workspace per target position.
- Repurposed the browser recorder and review workflow into mock-interview practice, transcription, Interview Review, and an Interview Scorecard covering relevance, evidence, structure, clarity, and delivery.
- Repurposed Action Center and Analytics into Career Action Plan and Career Progress.
- Removed Windows Desktop Recorder entry points from the MVP; its download and ingestion compatibility endpoints now return HTTP 410.
- Removed candidate-facing Live Q&A and disabled its legacy stream endpoints with HTTP 410 responses, keeping the product practice-only.
- Kept Admin Analytics and Incidents unchanged, separate, and administrator-only.
- Preserved legacy storage keys, route aliases, and meeting-shaped persistence fields behind the adapter boundary so existing data remains readable.
