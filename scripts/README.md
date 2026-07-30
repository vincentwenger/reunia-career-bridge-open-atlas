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
