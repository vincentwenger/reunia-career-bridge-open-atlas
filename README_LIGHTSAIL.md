# Réunia Career Bridge — AWS Lightsail container

This deployment package contains the merged product:

- Réunia Career Bridge shell at `/` and `/app`
- Resume-tailoring Application Builder at `/applications/`
- Lightsail health check at `/health`

The Application Builder uses the direct `/applications/` route and no Nginx sidecar.

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

Use the same environment variables as the existing Réunia deployment. The
Application Builder additionally reads `OPENAI_API_KEY` and optionally
`CAREER_BRIDGE_APPLICATIONS_DB`.

The current Application Builder database is SQLite and its active workflow
state is process-local. Keep one container node unless those stores are moved
to external persistent services.

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
