# Career Bridge simplification pass

This pass removes predecessor meeting functionality that no longer supports the Career Bridge journey while preserving the current candidate-facing features.

## Removed

- Long-form browser recorder routes, processing service, job queue, worker, and recorder storage repository.
- Public Interview Review share links, share API, repository, service, modal, settings, page assets, and DynamoDB cleanup configuration.
- Generic predecessor transcript and meeting-knowledge aliases.
- Legacy meeting-material migration branches.
- Legacy feature flags and deployment requirements, including `RECORDER_JOBS_BUCKET` and `MEETING_SHARES_TABLE_NAME`.
- The unused production launcher that existed only to start the recorder worker.

## Kept

- Adaptive Mock Interview, including browser microphone capture.
- Short-answer transcription through `ShortAudioTranscriptionService`.
- Interview Review summaries, scorecards, transcripts, Ask AI, and coaching.
- Career Evidence Library, Career Profile, Application Materials, Career Action Plan, and Progress & Outcomes.
- Durable application, workflow, document, and Job Discovery storage checks.

## User-interface cleanup

- Removed the nonfunctional Share control from Interview Review.
- Removed Sharing Defaults from Settings.
- Renamed the remaining category to Data & Privacy and limited it to retention controls.
- Removed obsolete sharing guidance and translation entries.

## Recommended next simplification

The next high-value refactor is to split `products/resume_taylor/app.py` and the Resume Workflow template into smaller composition modules. They remain large, central files, so that work should be done separately with route and browser regression coverage rather than combined with this low-risk legacy removal.

## Repository cleanup

- Removed obsolete split content/form grading prompt modules after the unified Interview Scorecard prompt replaced them.
- Removed the unused descriptive module registry, which had no runtime or test consumers.
- Stopped committing generated `.min.css` and `.min.js` siblings; the Docker build still creates them before startup, and source-mode execution falls back safely.
- Removed stale generated validation reports from the source package; validation commands recreate `reports/validation/` when requested.
- Moved static-quality configuration into `config/quality/`.
- Moved the sample candidate profile into `tests/fixtures/` so production data contains only runtime assets.
