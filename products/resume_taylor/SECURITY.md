# Security

## API keys

- Never place an API key in Python code, batch files, JSON, screenshots, ZIP files, or source control.
- Keep the key in `.env` or the operating system's `OPENAI_API_KEY` environment variable.
- `.env` is excluded by `.gitignore`.
- Revoke any key previously included in an archive or repository.

## Flask secret

Set `FLASK_SECRET_KEY` to a long random value. It signs the browser session cookie, which contains only an opaque workflow ID and CSRF token.

For an HTTPS deployment, set:

```text
FLASK_COOKIE_SECURE=true
```

Do not enable Flask debug mode on a public server.

## Server-side workflow state

Active resume content, Candidate Profiles, AI results, reports, audits, and generated Word bytes are held in process-local memory by `InMemoryWorkflowStore`. They are not placed in the browser cookie.

The store expires inactive workflows after eight hours. Restarting the process clears them. A production deployment with multiple workers should replace this store with Redis or another shared, access-controlled server-side store.

Application-tracker records are intentionally persistent. They are stored in `instance/applications.sqlite3` together with any exact Word resume snapshot saved for an application. Access is scoped to the opaque workflow ID in the signed browser cookie. Protect the `instance` directory, avoid sharing its database, and use authenticated user IDs instead of browser workflow IDs for a public or multi-user deployment.

## Form protection

All modifying forms use POST and include a session-bound CSRF token. File uploads are size-limited to 4 MB. Uploaded Candidate Profiles are parsed as strict Pydantic models and are not saved automatically.

## Candidate data

The application sends the professional profile, job description, candidate confirmation responses, proposal, and audit request to the configured OpenAI model. Contact details are excluded from model prompts. Process candidate information only when authorized.

## Generated files

Proposal JSON, reports, updated profiles, and active Word resumes are generated in memory and exposed through browser-session download routes. A Word resume is persisted only when the user explicitly selects **Save as application**; that immutable snapshot is stored in the local Applications database and is never uploaded elsewhere.
