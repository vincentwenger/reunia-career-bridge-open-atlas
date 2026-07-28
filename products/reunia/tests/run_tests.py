#!/usr/bin/env python3
"""Run all Réunia tests while keeping test assets under tests/.

Run from the project root:
    python tests/run_tests.py

Or from inside the tests folder:
    python run_tests.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
DEFAULT_REPORT = TESTS_DIR / "test-results.json"
UNIT_TESTS_DIR = TESTS_DIR / "unit_tests"
PYTEST_CACHE_DIR = TESTS_DIR / ".pytest_cache"

# Make the application package importable when this script is launched from tests/.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Avoid creating __pycache__ folders throughout the application while running tests.
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


@dataclass
class CaseResult:
    name: str
    expected: Any
    actual: Any
    passed: bool
    error: str | None = None


class SafeJsonEncoder(json.JSONEncoder):
    def default(self, value: Any):
        try:
            return super().default(value)
        except TypeError:
            return repr(value)


def run_expected_actual_cases() -> list[CaseResult]:
    from meeting_assistant import create_app
    from tests.expected_actual_cases import build_cases

    app = create_app("testing")
    results: list[CaseResult] = []

    for case in build_cases(app):
        try:
            actual = case.get_actual(app)
            passed = actual == case.expected
            results.append(
                CaseResult(
                    name=case.name,
                    expected=case.expected,
                    actual=actual,
                    passed=passed,
                )
            )
        except Exception as exc:
            results.append(
                CaseResult(
                    name=case.name,
                    expected=case.expected,
                    actual=None,
                    passed=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    return results


def run_pytest() -> dict[str, Any]:
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        str(UNIT_TESTS_DIR),
        "-q",
        "-o",
        f"cache_dir={PYTEST_CACHE_DIR}",
    ]

    if importlib.util.find_spec("pytest") is None:
        return {
            "passed": False,
            "skipped": True,
            "status": "dependency_missing",
            "reason": (
                "pytest is not installed in the Python environment used to run "
                "the test runner. Run tests/run_tests.bat again to let it install "
                "pytest automatically, or install it manually with: "
                f'"{sys.executable}" -m pip install pytest'
            ),
            "exit_code": None,
            "command": " ".join(str(part) for part in command),
            "stdout": "",
            "stderr": "No module named pytest",
        }

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "passed": completed.returncode == 0,
        "skipped": False,
        "status": "passed" if completed.returncode == 0 else "failed",
        "reason": "",
        "exit_code": completed.returncode,
        "command": " ".join(str(part) for part in command),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _display(value: Any, width: int = 52) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, cls=SafeJsonEncoder)
    return text if len(text) <= width else text[: width - 3] + "..."


def print_expected_actual_results(results: list[CaseResult]) -> None:
    print("\nEXPECTED VS ACTUAL TESTS")
    print("=" * 108)
    print(f"{'Result':<8} {'Test':<40} {'Expected':<27} Actual")
    print("-" * 108)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        actual = result.error if result.error else _display(result.actual, 27)
        print(
            f"{status:<8} "
            f"{result.name[:39]:<40} "
            f"{_display(result.expected, 27):<27} "
            f"{actual}"
        )
    print("=" * 108)


def write_report(path: Path, cases: list[CaseResult], pytest_result: dict[str, Any]) -> None:
    expected_actual_passed = all(result.passed for result in cases)
    overall_passed = expected_actual_passed and pytest_result["passed"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_result": "PASS" if overall_passed else "FAIL",
        "all_tests_passed": overall_passed,
        "expected_actual": {
            "passed": expected_actual_passed,
            "total": len(cases),
            "passed_count": sum(result.passed for result in cases),
            "failed_count": sum(not result.passed for result in cases),
            "tests": [asdict(result) for result in cases],
        },
        "pytest": pytest_result,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, cls=SafeJsonEncoder),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT.name,
        metavar="FILENAME",
        help=(
            "JSON report filename stored inside tests/ "
            f"(default: {DEFAULT_REPORT.name})"
        ),
    )
    parser.add_argument(
        "--expected-only",
        action="store_true",
        help="Run only expected-versus-actual checks and skip pytest.",
    )
    return parser.parse_args()


def resolve_report_path(value: str) -> Path:
    """Keep the generated report inside the tests folder."""
    filename = Path(value).name
    if not filename:
        raise ValueError("The report filename cannot be empty.")
    if not filename.lower().endswith(".json"):
        filename += ".json"
    return TESTS_DIR / filename


def main() -> int:
    args = parse_args()
    report_path = resolve_report_path(args.report)

    cases = run_expected_actual_cases()
    print_expected_actual_results(cases)

    if args.expected_only:
        pytest_result = {
            "passed": True,
            "skipped": True,
            "status": "intentionally_skipped",
            "reason": "pytest was skipped by --expected-only",
            "exit_code": 0,
            "command": "",
            "stdout": "pytest was skipped by --expected-only",
            "stderr": "",
        }
    else:
        print("\nPYTEST SUITE")
        print("=" * 108)
        pytest_result = run_pytest()
        if pytest_result.get("reason"):
            print(pytest_result["reason"])
        if pytest_result["stdout"]:
            print(pytest_result["stdout"])
        if pytest_result["stderr"]:
            print(pytest_result["stderr"], file=sys.stderr)

    all_passed = all(result.passed for result in cases) and pytest_result["passed"]
    print("\nFINAL TEST RESULT")
    print("=" * 108)
    print("ALL TESTS PASSED" if all_passed else "ONE OR MORE TESTS DID NOT PASS")

    write_report(report_path, cases, pytest_result)
    print(f"Detailed report: {report_path}")
    print(f"HTML report: {TESTS_DIR / 'test-report.html'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
