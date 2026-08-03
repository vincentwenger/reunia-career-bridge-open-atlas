# Career Bridge UI Simplification Review

## Goal

Reduce visual and cognitive clutter while keeping every core workflow and administrator capability available.

## Product-wide rules applied

- One clear page title and, where needed, one contextual primary action.
- One global navigation system; the duplicate Application Builder sub-navigation was removed.
- Repeated page introductions, decorative labels, icons, and duplicate status cards were removed.
- Forms that are not needed for the next action are collapsed by default.
- Advanced filters, source imports, scheduling, edit forms, and destructive actions remain available inside expandable sections.
- Cards are flatter, tighter, and more consistent; unnecessary shadows, gradients, oversized spacing, and animation were removed.
- Administrator-only controls remain in the account menu and are not shown as ordinary candidate navigation.

## Simplified areas

### Global navigation

- Reduced the interface to four compact global areas: Foundation, Jobs & Applications, Interviews, and Progress.
- Removed descriptions and decorative icons from menu entries.
- Moved AI Configuration and Administration into the administrator account menu.

### Application Builder shell

- Removed the duplicate four-item sub-navigation.
- Removed repeated hero descriptions and redundant back buttons.
- Replaced the large selected-application panel with one compact status strip.

### Job Applications

- Reduced the summary to three useful metrics.
- Collapsed application creation when applications already exist.
- Reduced each application card to status, stage, readiness, resume, next action, and next date.
- Moved editing, outcomes, notes, and deletion into an expandable edit area.

### Job Discovery

- Removed the introductory card and repeated catalog explanation.
- Kept result categories and job cards compact.
- Moved matching controls and page-size selection into a collapsed filter section.
- Kept one main assessment action; secondary bulk assessment is under More.
- Kept source creation, import, scan history, refresh scheduling, advanced filters, and danger controls collapsed until needed.

### Baseline Resume

- Reduced setup to target country, destination language, optional target role, and resume upload.
- Removed the repeated Career Profile summary and long onboarding explanation.
- Kept one compact source/language summary and the resume preview.

### Resume Workflow and Reports

- Removed repeated workflow introductions, report status grids, privacy reminders, and version-map explanations.
- Kept the six workflow stages as a compact step navigator.
- Kept optional application context collapsed.
- Preserved evidence validation, comparisons, quality review, optimization, and exports.

### Interview Preparation

- Removed the duplicate selected-application context card.
- Replaced multiple readiness cards with a compact three-item readiness strip.
- Shortened the generation panel while preserving the generated preparation workspace.

## Preserved functionality

- Company source management and single-source scanning.
- Shared catalog refresh and scheduled refresh configuration.
- Job assessment, filtering, saving, ignoring, analysis, and application creation.
- Application tracking, automatic readiness, outcomes, follow-ups, and document links.
- Baseline Resume translation safeguards and destination-language generation.
- Six-step resume workflow, evidence grounding, reports, and DOCX/PDF export.
- Interview preparation and all administrator-only controls.

## Validation

- Full test suite: **491 passed, 36 skipped, 148 subtests passed**.
- Python compilation completed successfully.
- Final integration and regression checks passed where runtime dependencies were available.
- Full Flask/Playwright execution remains unavailable in the test image because its optional runtime dependencies are not installed.

## Version 210 follow-up

- Removed the duplicate Career Profile / Career Evidence Library / Application Materials / Interview Preparation sub-navigation from the shared knowledge template.
- The global Career Bridge navigation is now the single navigation system on those pages.
- Removed the associated unused desktop and responsive CSS.

## Version 211 follow-up

- Rebuilt the dashboard around one personalized recommended action.
- Kept one compact current-application card with a single **Open application** action.
- Replaced four metric cards and three large foundation cards with one compact Career Foundation list.
- Reduced secondary actions to Discover jobs, Create application, and Practice an interview.
- Removed the decorative hero panel, repeated workspace directory, module cards, and dashboard-only practice disclaimer.
- Removed duplicate Resume Workflow and Interview Preparation actions from the current-application card; application-specific resume tools remain available after opening the application.

### Version 211 validation

- Full discoverable suite: **531 passed, 36 skipped**.
- Dashboard navigation and workspace-state contracts passed.
- JavaScript syntax and Python compilation passed.
- Dependency-light final integration checks passed.
- Flask-backed and Playwright checks remain blocked only because optional runtime dependencies are not installed in the execution image.

## Version 212 follow-up

### Standardized resume terminology

- Uses **Imported Resume**, **Baseline Resume**, **Application Baseline**, **Job-Aligned Resume**, and **Final Resume** consistently in user-facing screens.
- Keeps legacy internal aliases only for compatibility with saved records.
- Renamed the global foundation destination from Career Translation to **Baseline Resume**.

### Reduced global navigation

- Uses four global groups only: **Foundation**, **Jobs & Applications**, **Interviews**, and **Progress**.
- Removed Resume Workflow, Resume Reports, and Application Materials from global navigation because they are application-specific.
- Preserved hidden active-state mapping so application-specific pages still highlight the correct global group.

### Simplified application cards

- Uses one primary action: **Open application**.
- Places Interview preparation, View posting, resume download, and editing under **More**.
- Renamed ambiguous labels to **Resume stage** and **Interview readiness**.
- Simplified application creation to one **Create application** action.

### Collapsed secondary controls

- Career Action Plan keeps Search and Status visible and places the remaining filters under **More filters**.
- Progress & Outcomes keeps four primary measurements visible and places five secondary measurements under **Additional measurements**.

### Consolidated navigation implementation

- Application Builder now includes the same shared `navbar.html` used by the rest of Career Bridge.
- Deleted the duplicate Application Builder navbar template.
- Replaced overlapping authenticated-navigation CSS variants with one final shared block.

### Version 212 validation

- Full discoverable Python suite: **531 passed, 36 optional dependency skips**.
- Modified Jinja templates parse successfully.
- Modified CSS files have balanced rule blocks.
- Dependency-light final integration checks pass.
- Flask-backed and Playwright phases remain blocked only because Flask, Redis, OpenAI, xlrd, and browser runtime dependencies are unavailable in the execution image.


## Version 213 follow-up — consistent compact blue workspaces

### Shared visual direction

- Retained navy blue as the structural product color and teal for active/progress states.
- Consolidated authenticated primary buttons onto the shared navy-blue treatment instead of layering a second page-specific override.
- Restored a blue gradient header to the simplified dashboard and added subtle blue structure to application, progress, and dashboard cards.
- Kept orange available only as a secondary attention/deadline token rather than the normal workspace CTA.

### Career Profile

- Removed the redundant introductory card and kept one compact profile-enabled control.
- Uses one Save Career Profile action instead of duplicate save buttons.
- Keeps Professional identity open and places the three longer sections in expandable panels.
- Simplified the Profile summary header and collapsed the detailed usage explanation.

### Career Evidence Library

- Removed the permanent-storage explanation card and redundant section eyebrow.
- Simplified the main heading to Documents and removed decorative collection icons.
- Uses the same pale-blue card headers, navy borders, and compact shadows as the rest of Career Bridge.

### Adaptive Mock Interview

- Shortened the page and setup explanations.
- Removed the duplicate Open Application Builder shortcut.
- Compacted interview-format choices into a two-column layout with blue selected states.
- Standardized setup, question, and session surfaces with the shared blue system.

### Interview Review

- Shortened tabs to Summary, Scorecard, Transcript, and Ask AI.
- Reduced the sidebar width and shortened its search and guidance text.
- Replaced oversized rounded panels and shadows with the compact shared card treatment.

### Career Action Plan

- Reduced the command bar to status plus Add action.
- Converted four large KPI cards into a compact summary row.
- Collapsed the secondary application-priority overview.
- Made the Actions table the primary page workspace and kept advanced filters collapsed.

### Validation

- Full discoverable suite: **537 passed, 36 optional dependency skips**.
- All modified Jinja templates parse successfully.
- New consistency contracts cover the five updated pages and the shared blue workspace treatment.

### Extracted resume information placement

- Moved automatically extracted employment roles and job-title interpretations from Career Evidence Library into Baseline Resume.
- Career Evidence Library now focuses on additional and user-confirmed evidence rather than duplicating fields sourced from the resume.
- Title clarification links from Confirm Relevant Experience open the relevant Baseline Resume panel directly.

## Career Profile source-of-truth simplification

- Career Profile now contains only Career direction, International and transition context, and Preferences and constraints.
- Resume-derived facts are managed in Baseline Resume and linked from a prominent source notice.
- Removed form fields remain backward-compatible in storage but are not cleared or presented as editable Career Profile data.
