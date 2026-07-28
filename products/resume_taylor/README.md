# Evidence-Based Resume Tailor — Flask Edition

A local Flask web application that tailors a verified Candidate Profile to a job description without converting unsupported job requirements into candidate claims.

This edition replaces the former Streamlit interface with Flask routes, Jinja templates, HTML forms, browser-side JavaScript, and server-side workflow state. The AI, evidence validation, Resume Report scoring, optimization, and Word-export logic remain modular Python components.

## Security notice

The original project previously contained an OpenAI API key in source code. Revoke any exposed key and create a replacement. This project contains no API key.

Store the replacement only in a local `.env` file or the `OPENAI_API_KEY` environment variable. `.env` is excluded by `.gitignore`.

## Main capabilities

- Automatic Initial Resume Report on the Candidate Profile as-is
- Structured job-requirement analysis and ignored-boilerplate detection
- Evidence matching for every job requirement
- Interactive candidate-confirmation questions before proposal review
- Yes/No, Yes/No with details, short text, long text, number, and date-range answers
- Traceable supplemental evidence using `CONF-` identifiers
- Editable summary, skills, and experience bullets
- Live red/green word-level comparison while proposed wording is edited
- Automatic Job-Aligned Resume Report for Step 3, automatic Final Resume Report for Step 4, and three-version comparison
- Hard skills, Evidence & Gaps, Content Quality, Searchability, Recruiter tips, Formatting, and Soft skills scores, ordered by importance
- Deterministic validation plus an independent Step 3 evidence review
- Candidate-and-job-aware recommendations for career stage, resume format, and visual design, with composable `.docx` generation that avoids duplicating templates
- Persistent Applications tracker with submitted-resume snapshots, hiring stages, follow-up dates, screening/interview/offer outcomes, and conversion metrics
- Balanced, Fast, Maximum accuracy, Testing, and Custom model configurations
- Very-low-cost GPT-4o mini testing preset

## Flask architecture

The browser cookie contains only an opaque signed workflow ID and a CSRF token. Candidate data, proposals, report objects, audit results, and active generated Word bytes remain in process-local server memory. Application-tracker records and their immutable submitted-resume snapshots are stored persistently in `instance/applications.sqlite3`, keyed by the opaque browser workflow ID.

This is appropriate for the bundled local desktop-style workflow. Restarting the Flask process clears the active tailoring workflow but preserves tracked applications. For a multi-worker or public deployment, replace `InMemoryWorkflowStore` with Redis or another shared server-side store and associate application records with authenticated user accounts.

All modifying routes use POST and validate a CSRF token. Contact details are excluded from OpenAI prompts.

## Windows installation

1. Install Python 3.11 or newer.
2. Run `install.bat`.
3. Copy `.env.example` to `.env`.
4. Add a newly generated API key:

```text
OPENAI_API_KEY=your-new-key
FLASK_SECRET_KEY=replace-with-a-long-random-secret
```

5. Run `run_app.bat`.

The launcher opens `http://127.0.0.1:5000` in the default browser and starts Flask locally.

## Application layout and workflow

The interface uses one guided resume workflow plus dedicated Reports and Configuration screens. Resume editing is embedded directly in the stage where it is needed, so users do not have to move through a separate editor screen.

### Application Builder workflow

The module opens on a multi-application dashboard. Each application stores its company, job title, status, resume version, interview readiness, next action, and upcoming deadline or interview. Opening an application starts or resumes its own six-step resume workflow. Only three meaningful resume versions are named: **Initial Resume**, **Job-Aligned Resume**, and **Final Resume**.

1. **Career and Job Setup**: review the read-only source resume, enter the target company, title, and job description, then start the analysis.
2. **Confirm Relevant Experience**: answer only the evidence questions needed for the target role. Confirmed content is stored as traceable evidence and attached to the appropriate work experience.
3. **Review Tailored Resume**: review **Initial Resume → Job-Aligned Resume**, inspect the discreet **Why this changed** explanations, and correct any inaccurate wording.
4. **Improve Resume Quality**: use the protected report scores to apply safe content, searchability, recruiter, and soft-skill improvements.
5. **Finalize Resume**: choose the career stage, resume format, and visual design, then review the strongest final version.
6. **Evidence Review and Export**: review the final evidence report and export PDF or Word. Saving the final resume attaches the exact snapshot to the selected job application.

The delivery layer maps these six user-facing steps onto the mature internal `initial`, `confirmation`, `draft`, and `final` snapshots. This preserves the existing resume engine while making its responsibilities clearer inside Career Bridge.

The embedded editors show live additions in green, removals in red with strikethrough, version comparison, and resume quality-check controls. Evidence notes and matched requirements remain available under **Why this changed → View supporting details**. Editing the Final Resume refreshes the Final Resume Report and the PDF/Word export source when the changes are saved.

### Resume Reports

The reports tab contains four views:

- **Initial Resume (Step 1)** — automatically scores the original Candidate Profile as an immutable baseline when tailoring starts.
- **Job-Aligned Resume (Step 3)** — automatically scores the exact resume created after experience confirmation and refreshes after every saved Step 3 change.
- **Final Resume (Steps 4–6)** — begins during Improve Resume Quality, refreshes after saved Final Resume edits, and is finalized during Evidence Review and Export; PDF is the primary download and Word remains available when an employer requests DOCX.
- **Comparison** — supports Initial → Job-Aligned, Job-Aligned → Final, and Initial → Final score comparisons.

The Job-Aligned and Final Resume Reports include:

- **Searchability** — candidate/contact completeness, summary, section headings, title match, dates, education, and file type/name
- **Hard skills** — exact resume versus job-description occurrence counts with weighted coverage
- **Soft skills** — exact occurrence counts and medium-impact scoring
- **Content Quality** — data/structure integrity, semantic requirement matching, grammar/spelling, metric integrity, readability, writing consistency, and content focus
- **Recruiter tips** — job level, measurable results, tone, web presence, and word count
- **Formatting** — ATS layout, fonts, bullet/spacing/heading consistency, headers, footers, margins, page size, and configurable page-limit validation from the generated `.docx`
- **Evidence & Gaps** — requirement priority, evidence status, resume representation, evidence location, rationale, action, and score

Each report category identifies the workflow stage that owns it:

| Report category | Primarily improved in | Also verified in |
|---|---|---|
| Hard skills | Review Tailored Resume | — |
| Evidence & Gaps | Evidence Review and Export | — |
| Content Quality | Improve Resume Quality | — |
| Searchability | Improve Resume Quality | — |
| Recruiter tips | Improve Resume Quality | — |
| Formatting | Finalize Resume | Evidence Review and Export |
| Soft skills | Improve Resume Quality | — |

The Overall Score weights are:

| Section | Weight |
|---|---:|
| Hard skills | 25% |
| Content Quality | 15% |
| Evidence & Gaps | 15% |
| Searchability | 15% |
| Recruiter tips | 12% |
| Formatting | 10% |
| Soft skills | 8% |

Reports are mandatory workflow artifacts and are generated automatically at their corresponding steps. The Reports tab is primarily for review and comparison; secondary rerun actions remain available for recovery or manual refresh. A report-generation failure is shown clearly but does not discard a valid resume or block export.

Set `RESUME_PAGE_LIMIT=1` or `RESUME_PAGE_LIMIT=2` to choose the enforced page maximum. The report uses a rendered page count when a compatible document renderer is available and a conservative estimate otherwise.


### Applications

The Applications screen closes the loop between resume tailoring and real hiring outcomes. A completed Final Resume can be saved directly as a planned application, automatically carrying over:

- Company and role
- Exact submitted Word resume snapshot
- Resume version and template
- Job-alignment and overall scores
- Application date

Each record can then be updated through **Planned**, **Applied**, **Screening**, **Interview**, **Offer**, **Rejected**, or **Withdrawn**. Screening-call, interview, and offer outcomes are stored separately so the history remains accurate even when the current status later becomes Rejected or Withdrawn. Users can also save a job-posting link, notes, and a next follow-up date.

The dashboard reports applications tracked, submissions, screening-call rate, interview rate, offer rate, average alignment score for applications that reached interview, and follow-ups due. These are descriptive personal metrics and are not presented as proof that a score or template caused an employer response.

### Configuration

The Configuration tab contains:

- Processing mode and custom model controls
- Active routine and Step 3 evidence-review model details
- OpenAI API-key readiness
- Candidate Profile loading, restoration, and download
- Workflow reset controls

New browser sessions start in **Testing — GPT-4o mini (very low cost)**, while users can select and save another processing mode.

## Model configuration

| Mode | Routine work | Evidence review (Step 3) | Purpose |
|---|---|---|---|
| Balanced — recommended | `gpt-5.6-terra`, low | `gpt-5.6-sol`, medium | Quality, speed, and cost balance |
| Fast and economical | `gpt-5.6-luna`, low | `gpt-5.6-terra`, medium | Lower-cost routine processing |
| Maximum accuracy | `gpt-5.6-sol`, medium | `gpt-5.6-sol`, high | Quality-first processing |
| Testing — GPT-4o mini | `gpt-4o-mini` | `gpt-4o-mini` | Very-low-cost functional testing |

| Custom models | User-defined | User-defined | Separate settings for both workloads |

New browser sessions start in **Testing — GPT-4o mini (very low cost)**. The selector still allows another mode to be saved for the current workflow.

Testing mode can miss subtle evidence issues. Review its output manually before using a resume for a real application.

## Resume templates and PDF/Word export

The resume exporter composes three independent choices:

1. **Career stage** — Early Career, Mid-Career Professional, or Executive Leadership. This controls density, accomplishment scope, spacing, and education-versus-leadership emphasis.
2. **Resume format** — Standard Professional, Technical / Engineering, Career Changer / Hybrid, or Freelance / Project-Based. This controls section labels, section order, and skill-category priority while retaining the same verified content.
3. **Visual design** — Corporate or Modern. This controls typography, alignment, spacing, borders, and restrained accent treatment.

The implementation still requires only three clean base templates, one for each career-stage geometry:

- `data/resume_template_early_career.docx`
- `data/resume_template_professional.docx`
- `data/resume_template_executive.docx`

None of the templates contains a candidate name, contact details, employers, education entries, experience markers, tables, images, or hardcoded hyperlinks. The exporter rebuilds the complete document from the Candidate Profile and approved resume content each time, then applies the selected format and visual-design rules programmatically.

The app recommends each dimension independently from the target role. For example, a software-engineering role can receive Mid-Career + Technical / Engineering + Modern, while a regulatory banking role can receive Mid-Career + Technical / Engineering + Corporate. Changing any preference does not rerun AI optimization or alter verified resume wording. It regenerates the styled export source, clears any cached PDF, and refreshes formatting-sensitive Resume Reports. Every combination uses US Letter dimensions, a single-column ATS-friendly layout, empty headers and footers, and right-aligned date tab stops.

Final downloads use the concise filename `First_Last_Target_Role_Resume.pdf` or `First_Last_Target_Role_Resume.docx`. Spaces and unsupported characters are replaced with underscores, accented names are transliterated safely, internal style names are omitted, and stems are capped at 80 characters. PDF export is generated directly inside the Python application from the same approved resume data and selected style as the Word export, so Microsoft Word and LibreOffice are not required.

Candidate web links can be supplied through the optional contact fields:

```json
{
  "linkedin_label": "LinkedIn",
  "linkedin_url": "https://www.linkedin.com/in/example/",
  "github_label": "GitHub",
  "github_url": "https://github.com/example"
}
```

Rebuild all style templates after changing the shared style definitions with:

```bash
python scripts/build_resume_template.py data --all
```

A single template can also be rebuilt with `--style early_career`, `--style professional`, or `--style executive`.

## Environment variables

`.env.example` documents all supported variables:

```text
OPENAI_API_KEY=
OPENAI_ANALYSIS_TAILORING_MODEL=gpt-5.6-terra
OPENAI_EVIDENCE_REVIEW_MODEL=gpt-5.6-sol
OPENAI_ANALYSIS_TAILORING_REASONING_EFFORT=low
OPENAI_EVIDENCE_REVIEW_REASONING_EFFORT=medium
FLASK_SECRET_KEY=replace-with-a-long-random-secret
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_DEBUG=false
FLASK_COOKIE_SECURE=false
```

Use `FLASK_COOKIE_SECURE=true` only when the app is served through HTTPS.

The OpenAI variable names mirror the labels in the Configuration panel:

- `ANALYSIS_TAILORING` controls job analysis, resume tailoring, refinements, and quality improvements.
- `EVIDENCE_REVIEW` controls the independent evidence review performed in Step 3.

Only the environment variable names shown above are supported.

## Tests

Install development dependencies:

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Run:

```bat
.venv\Scripts\python -m pytest
```

The suite covers profile loading, prompts, confirmation evidence, validation, model configuration, report scoring, exact skill comparisons, formatting inspection, Word generation, text differences, Flask routes and templates, workflow ordering, optimization score guards, and secret scanning.

## Project structure

```text
app.py                          Flask application factory, routes, downloads, and workflow actions
templates/base.html             Application shell and top-level navigation
templates/index.html            Four-step tailoring workflow, embedded resume editing, reports, and configuration
static/styles.css               Responsive styling and report/diff presentation
static/app.js                   Tabs, live word differences, conditional questions, loading overlay
resume_tailor/web_state.py      Thread-safe process-local server workflow store
resume_tailor/ai.py             OpenAI calls and structured response handling
resume_tailor/model_config.py   Processing presets and model configuration
resume_tailor/confirmation.py   Candidate-answer validation and supplemental evidence
resume_tailor/models.py         Strict Pydantic data contracts
resume_tailor/prompts.py        Analysis, proposal, audit, refinement, and correction prompts
resume_tailor/validation.py     Deterministic evidence validation
resume_tailor/docx_export.py    Dynamic Word document generation
resume_tailor/pdf_export.py     Native styled PDF export with no desktop dependency
resume_tailor/export_naming.py  Professional PDF/DOCX filename rules
resume_tailor/docx_styles.py    ATS-friendly Word styles and page layout
resume_tailor/resume_report.py  ATS, recruiter, formatting, and evidence scoring
resume_tailor/profile_io.py     Candidate Profile loading
data/candidate_profile.json     Verified source facts
data/resume_template_early_career.docx Early Career style-only template
data/resume_template_professional.docx Mid-Career Professional style-only template based on the attached original resume
data/resume_template_executive.docx Executive Leadership style-only template
scripts/build_resume_template.py Rebuilds one or all Word style templates
data/job_description_example.txt
install.bat
run_app.bat
requirements.txt
requirements-dev.txt
wsgi.py                        WSGI entry point for deployment
```


## Tailored Draft change explanations

When the Job-Aligned Resume is compared with the permanent Initial Resume, Review Tailored Resume uses one compact **Job Alignment** summary immediately above the Draft comparison instead of separate introductory and explanation panels. The collapsed summary shows the number of applied change groups and keeps the breakdown behind **View details**. Modified, added, rewritten, and intentionally excluded content shows a small **Why this changed** link that reveals one concise reason. Report impact is displayed only when a positive score change can be uniquely and directly attributed to the specific edit; unchanged, negative, duplicate-label, and ambiguous metrics are silently suppressed. Every source bullet must have a structured inclusion decision. If proposal generation omits a bullet record, the app treats that omission as a generation defect and automatically restores the original Candidate Profile wording before the comparison is rendered. The user therefore reviews only real tailoring decisions and never has to resolve internal mapping failures. Full comparison, editable wording, evidence, and technical details remain behind **View comparison and supporting details** so the page stays easy to scan.


## Quality improvement, finalization, and export

Steps 4–6 use the **Final Resume Report** as one coordinated checklist while keeping quality improvement, visual finalization, and evidence/export decisions visible as separate user stages.

- The app evaluates **Content Quality**, **Searchability**, **Recruiter tips**, **Formatting**, and **Soft skills**.
- Safe changes are applied only when they can be supported by the Candidate Profile or confirmed evidence.
- The stage automatically applies every safe local repair, then deterministic checks run after automatic changes without requiring an additional AI call.
- The comparison uses the exact saved Step 3 baseline: **Job-Aligned Resume → Final Resume**.
- When the optimized content is identical to the baseline, the app shows that no safe content change was required instead of creating another named version.
- The Final Resume Report and styled export source are created together after the quality pass. PDF is the primary download; Word remains the secondary option. Remaining quality recommendations are advisory.

### Improve Resume Quality recommendations

Improve Resume Quality applies safe report recommendations and keeps remaining recommendations advisory. The final evidence audit is presented separately in Evidence Review and Export. Findings are processed in category-specific batches of at most three. After every batch, the application rebuilds the report and keeps the candidate version only when the overall score, job-match score, Hard Skills, Evidence & Gaps, the combined optimization-category score, and the targeted category all remain equal or improve. A batch that adds validation issues or lowers a protected metric is rolled back automatically. On a rerun, a manually edited Final Resume that scores below the saved Job-Aligned Resume is restored to that stronger baseline before optimization continues.

### Performance safeguards

The confirmation and optimization routes avoid unnecessary sequential work:

- An all-`No` confirmation round skips the proposal-refinement model call because no new evidence needs to be incorporated.
- Post-confirmation evidence review uses the analysis and tailoring model for one combined repair request and keeps the evidence review model for independent verification. Candidate-dependent fallback wording is restored locally from verified source content instead of making repeated repair calls.
- Step 4 reuses current Job-Aligned and Final Resume Reports when their proposal fingerprints match.
- Only report findings that can be changed through resume content are sent to the optimizer; profile-owned and template-owned checks remain advisory instead of producing no-op AI calls.
- Intermediate batch score guards use estimated page counts, avoiding a Word-to-PDF conversion after every candidate batch. The accepted Final Resume still receives an exact rendered page-count check before export.
- The Final Resume Report inspects the Word bytes already created for download rather than generating the same document twice.
- Server logs include `AI timing` entries with operation, model, reasoning effort, attempt, and elapsed seconds to make future latency diagnosis straightforward.
