# Final integration and product-quality checks

The final-check runner combines dependency-light contracts with optional Flask and browser journeys.

## Standard command

```bash
python tests/run_final_integration_checks.py \
  --json-output reports/validation/final-integration-check-results.json
```

The runner reports each unavailable optional dependency as **skipped**. This is useful for local source validation but is not sufficient for release approval.

## Release command

Use the strict runtime mode in CI or before deployment:

```bash
python tests/run_final_integration_checks.py \
  --require-runtime \
  --json-output reports/validation/final-integration-check-results.json
```

Strict mode must have the complete Flask application dependencies and browser-test runtime available. Missing required dependencies fail the command.

## Coverage

The orchestration includes:

- Unit tests for adapters, normalization, ranking, storage, translation, and readiness calculations.
- Contract tests for navigation, templates, terminology, security boundaries, and workspace states.
- Regression tests for international profiles and unsupported-claim prevention.
- Flask integration tests for cross-product workflows and persistence.
- Browser journeys when Playwright and the configured browser runtime are available.
- Structural validators for Application Builder architecture, cost controls, durable storage, and no-invented-experience safeguards.

## Generated output

`reports/validation/final-integration-check-results.json` and the companion text output are generated artifacts. Review the command exit code and the status of every required group; do not rely on an older locally generated report.
