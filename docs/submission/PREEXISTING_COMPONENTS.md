# Pre-Existing Components Disclosure

## Purpose

This document identifies what existed **before** the Open Atlas submission window opened on **June 20, 2026**, and distinguishes it from work that began during the submission period but before the unified **Réunia Career Bridge** product was assembled.

The distinction matters because the two source codebases were not equally pre-existing:

1. **Resume Tailor** was a rudimentary résumé-tailoring prototype that existed before June 20, 2026.
2. **Réunia** was a separate AI meeting-assistant project whose development began during the Open Atlas submission period; surviving history places development underway no later than **June 22, 2026**.
3. The two codebases began being integrated into **Réunia Career Bridge on July 28, 2026**.

The repository therefore does **not** describe Réunia as a pre-hackathon product. It is an earlier codebase in the Career Bridge lineage, but it was itself submission-period work.

Where the origin of an individual shared utility cannot be established confidently, the disclosure remains conservative and does not claim that utility as newly invented specifically for Career Bridge.

## 1. Pre-hackathon Resume Tailor foundation

The principal pre-existing product foundation was the rudimentary **Resume Tailor** prototype.

| Pre-existing capability | Original purpose | Career Bridge use |
|---|---|---|
| Resume import and parsing | Build a structured candidate résumé | Baseline Resume and reusable candidate foundation |
| Job-description analysis | Identify target-role requirements | Application-specific requirement extraction and fit analysis |
| Evidence-grounded tailoring concepts | Propose résumé wording tied to candidate evidence | Job-Aligned Resume and final résumé lifecycle |
| Resume comparison and quality review concepts | Compare versions and identify improvements | Multi-stage résumé review and optimization |
| Evidence review concepts | Detect unsupported or weakly supported claims | No-invented-experience controls and candidate confirmation |
| DOCX/PDF export concepts | Produce application documents | Final résumé and report exports |
| Early candidate-profile concepts | Capture résumé and career context | Expanded Career Profile and target-market context |

This prototype was rudimentary compared with the final Career Bridge product. Its existence before June 20 is disclosed rather than hidden, and the submission does not claim these baseline concepts as newly invented during the hackathon.

## 2. Réunia: submission-period predecessor foundation

Réunia began during the submission window and is therefore **not classified as pre-hackathon work**. It was initially developed as a separate Flask-based AI meeting assistant and later supplied useful foundations when Career Bridge was assembled.

Surviving development history places the original Réunia work underway no later than **June 22, 2026**. Examples of Réunia-era capabilities later reused or adapted include:

| Réunia capability | Original purpose | Career Bridge use |
|---|---|---|
| Flask application shell and deployment entry points | Run the Réunia web application | Unified Career Bridge web shell and production entry point |
| Authentication and user accounts | Sign-in, profiles, password flows, and persistence | Account access for private career workspaces |
| DynamoDB repository patterns | Store user and meeting-related records | Adapted for applications, actions, workflows, discovery, and async jobs |
| S3/document-storage patterns | Store knowledge and recorder artifacts | Adapted for résumés, exports, and large workflow snapshots |
| OpenAI client integration and usage controls | Meeting analysis, Q&A, summaries, and governance | Adapted for job, résumé, evidence, and interview workflows |
| Administrative settings, analytics, and support foundations | Operate the earlier product | Retained and simplified for Career Bridge operations |
| Generic action tracking | Track meeting follow-ups | Adapted into application-linked career actions |
| Audio, transcription, and review concepts | Record and review meetings | Reused only where relevant to mock-interview practice and review |

These capabilities may predate the **July 28 Career Bridge integration**, but they do not predate the **June 20 Open Atlas submission window** merely because they came from a predecessor codebase.

Predecessor meeting features that did not support the Career Bridge journey were later retired, disabled by default, or removed from production configuration.

## 3. General technical foundations

The submission also relies on standard technologies and engineering patterns that are not claimed as hackathon-specific inventions, including:

- Python, Flask, Jinja, HTML, CSS, and JavaScript application patterns;
- OpenAI API connectivity and model configuration;
- AWS Lightsail, DynamoDB, S3, and Docker deployment patterns;
- common security controls such as CSRF protection, password hashing, owner checks, rate limiting, and secret-based configuration;
- document parsing and export libraries;
- testing utilities, logging, error handling, and operational helpers.

## 4. Built or substantially expanded for Career Bridge

Starting with the July 28 integration, the submission-period work transformed the earlier codebases into a connected career product. Major additions and substantial expansions include:

- the unified Career Bridge information architecture and cross-feature application model;
- newcomer-focused Career Profile and Career Translation workflows;
- the reusable and editable Career Evidence Library with confirmed-answer reuse;
- Job Discovery source adapters, normalization, deduplication, freshness policies, ranking, and application conversion;
- job-specific application workspaces linking dates, status, artifacts, interviews, actions, and outcomes;
- the Application Baseline and full job-specific résumé lifecycle;
- evidence-prioritized bullet reconciliation and safeguards against unsupported content;
- role-specific Interview Preparation;
- adaptive mock-interview formats, saved question sets, follow-up logic, persistence, and scorecards;
- Interview Review with evidence-constrained improved answers;
- the application-linked Career Action Plan and Progress & Outcomes experience;
- durable DynamoDB and S3 storage for Career Bridge records and documents;
- durable background jobs for long-running AI work, including worker heartbeat and release validation;
- owner isolation, optimistic concurrency, idempotency, cancellation, retry, and cleanup behavior;
- a consolidated career-focused visual system, simplified navigation, page-scoped assets, and static-quality gates;
- broad unit, contract, integration, regression, and browser validation for the unified journey.

## 5. Repository-history boundary

The published repository history begins around the integration of the two codebases rather than reproducing every original commit from both predecessor projects. The published history preserves the original development chronology, commit messages, authorship, and commit dates. Historical content was sanitized only where necessary to remove personal or sensitive candidate data; it was not backdated or altered to imply earlier development activity.

The reviewable provenance record is therefore:

- this disclosure;
- [`HACKATHON_CHANGES.md`](HACKATHON_CHANGES.md);
- the concise history under [`project-history/`](project-history/);
- the actual Git commit history available in the public repository.

A GitHub source ZIP does not include the repository's `.git` database, branches, or tag references. For that reason, source packages such as this one cannot independently prove which Git tags exist on the hosted repository. Reviewers should use the public repository's **Commits**, **Branches**, and **Tags/Releases** views for Git-level provenance.

## 6. AI-assisted development disclosure

Career Bridge was developed through a human-directed, AI-assisted workflow using ChatGPT and OpenAI coding tools. AI tools helped inspect code, propose architecture, implement targeted changes, create tests, analyze defects, and improve documentation. The project owner defined the product direction, supplied requirements and acceptance criteria, tested the application, managed infrastructure and deployment, and decided which generated changes to accept, revise, or reject.

This development assistance is separate from the OpenAI functionality used by the running product.

## Submission-ready disclosure paragraph

> Réunia Career Bridge was developed during the Open Atlas submission period by bringing together two earlier codebases at different stages of maturity. A rudimentary Resume Tailor prototype existed before the June 20, 2026 submission window and supplied early résumé import, job-analysis, tailoring, evidence-review, comparison, and export concepts. Réunia, an AI meeting-assistant foundation, began during the submission period and was underway no later than June 22, 2026. The two codebases began being integrated into Career Bridge on July 28, 2026. During the hackathon, they were substantially redesigned and expanded into a unified, evidence-grounded career platform with newcomer-focused career translation, reusable candidate-confirmed evidence, job discovery and ranking, application-specific résumé workflows, interview preparation, adaptive mock interviews, scorecards, career action planning, durable cloud persistence, background processing, deployment safeguards, and a consolidated user experience.
