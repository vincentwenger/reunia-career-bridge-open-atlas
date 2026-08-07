# Open Atlas Hackathon Changes

## Scope

This document summarizes the new and substantially changed functionality created for the **unified Réunia Career Bridge application** during the Open Atlas – AI for Social Good Hackathon 2026 submission window, **June 20 through August 20, 2026**.

Open Atlas requires submissions to explain what was built during the hackathon versus what existed before. The pre-existing foundations are disclosed separately in [`PREEXISTING_COMPONENTS.md`](PREEXISTING_COMPONENTS.md).

## Summary

Career Bridge brought together two earlier codebases at different stages of maturity: a rudimentary **Resume Tailor** prototype that existed before June 20, 2026, and **Réunia**, an AI meeting-assistant project that began during the submission period and was underway no later than June 22, 2026. The two codebases began being integrated into Career Bridge on **July 28, 2026**.

The hackathon work was not merely a visual merge. It introduced a shared career data model, a new end-to-end candidate journey, application-specific workspaces, reusable career evidence, interview preparation and practice, action planning, job discovery, durable multi-user persistence, and a simplified career-focused interface.

## Major functionality built or substantially expanded

### 1. Unified Career Bridge architecture

- Created a shared data model connecting the user, Career Profile, Career Background, Verified Resume Evidence, Job Application, Application Baseline, tailored resume versions, interview preparation, mock interview sessions, scorecards, actions, and outcomes.
- Merged the predecessor products into one deployable Flask application instead of running them as separate applications.
- Added shared domain abstractions, ports, module registration, application ownership, and cross-feature identifiers.
- Refactored the integrated Application Builder into the main application routing, authentication, CSRF, error-handling, and configuration model.

### 2. Career-focused information architecture

- Replaced meeting-centered navigation with a career journey organized around Foundation, Jobs & Applications, Prepare and Practice, and Progress.
- Distinguished reusable one-time career information from job-specific application work.
- Removed, restricted, or deemphasized meeting features that did not support the target candidate journey.
- Consolidated duplicate navigation, panels, controls, and explanatory content to reduce cognitive load.

### 3. International and newcomer Career Profile

- Added fields for countries worked in, languages, international credentials, certifications, unfamiliar titles, target country, target role, career transitions, and target-market experience.
- Added target-country selection and country-aware terminology behavior.
- Added Career Translation and target-market review workflows for interpreting international experience without inventing equivalence.
- Added candidate confirmation when a translated title, credential, responsibility, or market interpretation cannot be fully traced to verified evidence.

### 4. Baseline Resume and Career Evidence Library

- Created a reusable Career Foundation containing the Career Profile, Baseline Resume, and Career Evidence Library.
- Added resume import and regeneration behavior that preserves links, contact information, evidence identity, and the source language where appropriate.
- Added reusable confirmed answers so candidates do not have to answer the same evidence question for every application.
- Added editing and reuse of saved career evidence across future applications.
- Added application-specific baselines so every job application starts from the current foundation without silently diverging from it.

### 5. Application-specific resume workflow

- Connected each resume workflow to a specific Job Application workspace.
- Added Application Baseline, Job-Aligned Resume, Final Resume, comparisons, reports, and export lifecycle.
- Strengthened bullet-selection rules to prioritize explicit job requirements, evidence strength, unique coverage, space, redundancy, and transferable value.
- Prevented zero-match automatically restored bullets from displacing stronger matched evidence.
- Added warnings and review behavior for missing decisions, unsupported claims, ambiguous evidence, and reconciliation defects.
- Added past-tense action-verb normalization and resume-bullet formatting safeguards.
- Improved score guards so automatic optimization is retained only when protected quality and job-alignment metrics remain equal or improve.

### 6. Interview Preparation workspace

- Added role and company summaries, likely interview questions, key responsibilities, strengths, gaps, candidate talking points, resume challenges, questions to ask, and introduction outlines.
- Connected preparation content to the selected application, job description, and verified candidate evidence.
- Added preparation behavior that distinguishes future interviews from completed interviews.

### 7. Adaptive Mock Interview

- Added recruiter, hiring-manager, behavioral, technical, final-round, and custom mock interview modes.
- Added adaptive follow-up questions grounded in the candidate’s answer and target role.
- Added reusable user-created question lists for later practice.
- Connected sessions to a specific Job Application and evidence context.

### 8. Interview Scorecard and review

- Added per-answer and session-level scoring for relevance, evidence, STAR structure, clarity, job alignment, confidence, follow-up handling, and candidate questions.
- Added evidence-grounded improved-answer suggestions that may use only confirmed facts.
- Added review outputs that identify weak answers and convert them into concrete practice actions.
- Added post-interview behavior that creates thank-you and follow-up actions instead of obsolete preparation tasks.

### 9. Career Action Plan

- Repurposed generic action tracking into an application-linked Career Action Plan.
- Added actions generated from resume gaps, evidence-review findings, interview scorecards, upcoming interviews, follow-up dates, and saved next steps.
- Added career-specific action types such as gathering evidence, researching a company, preparing STAR examples, explaining international credentials, recruiter follow-up, thank-you messages, and additional mock practice.
- Connected actions to the relevant application so the user can understand why each action exists.

### 10. Job Discovery and fit ranking

- Added employer-source job discovery with adapters and fallbacks for multiple applicant-tracking systems and structured job pages.
- Added source normalization, safe URL handling, robots/source-policy checks, bounded requests, deduplication, posting-age rules, and conservative deactivation.
- Added two-stage ranking: inexpensive filtering followed by evidence-grounded fit analysis.
- Added separate Job Fit and Search Priority concepts.
- Added strengths, gaps, confidence, recommendation, and supported-requirement explanations.
- Added Save, Ignore, and Create Application Workspace actions without auto-applying to employers.
- Added per-source scanning, admin-managed sources, and scheduled/external refresh support.

### 11. Shared public-job catalog

- Added an administrator-managed shared catalog of public job postings so the same employer source does not need to be scanned independently for every user.
- Kept candidate preferences, evidence, fit assessments, saved/ignored state, and applications private and owner-scoped.
- Added source-level refresh locks, cache freshness policies, canonical source identities, and user-specific materialization from public records.
- Added a materialized result index to reduce Job Discovery page-load latency and DynamoDB reads.

### 12. Durable production storage

- Replaced process-local workflow state and SQLite application persistence with DynamoDB-backed repositories.
- Added S3-backed storage for uploaded resumes, generated documents, large workflow snapshots, findings, interview preparation, and progress details.
- Added versioning, optimistic concurrency, owner isolation, serialization, workflow retention policies, and deletion behavior.
- Added dedicated Job Discovery storage and public-catalog partitions.
- Removed remaining SQLite production paths and added validation that rejects unsafe production storage configurations.

### 13. Deployment and operational hardening

- Added AWS Lightsail deployment validation, service-scale checks, container-command checks, worker-safety constraints, and health visibility.
- Added production warnings for non-durable storage and explicit demo-only overrides.
- Added automatic incident reporting and administrator visibility for server errors.
- Improved AI usage tracking, cost estimation, model configuration, token controls, retries, and failure handling.
- Added tests and validation scripts for persistence, multi-instance safety, concurrency, owner isolation, job-source behavior, and critical workflows.

### 14. User-interface simplification

- Consolidated the product around a smaller number of high-value workspaces.
- Removed duplicate subnavigation, redundant hero panels, repeated instructions, and low-value controls.
- Simplified dashboards and application panels.
- Applied a consistent trust-focused visual system and responsive page hierarchy.
- Improved loading behavior and deferred expensive Job Discovery analysis to reduce perceived latency.

## New versus adapted: concise matrix

| Area | Earlier foundation / starting point | Hackathon-period work |
|---|---|---|
| Product shell | Réunia Flask app, itself developed during the submission period | Unified Career Bridge application and career-specific navigation |
| Career data | Resume Tailor Candidate Profile | Shared Career Profile, Career Background, Evidence Library, Application, Interview, Action, and Outcome model |
| Resume workflow | Evidence-grounded tailoring and export | Application Baseline lifecycle, cross-application evidence reuse, stronger reconciliation and optimization rules |
| Actions | Réunia generic meeting Action Center, developed during the submission period | Application-linked Career Action Plan with automatically generated career next steps |
| Scoring | Réunia meeting scoring plus Resume Tailor résumé reports | Interview scorecards and shared evidence-grounded assessment patterns |
| Interview tools | Réunia meeting recording/review foundations, developed during the submission period | Interview Preparation, adaptive mock interviews, answer review, and post-interview follow-up |
| Job sourcing | None in the predecessor products | Multi-source Job Discovery, ranking, shared public catalog, and application conversion |
| Persistence | Réunia AWS patterns from submission-period work; Resume Tailor process/SQLite state | DynamoDB-only production application/workflow/discovery storage and S3 document persistence |
| User experience | Separate Réunia and Resume Tailor codebases | One end-to-end career journey designed for international and newcomer candidates |

## Chronological milestone summary

The following labels summarize the selected development sequence reconstructed from saved project archives and the earlier provenance review. They are descriptive review labels, not claims that matching Git tags are bundled in this source ZIP.

| Sequence | Date / period | Milestone | Review label |
|---:|---|---|---|
| 000-A | Before Jun 20, 2026 | Rudimentary Resume Tailor prototype already existed | Pre-hackathon baseline |
| 000-B | By Jun 22, 2026 | Original Réunia meeting-assistant development underway | Submission-period predecessor |
| 000-C | Jul 28, 2026 | Réunia and Resume Tailor integration into Career Bridge began | Integration start |
| 001 | After Jul 28, 2026 | Shared Career Bridge data model | Data model baseline |
| 002 | After Jul 28, 2026 | Career-focused navigation | Incremental milestone |
| 003 | After Jul 28, 2026 | Integrated Application Builder | Incremental milestone |
| 004 | After Jul 28, 2026 | First unified Career Bridge build | Unified product baseline |
| 005 | After Jul 28, 2026 | Newcomer and international Career Profile | Incremental milestone |
| 006 | After Jul 28, 2026 | Interview Preparation | Incremental milestone |
| 007 | After Jul 28, 2026 | Adaptive Mock Interview | Incremental milestone |
| 008 | After Jul 28, 2026 | Interview Scorecard | Incremental milestone |
| 009 | After Jul 28, 2026 | Career Action Plan | Incremental milestone |
| 010 | After Jul 28, 2026 | Complete end-to-end MVP journey | End-to-end MVP |
| 011 | After Jul 28, 2026 | Production deployment hardening | Production hardening |
| 012 | After Jul 28, 2026 | Job Discovery | Job Discovery |
| 013 | After Jul 28, 2026 | DynamoDB Job Discovery records | Incremental milestone |
| 014 | After Jul 28, 2026 | Shared public-job catalog | Incremental milestone |
| 015 | After Jul 28, 2026 | DynamoDB-only production persistence | Durable persistence |
| 016 | After Jul 28, 2026 | Consolidated minimal interface | Minimal interface |
| 017 | After Jul 28, 2026 | Consistent trust-focused interface | Incremental milestone |
| 018 | After Jul 28, 2026 | Current Career Bridge snapshot | Current source snapshot |

The sequence was reconstructed from sanitized project archives and is provided as a product-development summary, not as an original commit-by-commit record. The current source package and the conservative component disclosure remain the reviewable artifacts.

## AI-assisted development disclosure

Career Bridge was developed through a human-directed, AI-assisted workflow using ChatGPT and OpenAI coding tools. AI tools helped inspect code, propose architecture, implement targeted changes, create tests, analyze defects, and improve documentation. The project owner defined the product direction, supplied requirements and acceptance criteria, tested the application, managed infrastructure and deployment, and decided which generated changes to accept, revise, or reject.

The use of AI tools to build the application is separate from the OpenAI functionality used by the running product for job analysis, resume tailoring, evidence review, interview preparation, mock interviews, and related career guidance.

## Boundaries and limitations

- Career Bridge does not automatically submit job applications or interact with employer forms on the candidate’s behalf.
- Generated resume and interview content is constrained to verified or candidate-confirmed evidence.
- The demo must not contain real sensitive personal data.
- Real-time interview answer generation is not part of Career Bridge; candidates use preparation, mock interviews, and post-practice review.
- Applicant-tracking-system access depends on public endpoints, robots rules, source policies, and availability.
- The reconstructed history includes selected milestones rather than every intermediate corrective build.

## Submission-ready disclosure paragraph

> Réunia Career Bridge was developed during the Open Atlas submission period by bringing together two earlier codebases at different stages of maturity. A rudimentary Resume Tailor prototype existed before June 20, 2026. Réunia, an AI meeting-assistant foundation, began during the submission period and was underway no later than June 22, 2026. The two codebases began being integrated into Career Bridge on July 28, 2026. During the hackathon, I substantially redesigned and expanded those foundations into the unified Career Bridge architecture and journey, including newcomer-focused Career Profile and Career Translation workflows, the Career Evidence Library, application-specific résumé lifecycle, Interview Preparation, adaptive Mock Interview, Interview Scorecard, Career Action Plan, Job Discovery and shared public-job catalog, DynamoDB/S3 persistence, durable background processing, and a consolidated career-focused interface. The published Git history begins around the integration point; the README and `docs/submission/project-history/` document the earlier provenance. The published copy was sanitized only to remove personal or sensitive candidate data while preserving commit messages, authorship, dates, and development chronology; commits were not backdated to manufacture activity.

## Legacy meeting runtime retired by default

- Added explicit default-off flags for the predecessor recorder, meeting sharing, generic transcript aliases, meeting-knowledge aliases, and one-time materials migration.
- Legacy route modules and blueprints are imported and registered only when their flags are enabled.
- Redis, recorder S3 storage, recorder queues, meeting-share tables, and the recorder worker are no longer required or started for the core Career Bridge deployment.
- Adaptive Mock Interview sessions now persist through the canonical application store rather than the legacy recorder job store.

## Product promise clarification

- Corrected marketing, help, user-guide, login, README, and feature-mapping copy so recruiter-message history, communication drafting, calendar synchronization, cover-letter generation, and advanced application analytics are identified as planned secondary features rather than current capabilities.
- Documented the current support for upcoming interview dates, application follow-up dates, custom next steps, interview-preparation actions, and post-interview thank-you actions.
- Added a canonical six-step secondary-feature roadmap and regression tests that prevent future copy from overstating implemented capabilities.

## Explicit Career Bridge analytics identifiers

- Added a stable `data-feature` identifier to every rendered page instead of deriving product features from URL paths.
- Updated the client tracker to send the explicit feature with both page activity and `feature_used` events.
- Added analytics tracking to Application Builder pages, which previously did not load the shared tracker.
- Replaced legacy feature names such as `browser_recorder`, `meeting_review`, `action_center`, and `analytics` with canonical Career Bridge identifiers while retaining aliases for historical events.
- Stored the latest explicit feature on activity records so current Admin Analytics no longer depends on route-name inference.

## DynamoDB test-user cleanup utility

- Added a dry-run-first `scripts/delete_dynamodb_user_records.bat` wrapper and Python implementation for deleting one user's records across canonical `careerbridge_` DynamoDB tables.
- The utility resolves configured table names from `.env` and process environment variables, skips absent tables, discovers user/email aliases from the account record, and includes async queue tickets linked through `job_owner_id`.
- Destructive mode requires `--delete` and an exact confirmation unless `--yes` is explicitly supplied; user-account records are deleted last and S3 objects are never removed.

## Durable Resume Workflow operations

- Moved Baseline Resume translation, job analysis/initial tailoring, report regeneration, final optimization/evidence review, and final Word/PDF generation to the existing durable `AsyncJob` worker.
- Added per-workflow idempotency guards, duplicate-submission reuse, conflicting-operation protection, phase progress, cancellation, retry, and reconnect UI.
- Added independent post-optimization evidence verification and a provider-safe fallback that preserves the approved resume when optional refinement fails.
- Removed PDF generation from final-download HTTP requests and added a durable export preparation action.
- Reduced the default Gunicorn timeout from 600 seconds to 180 seconds.
## 2026-08-04 - Application Builder JavaScript contract cleanup

- Removed the obsolete aggregate `products/resume_taylor/static/app.js` and its minified sibling.
- Migrated contract and integration tests to the page-scoped assets loaded by each workspace.
- Moved shared hash-panel reveal and workspace retry behavior into always-loaded `app-shell.js`.
- Added a regression contract that prevents the aggregate bundle from returning.


## Static asset CI guardrails

- Restored and strengthened `.github/workflows/asset-budget.yml`.
- Added non-mutating `python scripts/build_static_assets.py --check` validation.
- CI now enforces generated assets, asset budgets, shared-page assets, and the performance/maintainability contracts on pushes to `main` and pull requests.

## Maintainability module decomposition

- Replaced the 3,000-line Job Discovery `register()` closure with a small registrar and top-level modules for source support, result queries, workspace view models, source commands, background operations, and result actions.
- Replaced the oversized Resume Workflow route closure with small controllers for profile, tailoring, confirmation, finalization, downloads, workspace rendering, configuration, and durable jobs.
- Split Resume Report, Admin Analytics, Job Discovery storage, and Mock Interview into bounded implementation modules behind backward-compatible facades.
- Updated structural tests to inspect the specific implementation module that owns each behavior.
- Added architecture contracts that cap facade/component size and forbid nested route handlers inside the Job Discovery and Resume Workflow registrars.

## Canonical CSS token system

- Replaced the overlapping `--color-*`, `--career-*`, `--ma-*`, and generic Application Builder variables with one `--cb-*` namespace.
- Centralized all exact hex shades in `design-tokens.css`; user-facing page and component styles now contain no raw hex literals.
- Reduced source `!important` declarations from 148 to 113 and added a non-increasing migration baseline.
- Added Stylelint rules for canonical custom-property names, raw-color rejection, and `!important` warnings.
- Added CI and Python guardrails that prevent new page-local colors, token aliases, exact palette growth, or additional `!important` declarations.

## Career Evidence Library readiness

- Replaced the dashboard's hard-coded `Open` badge with a real readiness status.
- The library is `Ready` when it contains at least one processed reusable document, supportive confirmed answer, or active confirmed career role.
- The dashboard now displays `Ready · N items` or `Needs setup`, and Career Foundation completion counts all three foundation areas.

## Repository submission cleanup

- Moved hackathon provenance documents from the repository root into `docs/submission/`, leaving `README.md` as the only root Markdown document.
- Consolidated the numbered Lightsail upload variants into the canonical `scripts/deployment/upload_to_lightsail.bat` entry point.
- Corrected the storage preflight filename to `provision_career_bridge_storage.bat`.
- Added repository-hygiene contracts and `scripts/clean_repository_artifacts.py` to remove Python cache and compiled artifacts before packaging.
- Verified the Lightsail guide links to the existing `docs/deployment/async-ai-jobs.md` documentation.

## Development-test runtime enforcement

- Added `requirements-dev.txt`, which includes the complete runtime requirements and Python Playwright.
- Added `.github/workflows/runtime-tests.yml` to install Playwright Chromium and run `tests/run_final_integration_checks.py --require-runtime`.
- Centralized runtime dependency checks in `tests/runtime_dependencies.py` and detect Playwright-managed Chromium rather than relying on a system browser executable.
- Added contracts that prevent the development dependency file or required-runtime CI command from being removed silently.

## CSP compatibility validation

- Added static contracts that reject inline HTML event handlers and require the request nonce on every inline executable script.
- Added Flask runtime checks that load the main Career Bridge pages with the production CSP header and verify rendered inline-script nonces.
- Added a Playwright journey that exercises Job Discovery filtering plus dismiss/accept behavior for destructive source removal under CSP.
- Added the CSP runtime and browser suites to `tests/run_final_integration_checks.py --require-runtime` so missing browser coverage fails CI rather than being silently blocked.

- Added a durable async-worker heartbeat record, `/health` freshness metadata, long-job heartbeat publishing, and deployment validation that rejects missing or stale workers.
