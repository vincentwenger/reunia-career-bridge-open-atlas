# Operational scripts

Only operational and deployment utilities belong here. Automated tests, test
validators, fixtures, and test orchestration live under `tests/`.

## Lightsail deployment validation

After a deployment becomes active, run:

```bat
scripts\deployment\validate_lightsail_deployment.bat
```

Set `CAREER_BRIDGE_BASE_URL`, `DEPLOYMENT_VALIDATION_EMAIL`, and
`DEPLOYMENT_VALIDATION_PASSWORD` first. The Python implementation uses only the
standard library and the installed AWS CLI. Persistent DynamoDB/S3 storage is
required by default. Use the legacy-named `--allow-demo-storage` flag only when a controlled validation deployment was started
with `CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION=true`. See
`docs/deployment/lightsail.md` for the complete contract and options.


## External job-discovery scans

Do not run a discovery scheduler inside Flask or Gunicorn. Invoke the packaged
module from EventBridge/Lambda, a scheduled container, or controlled cron:

```bash
JOB_DISCOVERY_OWNER_IDS=user-1,user-2 python -m job_discovery.scheduling
```

The process uses the dedicated discovery store and exits after one bounded scan.
See `docs/job_discovery.md` for required DynamoDB variables and optional profile
provider input.

## Career Bridge AWS storage preflight

Run `scripts/deployment/provision_career_bridge_storage.bat` before deploying.
It reads the current Lightsail container environment, verifies the application,
workflow, and discovery DynamoDB schemas, enables workflow TTL, and creates or
hardens the private versioned document bucket. The upload script runs this
preflight automatically and stops before the Docker build when storage is not
ready.

## Career Bridge test-user cleanup

Preview all records associated with one exact user ID or email:

```bat
scripts\delete_dynamodb_user_records.bat test-user@example.com
```

Delete only after reviewing the preview:

```bat
scripts\delete_dynamodb_user_records.bat test-user@example.com --delete
```

The wrapper invokes `delete_dynamodb_user_records.py`. The Python utility reads
`.env`, uses process environment overrides, supports `--region`, `--profile`,
`--table`, and `--discover-tables`, and skips missing tables. It derives the
hashed Workflow IDs for Career Foundation, scratch, and application workflows,
which ensures Baseline Resume state is removed even though those DynamoDB items
do not store `user_id` or `owner_id`. Destructive mode requires typing
`DELETE <user_id>` unless `--yes` is supplied. When
`CAREER_BRIDGE_DOCUMENTS_BUCKET` is configured (or `--bucket` is supplied), it
also deletes the user's Career Bridge S3 objects. Pass `--keep-s3` to preserve
those objects intentionally.

## CSS token policy

```bash
python scripts/check_css_token_policy.py
```

The checker enforces the canonical `--cb-*` namespace, prevents raw hex colors outside `design-tokens.css`, and blocks increases in reviewed `!important` declarations or exact legacy palette tokens. Stylelint provides the editor and CI-facing version of the same rules through `npm run lint:css`.


## Repository cleanup

Before creating a submission archive, remove generated Python and tool caches:

```bash
python scripts/clean_repository_artifacts.py
```

For Lightsail deployment, use the single canonical entry point:

```bat
scripts\deployment\upload_to_lightsail.bat
```

Use `scripts\deployment\provision_career_bridge_storage.bat` only when running the storage preflight separately.

## Submission utilities

- `submission/check_submission_readiness.py` validates repository artifacts and can run the full local quality suite. `--strict` additionally requires the final public repository URL, demo video URL, real screenshots, and healthy live deployment.
- `submission/capture_demo_screenshots.py` signs into a prepared synthetic account with Playwright and captures real browser screenshots for the submission package.
