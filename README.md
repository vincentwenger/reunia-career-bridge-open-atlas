# Réunia Career Bridge

Réunia Career Bridge combines the Réunia career workspace with the Application Builder in one Flask application.

## Repository layout

```text
app.py                     Production WSGI entry point
career_bridge/             Shared domain and application abstractions
products/                  Product implementations and web assets
tests/                     All automated tests, fixtures, validators, and test runners
docs/                      Deployment and validation documentation
reports/validation/        Generated validation output
scripts/deployment/        Deployment utilities
```

## Run the tests

Run every discoverable unit, integration, regression, contract, and browser test:

```bash
python -m unittest discover -s tests -t . -v
```

Run the final integration-quality orchestration:

```bash
python tests/run_final_integration_checks.py \
  --json-output reports/validation/final-integration-check-results.json
```

Use `--require-runtime` in CI or deployment validation when missing Flask/browser dependencies must fail the command.

More details are in [`tests/README.md`](tests/README.md) and [`docs/validation/`](docs/validation/).
