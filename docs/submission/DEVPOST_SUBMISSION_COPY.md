# Devpost Submission Copy

## Project name

Réunia Career Bridge

## Elevator pitch

Réunia Career Bridge helps immigrants and newcomers translate their international experience into applications that U.S. employers can understand and evaluate - without inventing qualifications. It connects verified career evidence, job matching, tailored resumes, interview preparation, adaptive practice, and application-specific next actions in one evidence-grounded workflow.

## Full description

Internationally experienced professionals often have strong skills and accomplishments that are difficult to express in the terminology, evidence format, and interview conventions expected by U.S. employers. Existing career tools frequently optimize wording without preserving traceability, ask candidates to repeat the same evidence for every application, or separate resume work from interview preparation and follow-up.

Réunia Career Bridge maintains a reusable foundation of candidate-provided and candidate-confirmed evidence. It uses that foundation to translate international titles and credentials, assess public jobs, create application workspaces, produce job-aligned resume wording, generate interview preparation, conduct adaptive mock interviews, score responses, and turn gaps or dates into an application-linked action plan.

The core safeguard is simple: AI may improve communication, but it may not invent experience. Uncertain interpretations require candidate confirmation, generated claims retain evidence provenance, and improved interview answers are constrained to verified facts.

The project aligns primarily with Career & Talent AI for workforce inclusion and Newcomer Settlement Tools. Its goal is to reduce avoidable information loss when internationally experienced candidates enter a new labor market.

## What is technically distinctive

- One canonical application aggregate connects job requirements, resume versions, evidence, interview preparation, practice sessions, outcomes, and actions.
- A reusable Career Evidence Library reduces repeated questioning across applications.
- Evidence-aware reconciliation prevents unsupported or zero-match bullets from displacing stronger verified evidence.
- Adaptive mock interviews select follow-ups from the candidate's previous response.
- Long-running AI tasks use durable background jobs with retries, idempotency, progress, and worker-heartbeat health checks.
- Production application, workflow, discovery, and job records use DynamoDB; large documents and snapshots use S3.
- Owner isolation, optimistic concurrency, cost controls, deployment validation, and no-invented-experience regression tests are built into the system.

## Social impact

Career Bridge is designed for immigrants, newcomers, internationally trained professionals, career changers, and other candidates whose experience may be strong but difficult to translate into a new labor market. It aims to help candidates preserve the substance of their experience, understand where evidence is missing, practice unfamiliar interview conventions, and take concrete next steps without fabricating qualifications.

## Built with

Python, Flask, Jinja, JavaScript, OpenAI API, Amazon DynamoDB, Amazon S3, Docker, AWS Lightsail Container Service, Playwright, python-docx, pypdf, ReportLab, and openpyxl.

## Links to complete before submission

- Live application: https://career.reunia.app
- Public repository: set `OPEN_ATLAS_REPOSITORY_URL` and add the final URL here.
- Demo video: set `OPEN_ATLAS_DEMO_VIDEO_URL` and add the final public or unlisted URL here.

## Track selection

- Career & Talent
- Newcomer Settlement

## Disclosure

Réunia Career Bridge was developed during the Open Atlas submission period by bringing together two earlier codebases at different stages of maturity. A rudimentary Resume Tailor prototype existed before the June 20, 2026 submission window and supplied early résumé import, job-analysis, tailoring, evidence-review, comparison, and export concepts. Réunia, an AI meeting-assistant foundation, began during the submission period and was underway no later than June 22, 2026. The two codebases began being integrated into Career Bridge on July 28, 2026. During the hackathon, the foundations were substantially redesigned and expanded into the unified Career Bridge journey with newcomer-focused profile and translation workflows, a reusable evidence library, job discovery and ranking, application-specific résumé lifecycle, interview preparation, adaptive mock interviews, scorecards, action planning, durable background jobs, production persistence, deployment gates, and a consolidated career-focused interface. The conservative component-by-component disclosure is in `docs/submission/PREEXISTING_COMPONENTS.md`, with a concise chronology in `docs/submission/project-history/`.
