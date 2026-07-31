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
required by default. Use `--allow-demo-storage` only when production was started
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
