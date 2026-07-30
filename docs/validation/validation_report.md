# Career Bridge Product-State and Architecture Validation

## Shared loading, empty, and error states

Career Bridge now has a reusable workspace-state system instead of relying only on page-specific markup, flash messages, or toast notifications.

The shared system includes:

- A Jinja `workspace_state(...)` macro for accessible loading, empty, and error panels.
- Shared `.app-state` styling, including compact and responsive variants.
- Shared `AppUI.setWorkspaceState`, `showWorkspaceState`, and `hideWorkspaceState` helpers.
- Local retry or recovery actions for asynchronous failures.
- Preserved page-specific behavior where it adds value, including the Application Builder's staged loading overlay.

The shared state contract is applied to these major workspaces:

1. Homepage career journey.
2. Application Builder.
3. Interview Preparation.
4. Career Action Plan.
5. Social Impact & Career Progress.
6. Admin Analytics.
7. Career Evidence Search.
8. Adaptive Mock Interview.

A dependency-free automated contract test verifies that every listed workspace declares loading, empty, and error states and that asynchronous error states expose wired retry controls.

## Application Builder architecture

The Application Builder remains validated as a Blueprint inside the single Réunia Flask application.

The architecture validator checks:

1. Builder exceptions use Réunia's centralized recovery-focused 500 page.
2. Missing `/applications/...` routes use Réunia's centralized 404 page.
3. Builder forms use Réunia's `_csrf_token` and global `csrf_token()` helper.
4. `/app` and `/applications/` use the same authenticated session.
5. Builder assets resolve through `application_builder.static`; shared assets use Réunia's static endpoint.
6. Workflow and application stores are initialized once per Flask app.
7. Builder routes and `url_for()` references use the `application_builder.*` namespace.

## Validation commands

Workspace-state contract:

```bash
python -m unittest -v tests.contracts.test_workspace_states
```

Dependency-free Application Builder architecture validation:

```bash
python tests/validators/validate_application_builder_architecture.py
```

Python compilation:

```bash
python -m compileall -q app.py career_bridge products/resume_taylor products/reunia/meeting_assistant tests
```

JavaScript syntax validation:

```bash
node --check products/reunia/static/js/common.js
node --check products/reunia/static/js/pages/index.js
node --check products/reunia/static/js/pages/action-center.js
node --check products/reunia/static/js/pages/analytics.js
node --check products/reunia/static/js/pages/admin-analytics.js
node --check products/reunia/static/js/pages/knowledge.js
node --check products/reunia/static/js/pages/meeting-recorder.js
node --check products/resume_taylor/static/app.js
```

Runtime Flask request validation, after installing `requirements.txt`:

```bash
python -m unittest -v tests.integration.test_application_builder
```

The runtime suite forces an exception in a real Builder route, exercises the shared CSRF handler, requests shell and Builder pages with one session, fetches static assets, and verifies store identities. It requires Flask and the project dependencies to be installed.

## International career profile regression testing

Career Bridge now includes six deterministic international candidate scenarios rather than relying on one example profile:

1. Internationally trained accountant with a French credential and unfamiliar title.
2. Educator changing careers into customer success.
3. Brazilian software engineer with limited U.S. experience.
4. Kenyan procurement professional whose official title needs U.S.-market explanation.
5. Philippine nurse missing a mandatory Oregon license.
6. Moroccan multilingual project coordinator with French and Arabic resume content.

The fixtures live in `tests/fixtures/international_profiles/`. Each scenario contains a complete Candidate Profile, international career context, structured job analysis, evidence-grounded tailoring proposal, and expected outcomes.

The automated suite verifies:

- Candidate Profile and job-analysis schema validation.
- Unicode and multilingual content preservation.
- Safe international title and credential explanations.
- Transferable-skill findings for career changes and cross-market experience.
- Unsupported target requirements and mandatory eligibility blockers.
- Deterministic proposal integrity with no invented skills, numbers, bullets, or evidence IDs.
- Separation of onboarding context from verified interview evidence.
- Removal of unverified evidence references from Interview Preparation.

Run directly:

```bash
python -m unittest -v tests.regression.test_international_career_profiles
```

Or use the dedicated validator:

```bash
python tests/validators/validate_international_career_profiles.py
```

## Application Builder OpenAI cost controls

The Application Builder now uses Réunia's shared AI infrastructure for every structured OpenAI operation, including resume import, job analysis, tailoring, refinement, evidence review, suggested fixes, and interview preparation.

The integration adds:

1. Global and per-user budget reservation through `AICostControlService` before each provider attempt.
2. Settlement from the provider's actual token-usage report using the configured model pricing table.
3. Explicit operation-specific `max_completion_tokens` ceilings.
4. User-scoped response caching through the shared Redis or in-memory `ai_response_cache`.
5. A two-attempt default with a hard three-attempt ceiling.
6. Conservative retained reservations for ambiguous timeout/connection/5xx failures.
7. Durable, content-free AI usage metrics for Application Builder features.
8. Standard pricing entries for GPT-5.6 Sol, Terra, and Luna, still overridable through `ANALYTICS_AI_PRICING_JSON`.

Dependency-free validation:

```bash
python tests/validators/validate_application_builder_ai_cost_controls.py
```

Runtime unit validation, after installing `requirements.txt`:

```bash
python -m unittest -v tests.integration.test_resume_ai_cost_controls
```

## No-invented-experience grounding and regression validation

Career Bridge now applies a shared deterministic evidence-grounding layer after model generation, not only prompt instructions. The layer blocks or sanitizes high-risk unsupported content including new numbers, named entities and technologies, credentials, strengthened leadership/outcome/scope language, and low-overlap factual responsibilities.

Coverage now includes:

1. Resume import: imported Candidate Profile content is checked against the uploaded resume before acceptance.
2. Tailored resumes: professional summaries and included bullets are validated after generation, refinement, and suggested-fix operations. Unsafe bullets revert to their exact source wording; unsafe summaries are rebuilt only from verified source text.
3. Career Translation: a real evidence ID is no longer sufficient by itself. Positive translations are downgraded to clarification when their source wording or interpretation is not traceable to the cited evidence.
4. Resume reports: reports show an explicit pass/fail check named `Generated candidate claims are traceable to verified evidence`.
5. Interview Preparation: candidate answer focuses, strengths, challenge areas, and the personal introduction are post-validated against the exact cited evidence text.
6. Mock Interview: model-generated coaching, evaluation summaries, evidence suggestions, practice actions, and `sample_improved_answer` are sanitized before entering the scorecard. Unsafe sample answers fall back to the candidate's own answer.
7. Career Action Plan: interview-derived action wording is revalidated before publication and falls back to a generic evidence-safe action when needed.
8. Export and application attachment: DOCX/PDF export and saving a final resume to an application are blocked when generated candidate claims fail grounding, including when an older cached export exists.
9. Failure behavior: mock-interview and action grounding fail closed if the shared validator is unavailable.

The regression suite injects invented SAP S/4HANA work, fabricated team sizes, global/enterprise scope, unsupported leadership, unrelated employers, and invented responsibilities into all six international candidate scenarios. It verifies detection, deterministic repair, source-bullet restoration, valid-ID misuse detection, Career Translation downgrading, Interview Preparation filtering, report failure status, mock-interview sanitization, and action fallback behavior.

Run:

```bash
python tests/validators/validate_no_invented_experience.py
```

Current dependency-light result: 16 tests passed or were recognized correctly; 14 executed and passed, while the two Flask-runtime integration checks were skipped because Flask is not installed in this execution environment. All modified Python modules compile successfully. Run the same command after installing `requirements.txt` to execute the two runtime service checks as well.

This is a strong deterministic and regression-tested safeguard, not a mathematical proof that arbitrary natural-language paraphrases can never evade detection. Newly generated and exported outputs now have enforceable post-generation gates. Historical binary resume snapshots created before these controls should be regenerated from their evidence-bearing workflow if they require the same verification status.

## Final cross-product integration checks

Two direct regression tests now close the previously identified integration-test gaps:

1. `tests.integration.test_product_quality.ResumeFindingsPromptIntegrationTests` verifies that unsupported requirements, evidence-review warnings, Career Translation findings, Resume Report weaknesses, alignment changes, and excluded/questioned claims all reach the production Interview Preparation prompt.
2. `tests.integration.test_product_quality.InterviewScorecardActionIntegrationTests` executes the production Career Action Plan derivation path and verifies that weak answers and low overall interview scores create correctly linked actions while adversarial invented experience is removed.

Both direct tests pass in the supplied validation environment.

A browser test is also included at `tests/browser/test_international_profile_journey.py`. It seeds every international profile, launches the unified app, submits the actual Interview Preparation form in Chromium, and injects an unsafe mocked AI result at the provider boundary. The test asserts that invented team size, product, company, leadership, and scope claims do not reach saved or rendered output.

The browser and full Flask suites require the complete project runtime. They are implemented but could not execute in this container because `flask`, `openai`, `xlrd`, and an importable `redis` package are unavailable. This is recorded as a blocked validation phase rather than a pass.

Run all final checks in deployment or CI with:

```bash
python tests/run_final_integration_checks.py \
  --require-runtime \
  --json-output reports/validation/final-integration-check-results.json
```

See `docs/validation/final_integration_checks.md` for the detailed results and exact scope.
