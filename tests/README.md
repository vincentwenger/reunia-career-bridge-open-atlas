# Test suite

All test-specific code lives under this directory.

```text
tests/
├── browser/       Playwright end-to-end journeys
├── contracts/     Static UI and workspace-state contracts
├── fixtures/      Reusable test data
├── helpers/       Isolated integration helpers
├── integration/   Cross-product and Flask integration tests
├── regression/    International-profile and grounding regressions
├── unit/          Source-adapter, normalization, ranking, and storage tests
├── validators/    Dependency-light structural validation commands
└── run_final_integration_checks.py
```

## Commands

Run all discoverable tests:

```bash
python -m unittest discover -s tests -t . -v
```

Run the dependency-aware final checks:

```bash
python tests/run_final_integration_checks.py
```

Run individual structural validators:

```bash
python tests/validators/validate_application_builder_architecture.py
python tests/validators/validate_application_builder_ai_cost_controls.py
python tests/validators/validate_international_career_profiles.py
python tests/validators/validate_no_invented_experience.py
```

## Storage migration and backend contracts

The storage migration suite runs the same ApplicationStore behavior contract
against SQLite and DynamoDB, and the same WorkflowStore behavior contract
against memory and DynamoDB:

```bash
python -m unittest tests.contracts.test_storage_backend_migration_contracts -v
```

It also verifies cross-instance/restart durability, optimistic concurrency,
owner isolation, large-object S3 externalization, legacy inline-item migration,
S3 cleanup on application deletion, and the demo-storage Lightsail scale guard.
The Flask cross-instance cases run when Flask and the Builder runtime
dependencies are installed.

## Deployment validation contracts

The test suite includes dependency-free contracts for
`scripts/deployment/validate_lightsail_deployment.py`. Those tests validate the
AWS scale/command logic, Docker CMD parsing, health contract, authenticated
create/retrieve smoke workflow, cleanup behavior, and required documentation
warning without contacting AWS or a live deployment.
