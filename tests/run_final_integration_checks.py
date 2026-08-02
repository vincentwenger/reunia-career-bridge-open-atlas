"""Run the three final Integration and Product Quality checks.

Phases:
1. Direct cross-product integration tests.
2. Full Flask-backed runtime suites when project dependencies are available.
3. Six-profile Playwright browser journey with an adversarial mocked AI response.

The runner always executes dependency-light checks. It reports runtime phases as
BLOCKED rather than silently passing when the local environment lacks required
packages. Use ``--require-runtime`` in CI/deployment to make a blocked runtime
phase fail the command.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CheckResult:
    name: str
    status: str
    command: list[str]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    reason: str = ""


def _run(name: str, command: list[str]) -> CheckResult:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return CheckResult(
        name=name,
        status="passed" if completed.returncode == 0 else "failed",
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _missing_runtime_dependencies() -> list[str]:
    required = (
        "flask",
        "dotenv",
        "redis",
        "openai",
        "docx",
        "reportlab",
        "openpyxl",
        "pypdf",
        "xlrd",
    )
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if importlib.util.find_spec("playwright.sync_api") is None:
        missing.append("playwright")
    if not (shutil.which("chromium") or shutil.which("chromium-browser")):
        missing.append("chromium")
    return missing


def _print_result(result: CheckResult) -> None:
    print(f"\n[{result.status.upper()}] {result.name}")
    if result.reason:
        print(result.reason)
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-runtime",
        action="store_true",
        help="Return failure when Flask/browser phases are blocked.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path for a machine-readable result summary.",
    )
    args = parser.parse_args()

    results: list[CheckResult] = []

    dependency_light_commands = (
        (
            "Direct resume-findings and scorecard-to-action integrations",
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "tests.integration.test_product_quality",
            ],
        ),
        (
            "Six international profile regressions",
            [sys.executable, "tests/validators/validate_international_career_profiles.py"],
        ),
        (
            "No-invented-experience regressions",
            [sys.executable, "tests/validators/validate_no_invented_experience.py"],
        ),
        (
            "Application Builder AI cost-control contract",
            [sys.executable, "tests/validators/validate_application_builder_ai_cost_controls.py"],
        ),
        (
            "Workspace loading, empty, and error-state contract",
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "tests.contracts.test_workspace_states",
            ],
        ),
        (
            "Reduced global navigation contract",
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "tests.contracts.test_reduced_navigation",
            ],
        ),
    )
    for name, command in dependency_light_commands:
        result = _run(name, command)
        results.append(result)
        _print_result(result)

    missing = _missing_runtime_dependencies()
    if missing:
        reason = (
            "Required deployment/runtime dependencies are unavailable in this "
            "execution image: " + ", ".join(missing)
        )
        for name, command in (
            (
                "Full Flask runtime integration suites",
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "-v",
                    "tests.integration.test_application_builder",
                    "tests.integration.test_resume_ai_cost_controls",
                    "tests.regression.test_no_invented_experience",
                ],
            ),
            (
                "Six-profile Playwright browser journey",
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "-v",
                    "tests.browser.test_international_profile_journey",
                ],
            ),
        ):
            result = CheckResult(
                name=name,
                status="blocked",
                command=command,
                returncode=None,
                reason=reason,
            )
            results.append(result)
            _print_result(result)
    else:
        runtime_commands = (
            (
                "Full Flask runtime integration suites",
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "-v",
                    "tests.integration.test_application_builder",
                    "tests.integration.test_resume_ai_cost_controls",
                    "tests.regression.test_no_invented_experience",
                ],
            ),
            (
                "Six-profile Playwright browser journey",
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "-v",
                    "tests.browser.test_international_profile_journey",
                ],
            ),
        )
        for name, command in runtime_commands:
            result = _run(name, command)
            results.append(result)
            _print_result(result)

    if args.json_output:
        output_path = args.json_output
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps([asdict(result) for result in results], indent=2),
            encoding="utf-8",
        )
        try:
            display_path = output_path.relative_to(ROOT)
        except ValueError:
            display_path = output_path
        print(f"\nWrote JSON results to {display_path}")

    failed = any(result.status == "failed" for result in results)
    blocked = any(result.status == "blocked" for result in results)
    if failed or (args.require_runtime and blocked):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
