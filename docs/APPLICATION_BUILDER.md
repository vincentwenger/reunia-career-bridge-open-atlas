# Application Builder Integration

## Product role

Resume Taylor is now one major module inside Réunia Career Bridge. It no longer presents itself as the whole product. The Career Bridge navigation opens the module at `/application-builder/`, where the first page is a multi-application dashboard.

Each dashboard record is an application-oriented read model aligned with the shared `JobApplication` aggregate. It shows:

- company;
- job title;
- application status;
- current resume version;
- interview readiness;
- next action;
- upcoming application deadline, interview, or follow-up.

Each application owns a separate in-memory resume workflow key and a persistent dashboard record. Existing Resume Taylor resume-generation algorithms, reports, editors, and export behavior remain the delivery adapter behind the module.

## Six-step workflow

1. **Career and Job Setup** — select the source resume and enter the target role and job description.
2. **Confirm Relevant Experience** — confirm only evidence that may truthfully support this application.
3. **Review Tailored Resume** — review the tailored version and its evidence-backed changes.
4. **Improve Resume Quality** — apply score-protected content and searchability improvements.
5. **Finalize Resume** — choose final structure, format, career stage, and visual presentation.
6. **Evidence Review and Export** — review the final evidence report and export PDF or Word.

The legacy engine still uses four internal lifecycle snapshots (`initial`, `confirmation`, `draft`, and `final`). The delivery adapter maps those implementation stages to six user-facing steps rather than rewriting the mature generation pipeline.

## Runtime boundary

Réunia requires OpenAI SDK 1.x while Resume Taylor requires OpenAI SDK 2.x. They therefore remain separate Python services and virtual environments. A reverse proxy composes them into one URL space:

- Réunia shell and authentication: `/`
- Application Builder: `/application-builder/`

Both services must use the same `FLASK_SECRET_KEY`, session cookie name, and cookie domain. The Application Builder WSGI entry point requires an authenticated Réunia session and uses the Réunia `user_id` as the application owner.

Example deployment wiring is in `deploy/career_bridge.nginx.conf.example`. Start the builder service from the repository root with its own environment:

```bash
FLASK_SECRET_KEY='<same value used by Reunia>' \
  gunicorn --bind 127.0.0.1:5001 application_builder_wsgi:application
```

## Compatibility boundary

- No Réunia route or database table is merged with Resume Taylor storage.
- The existing Resume Taylor SQLite table is migrated in place with dashboard fields.
- Legacy statuses (`planned`, `interview`, `offer`) are normalized to shared Career Bridge statuses.
- Existing final resume snapshots remain downloadable.
- The shared domain remains dependency-free and does not import Flask, SQLite, OpenAI, or either legacy product.
