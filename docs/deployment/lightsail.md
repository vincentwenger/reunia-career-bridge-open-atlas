# Réunia Career Bridge — AWS Lightsail container

This deployment package contains the merged product:

- Réunia Career Bridge shell at `/` and `/app`
- Resume-tailoring Application Builder at `/applications/`
- Per-application Interview Preparation at `/applications/interview-preparation`
- Lightsail health check at `/health`

The Application Builder uses the direct `/applications/` route and no Nginx sidecar.

## Data-loss warning

> **WARNING: Redeploying or replacing the Lightsail container can erase Application Builder records.**
> The SQLite database is stored on ephemeral container-node storage. Export or
> migrate records to durable storage before any redeployment where retention matters.


## Build and run locally

```bash
docker build -t reunia-career-bridge .
docker run --rm -p 8000:8000 --env-file .env reunia-career-bridge
```

## Lightsail endpoint

- Container port: `8000`
- Public endpoint port: `8000`
- Health check path: `/health`
- Scale: `1`
- Lightsail container **Command**: leave empty
- Gunicorn workers: `1`
- Gunicorn threads: `4`

The health endpoint exposes the non-secret Application Builder storage mode for
deployment validation:

```json
{
  "status": "ok",
  "services": ["reunia", "application-builder"],
  "application_builder": {
    "workflow_storage": "memory",
    "application_storage": "sqlite",
    "durability": "demo-only",
    "multi_worker_safe": false,
    "multi_node_safe": false
  }
}
```

This metadata documents the application limitation; it does not detect the
actual Gunicorn worker count or Lightsail service scale.

Use the same environment variables as the existing Réunia deployment. The
Application Builder additionally reads `OPENAI_API_KEY` and optionally
`CAREER_BRIDGE_APPLICATIONS_DB`.

## Required single-process runtime

The current Application Builder database is SQLite and its active workflow
state is process-local. The complete deployment invariant is therefore:

```text
Lightsail scale = 1
Lightsail command override = none
Gunicorn workers = 1
Gunicorn threads = 4
```

The Docker image already supplies the safe startup command:

```text
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 ... app:app
```

At application startup, Réunia emits a prominent warning with the active
Application Builder persistence configuration. In the Lightsail image it reads:

```text
Application Builder persistence:
- workflow backend: process memory
- application backend: SQLite
- database path: /app/instance/career_bridge_applications.sqlite3
- safe only with one Gunicorn worker and Lightsail scale 1
- records may be lost during container replacement
```

The path is resolved from `APPLICATIONS_DB_PATH` /
`CAREER_BRIDGE_APPLICATIONS_DB`, so the log reports the actual configured path
when an override is used. The warning is emitted once per application process.

Do not enter a custom command in the Lightsail container configuration. In
particular, a command containing `gunicorn --workers 2` creates two independent
processes and splits process-local workflow state even when the Lightsail
service scale remains `1`. Leave the Lightsail **Command** field empty so the
container uses the image's existing `CMD`.

The Windows deployment script at `scripts/deployment/upload_to_lightsail.bat`:

1. builds and pushes the image;
2. checks the current Lightsail deployment and stops if any container command
   override is present;
3. sets the service scale to `1`; and
4. queries the service to verify the returned scale.

The script never supplies a Lightsail command override. It exits with a nonzero
status and a prominent error banner if the command preflight, scale update,
query, or verification fails. The preflight occurs before the scale update
because changing Lightsail capacity redeploys the current deployment.

## Post-deployment validation

Run the standalone validator after the Lightsail deployment is active. It verifies:

1. the live Lightsail service scale is exactly `1`;
2. the public application container has no Lightsail command override and the
   Docker image `CMD` contains exactly one Gunicorn worker and four threads;
3. `/health` returns HTTP 200 with the documented storage limitations;
4. an authenticated smoke-test application can be created and retrieved through
   the real Application Builder workflow; and
5. this document contains the prominent redeployment data-loss warning.

The test application is removed after successful retrieval unless
`--keep-test-application` is supplied. Use a dedicated low-privilege validation
account rather than a personal account. The password is read from the environment
and is never printed.

Windows Command Prompt example:

```bat
set CAREER_BRIDGE_BASE_URL=https://career.reunia.app
set DEPLOYMENT_VALIDATION_EMAIL=deployment-validator@example.com
set DEPLOYMENT_VALIDATION_PASSWORD=replace-with-the-account-password
scripts\deployment\validate_lightsail_deployment.bat
```

Equivalent direct Python invocation after setting the same credential
environment variables:

```bash
python scripts/deployment/validate_lightsail_deployment.py \
  --base-url https://career.reunia.app
```

The defaults are region `us-west-2` and service name
`reunia-career-bridge`. Override them with `AWS_REGION`,
`LIGHTSAIL_SERVICE`, `--region`, or `--service-name`. The validator requires a
configured AWS CLI identity with permission to call
`lightsail:GetContainerServices`.

Interview Preparation records are stored in the same SQLite database and include
a snapshot of the exact verified evidence used for generation. A workspace is
marked for regeneration when the company, target role, job description, or
verified evidence changes. Generation requires either completed candidate
evidence confirmation in the active workflow or an attached evidence-reviewed
Final Resume.

## Restricted Live Interview Assistance

Live Interview Assistance is restored but hidden from standard accounts. Access
is enforced by the server for the page, live stream, audio-chunk endpoint, and
cancellation endpoint.

- Administrators configured through `ADMIN_USER_IDS` or `is_admin=true` always have access.
- Approved groups are configured with the comma-separated
  `LIVE_INTERVIEW_ASSISTANCE_GROUPS` variable. Its default groups are
  `career_bridge_beta` and `career_coaches`. A user's DynamoDB record must contain
  a matching `groups` list.
- Specific users can be allowlisted with the comma-separated
  `LIVE_INTERVIEW_ASSISTANCE_USER_IDS` variable.
- In **Admin Analytics → Users**, an administrator can set an individual account
  to **Enabled**, **Disabled**, or **Inherit**. Individual choices are stored in
  `features.live_interview_assistance`.

For authorized users, microphone, speaker, and clipboard behavior continues to
come from **Settings → Live interview assistance**. Disabled sources are rejected
before transcription so they do not create OpenAI transcription usage.
