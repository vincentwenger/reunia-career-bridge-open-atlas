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
