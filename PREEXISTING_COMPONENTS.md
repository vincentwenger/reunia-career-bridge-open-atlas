# Pre-existing Components

This file establishes provenance for the Open Atlas hackathon project. The source archives did not contain `.git` directories, so their upstream commit history was unavailable. Each archive was therefore imported unchanged as an independent Git root commit and tagged before any shared Career Bridge work was added.

## Provenance tags

| Imported application | Git tag | Preserved path |
|---|---|---|
| Réunia | `reunia-original-import` | `products/reunia/` |
| Resume Taylor | `resume-tailor-original-import` | `products/resume_taylor/` |

The two root histories are retained on `import/reunia-original` and `import/resume-taylor-original`, then merged into `main` with `--allow-unrelated-histories`. SHA-256 manifests are stored under `provenance/`.

## Reusable component inventory

| Capability | Pre-existing implementation | Reuse decision | Important boundary |
|---|---|---|---|
| Authentication | Réunia authentication service, user repository, auth blueprint | Wrap | Réunia remains the account authority; do not duplicate login routes. |
| User profiles | Réunia account/settings profile plus Resume Taylor `CandidateProfile` | Consolidate later | Keep account preferences separate from verified career evidence. |
| Document storage | Réunia local/S3 knowledge and recorder stores; Resume Taylor exports | Adapt | Storage owns bytes; resume generation owns document creation. |
| OpenAI integration | Both applications call OpenAI and have model/prompt logic | Extract | Resolve SDK major-version conflict before a shared runtime adapter. |
| Audio recording | Réunia browser recorder, async jobs, worker, route layer | Wrap | Keep browser/worker behavior intact behind a port. |
| Transcription | Réunia recorder transcription and transcript services/repository | Adapt | Normalize segments to the shared transcript model. |
| Scoring | Réunia meeting scoring; Resume Taylor job-fit/resume reports | Consolidate later | Preserve individual rubrics; normalize only score type and 0–100 output. |
| Action tracking | Réunia action center; Resume Taylor application tracker | Adapt | Tasks and application outcomes remain separate legacy concepts adapted into one job application. |
| Admin support | Réunia support, incident, analytics services/repositories | Wrap | Reuse operational controls instead of rebuilding them in Career Bridge. |
| Resume parsing and generation | Resume Taylor profile schema, AI workflow, validation, DOCX/PDF export | Wrap | Treat the existing evidence-grounded resume pipeline as one engine. |

## Runtime and persistence observations

- Réunia uses Flask blueprints and services with DynamoDB, S3, Redis, and local/in-memory development alternatives.
- Resume Taylor is currently a single Flask application with a Pydantic-based resume domain and SQLite application tracking.
- Réunia pins `openai>=1.30,<2.0`; Resume Taylor pins `openai>=2.0,<3.0`. They must keep separate environments until adapters are upgraded to a compatible SDK.
- No routes, DynamoDB tables, Redis keys, S3 object layouts, or SQLite tables were merged in this foundation phase.

## New shared layer

The new `career_bridge/` package defines technology-neutral domain entities and ports. It is new hackathon work and is not represented as pre-existing functionality. See `HACKATHON_CHANGES.md` for the development log.
