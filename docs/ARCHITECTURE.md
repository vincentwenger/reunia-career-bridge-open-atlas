# Career Bridge Architecture

## Goal

Create one Career Bridge product that reuses mature capabilities from Réunia and Resume Taylor while keeping integration reversible, auditable, and centered on a candidate's job application.

## Layering

1. **Domain (`career_bridge/domain`)** — stable career concepts and invariants.
2. **Application (`career_bridge/application`)** — orchestration independent of Flask, AWS, SQLite, Redis, or OpenAI.
3. **Ports (`career_bridge/ports.py`)** — contracts implemented by product adapters.
4. **Adapters** — thin wrappers around existing services in `products/`.
5. **Delivery** — the Réunia shell plus path-mounted Application Builder and interview workspaces.

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

## Career Bridge navigation adapter

The first delivery-layer integration uses the shared `career_bridge.presentation` information architecture. The navigation is ordered around one `JobApplication`, not around Réunia's previous meeting lifecycle.

| Workspace | Shared relationship | Current delivery adapter |
|---|---|---|
| Career Profile | `candidate_profile_id`, `career_background_id`, `resume_id`, `evidence_library_id` | Réunia profile workspace |
| Application Builder | target job, tailored resume versions, status | Resume Taylor multi-application dashboard and six-step workflow |
| Interview Preparation | `interview_preparation_id` | Réunia knowledge/materials workspace |
| Mock Interview | `mock_interview_session_ids` | Réunia recorder |
| Interview Review | mock session transcripts and scorecards | Réunia meeting review |
| Career Action Plan | `improvement_action_ids` | Réunia Action Center |
| Progress | `status`, `status_history`, normalized scores | Réunia analytics |
| Help & Support | not aggregate-owned | Réunia support workflow |

This is an anti-corruption mapping, not a claim that legacy meeting records have become job applications. Future adapters must resolve the selected application and attach generated resume, preparation, mock-session, score, and action records through the shared repository contracts.

Administration remains outside the eight-step candidate workflow. It is rendered as a separate control only when the session is administrative, and the existing server-side authorization decorator remains authoritative.

## Application Builder service boundary

The Application Builder is composed into the Career Bridge URL space at `/application-builder/`, but remains a separate process because the imported products require incompatible OpenAI SDK major versions. Réunia owns authentication and the primary navigation. The builder reads the shared Flask session cookie, uses the Réunia `user_id` as its owner key, and persists application dashboard records in its existing SQLite adapter.

The visible six-step workflow maps onto the mature Resume Taylor engine without renaming its internal snapshot lifecycle:

| Career Bridge step | Existing engine stage | Shared aggregate relationship |
|---|---|---|
| Career and Job Setup | `initial` | source resume and target job |
| Confirm Relevant Experience | `confirmation` | evidence library selection |
| Review Tailored Resume | `draft` | tailored resume versions |
| Improve Resume Quality | `final` quality pass | tailored resume version and quality scores |
| Finalize Resume | `final` editor/export preparation | current tailored resume version |
| Evidence Review and Export | `final` evidence report/export | current version and evidence library |

The dashboard is a read model above this workflow. It does not replace the full `JobApplicationBundle`; it provides the cross-module fields needed to select the active application and continue into resume or interview capabilities.
