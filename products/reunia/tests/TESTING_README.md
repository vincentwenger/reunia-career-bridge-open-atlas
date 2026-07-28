# Réunia Testing Guide

All test scripts, reports, and supporting files stay inside the `tests` folder. Individual pytest files are grouped under `tests/unit_tests/` to keep the main folder organized.

## Folder location

Copy the complete `tests` folder into the Réunia project root:

```text
Meeting_assistant/
├── meeting_assistant/
├── templates/
├── static/
└── tests/
    ├── unit_tests/
    │   ├── test_app_factory.py
    │   ├── test_parsers.py
    │   ├── test_security_failure_regressions.py
    │   └── other test_*.py files
    ├── conftest.py
    ├── expected_actual_cases.py
    ├── run_tests.py
    ├── run_tests.bat
    ├── serve_report.py
    ├── test-report.html
    └── test-results.json
```

Do not move `run_tests.py`, `run_tests.bat`, `serve_report.py`, `test-report.html`, or `test-results.json` outside the `tests` folder.

## Folder organization

```text
tests/
├── unit_tests/              # All pytest files named test_*.py
├── conftest.py              # Shared pytest fixtures
├── expected_actual_cases.py # Expected-versus-actual report cases
├── run_tests.py             # Main test runner
├── run_tests.bat            # Windows launcher
├── serve_report.py          # Opens the browser report
├── test-report.html         # User-friendly report page
└── test-results.json        # Latest generated results
```

The runner automatically executes the tests inside `tests/unit_tests/`.

## Recommended Windows method

Double-click:

```text
tests/run_tests.bat
```

The batch file will:

1. Prefer the project's `.venv` or `venv` Python interpreter.
2. Check whether `pytest` is installed in that interpreter.
3. Install `pytest` automatically when it is missing.
4. Run the expected-versus-actual comparisons.
5. Run the complete pytest suite.
6. Save the latest results to `tests/test-results.json`.
7. Start a temporary local report server.
8. Open `tests/test-report.html` in the default browser.
9. Load the newly generated `test-results.json`.

The temporary report server stops automatically after 15 minutes and serves only files from the `tests` folder.

The report opens even when one or more tests fail, allowing failures to be reviewed immediately.

## Run from a command prompt

From the project root:

```bash
python tests/run_tests.py
```

This generates the JSON report but does not automatically open the browser.

To open the browser report afterward:

```bash
python tests/serve_report.py
```

## Run pytest directly

Run the complete suite:

```bash
pytest -q
```

Latest validated result:

```text
178 passed
```

Run only the additional security and failure-regression batch:

```bash
pytest -q tests/unit_tests/test_security_failure_regressions.py
```

Latest validated result:

```text
28 passed
```

## Added security and failure-regression coverage

The file `tests/unit_tests/test_security_failure_regressions.py` adds 28 tests focused on high-risk failure modes.

### Transcript ownership and failures

The tests verify that:

- Authenticated user identity is passed to transcript update and delete operations.
- Cross-user update and delete attempts are rejected.
- Duplicate transcript submissions return a clear validation error.
- DynamoDB transcript-list failures become application database errors.
- Invalid or altered API tokens cannot access transcript endpoints.

### Authentication and sessions

The tests verify that:

- DynamoDB authentication failures are handled safely.
- Incorrect passwords are rejected.
- Successful login clears stale session values.
- Logout clears the complete session.
- Registration still succeeds when analytics recording is unavailable.
- Desktop authentication still succeeds when usage analytics is unavailable.

### Live Q&A

The tests verify that:

- Live Q&A submission requires authentication.
- Feed entries remain isolated between users.
- OpenAI failures are written to the feed and returned to the client.
- Analytics failures do not interrupt a successfully generated answer.
- The initial server-sent events payload contains only the signed-in user's entries.

### Browser recorder recovery

The tests verify that:

- Empty recorder uploads remove partially created job directories.
- Processing failures mark jobs as failed and remove uploaded audio.
- Completed jobs cannot be processed a second time.
- Recorder jobs are hidden from users who do not own them.
- Unexpected upload failures return a structured JSON error.

### CSV export safety

The tests verify that:

- Values beginning with `=`, `+`, `-`, or `@` are escaped to prevent spreadsheet formula execution.
- Unicode, commas, and embedded line breaks remain valid CSV content.
- Document storage remains correctly exported in megabytes.

These regression tests do not require production runtime changes, new DynamoDB tables, IAM permissions, APIs, or environment variables.

## Files created or updated by the runner

The runner keeps its output inside `tests/`:

```text
tests/test-results.json
tests/.pytest_cache/
```

The browser report is stored at:

```text
tests/test-report.html
```

When the report is opened through `run_tests.bat` or `serve_report.py`, it loads `test-results.json` automatically and avoids browser restrictions on local file access.

## Use another JSON report filename

The report file is still saved inside `tests/`, even when a path is entered:

```bash
python tests/run_tests.py --report my-results.json
```

This creates:

```text
tests/my-results.json
```

The automatic browser report uses `tests/test-results.json`. Use the report's **Open test results** button to view a differently named JSON file.

## Run only expected-versus-actual checks

```bash
python tests/run_tests.py --expected-only
```

## Test dependency

`run_tests.bat` installs `pytest` automatically when it is missing.

To install it manually in the Python environment used by the project:

```bash
python -m pip install pytest
```

When the project has a virtual environment, activate it first or run its Python executable directly. For example:

```text
.venv\Scripts\python.exe -m pip install pytest
```

The HTML report distinguishes a missing test dependency from a real application test failure. It displays **Test setup is incomplete** instead of incorrectly suggesting that the application tests failed.

## Exit codes

- `0`: all tests passed
- `1`: one or more tests failed or raised an error

These exit codes can be used by Jenkins, GitHub Actions, or another CI/CD tool.
