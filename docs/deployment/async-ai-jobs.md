# Durable AI jobs

Career Bridge keeps long AI and export work outside public Flask requests. The web process validates input, persists the canonical application/workflow state, creates a compact `AsyncJob`, and returns immediately. A separate private worker claims jobs through a lease and persists progress after each completed phase.

## Supported jobs

- Job Discovery assessment
- Interview Preparation generation
- Baseline Resume target-language generation
- Job analysis, Target-Market Review, and initial tailoring
- Initial, Job-Aligned, and Final Resume Report regeneration
- Final Resume optimization and independent evidence review
- Final Word and PDF generation

The Resume Workflow stores only identifiers, fingerprints, guards, and result URLs in the queue item. Resume content remains in the canonical DynamoDB workflow record, while large generated documents are externalized to private object storage.

## Worker

Run one or more private workers with the same durable storage configuration as the web application:

```bash
python -m job_discovery.background_worker --poll --interval 5
```

Do not expose a public endpoint for the worker. For AWS, the same handler can be invoked from a scheduled Lambda through `job_discovery.background_worker.lambda_handler`.

## Resume job safety

Resume jobs include a guard over the imported source, job description, target settings, and selected AI models. If those inputs change after submission, the worker rejects the stale job instead of overwriting newer work.

The worker saves workflow state after bounded phases and advances the durable cursor. Reclaimed jobs reuse saved analysis, translations, and proposals. Final optimization uses one provider attempt with `CAREER_BRIDGE_RESUME_ASYNC_AI_TIMEOUT_SECONDS` (default 240 seconds, bounded to 30–300). If optional wording optimization fails, the evidence-reviewed resume is preserved and export continues without exposing provider details.

Only one Resume Workflow operation may run per application at a time. Repeating the same submission reconnects to the existing job; a conflicting operation is blocked until the active job completes or is canceled.

## User experience

Resume pages render an explicit durable-job panel showing status, progress, cancellation, retry, and the result link. The browser polls the job record rather than holding the original HTTP request open. Closing the page or replacing the web container does not cancel accepted work.

## Storage and retention

The async queue can share `careerbridge_job_discovery` through reserved key prefixes. No additional index is required. Job records use the configured async retention period. Private workflow documents continue to follow the lifecycle rules of `CAREER_BRIDGE_DOCUMENTS_BUCKET`.

## Deployment validation

A production deployment needs:

- `CAREER_BRIDGE_ASYNC_JOB_STORAGE_BACKEND=dynamodb`
- `CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME=careerbridge_job_discovery`
- `CAREER_BRIDGE_ASYNC_WORKER_HEARTBEAT_INTERVAL_SECONDS=15`
- `CAREER_BRIDGE_ASYNC_WORKER_MAX_AGE_SECONDS=90`
- durable workflow storage and private document storage
- at least one continuously running or frequently scheduled worker

The public web image uses a 180-second Gunicorn timeout. Long Resume Workflow work must not be moved back into routes to compensate by increasing that timeout.


## Worker heartbeat

The worker stores one reserved `ASYNC#WORKER#HEARTBEAT` record in the async-job table. It is refreshed while the worker is idle and from a dedicated heartbeat thread while a long AI request is running. The record includes the worker ID, start time, current state, current job ID/type, processed-job count, and latest heartbeat timestamp. A 24-hour DynamoDB TTL removes abandoned metadata automatically.

The web health endpoint returns:

```json
{
  "async_worker": {
    "status": "healthy",
    "last_heartbeat_at": "2026-08-04T19:00:00+00:00",
    "age_seconds": 12,
    "max_age_seconds": 90,
    "state": "idle"
  }
}
```

Possible status values are `healthy`, `stale`, `missing`, `stopping`, `invalid`, and `unavailable`. The deployment validator requires `healthy`; it fails prominently before the application smoke test when the worker is not recent. No additional table or index is required.
