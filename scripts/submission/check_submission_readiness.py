#!/usr/bin/env python3
"""Validate repository and optional live-submission readiness.

The default mode is dependency-light and checks only artifacts that belong in
this repository. ``--full`` runs the local test and static-quality commands.
``--strict`` additionally requires repository, video, screenshots, and live
site configuration supplied through arguments or environment variables.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _required_paths() -> tuple[Path, ...]:
    return (
        ROOT / "README.md",
        ROOT / "LICENSE",
        ROOT / ".github" / "workflows" / "asset-budget.yml",
        ROOT / ".github" / "workflows" / "runtime-tests.yml",
        ROOT / "docs" / "submission" / "PREEXISTING_COMPONENTS.md",
        ROOT / "docs" / "submission" / "HACKATHON_CHANGES.md",
        ROOT / "docs" / "submission" / "DEMO_PLAN.md",
        ROOT / "docs" / "submission" / "DEVPOST_SUBMISSION_COPY.md",
        ROOT / "docs" / "submission" / "PRIVACY_AND_DEMO_DATA.md",
        ROOT / "docs" / "submission" / "SUBMISSION_CHECKLIST.md",
        ROOT / "docs" / "submission" / "project-history" / "README.md",
        ROOT / "docs" / "submission" / "project-history" / "PRE_HACKATHON_RESUME_TAILOR.md",
        ROOT / "docs" / "submission" / "project-history" / "REUNIA_SUBMISSION_PERIOD.md",
        ROOT / "docs" / "submission" / "project-history" / "CAREER_BRIDGE_TIMELINE.md",
        ROOT / "docs" / "submission" / "project-history" / "GIT_HISTORY_GUIDE.md",
        ROOT / "docs" / "submission" / "assets" / "architecture.svg",
        ROOT / "docs" / "submission" / "pitch-deck.pdf",
    )


def _valid_public_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def _run(command: list[str], *, name: str) -> Check:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    if completed.returncode == 0:
        return Check(name, "passed", output[-1000:] or "Command passed.")
    return Check(name, "failed", output[-2000:] or f"Exit code {completed.returncode}.")


def _repository_checks() -> list[Check]:
    checks: list[Check] = []
    missing = [str(path.relative_to(ROOT)) for path in _required_paths() if not path.is_file()]
    checks.append(
        Check(
            "Required submission artifacts",
            "passed" if not missing else "failed",
            "All required repository artifacts are present." if not missing else "Missing: " + ", ".join(missing),
        )
    )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    forbidden_placeholders = (
        "<your-public-repository-url>",
        "<repository-directory>",
        "legacy/reunia",
        "legacy/resume-tailor",
    )
    present = [item for item in forbidden_placeholders if item in readme]
    checks.append(
        Check(
            "README public-review accuracy",
            "passed" if not present else "failed",
            "No stale repository placeholders or missing predecessor paths are claimed."
            if not present
            else "Remove or replace: " + ", ".join(present),
        )
    )

    provenance_documents = (
        ROOT / "README.md",
        ROOT / "docs" / "submission" / "PREEXISTING_COMPONENTS.md",
        ROOT / "docs" / "submission" / "HACKATHON_CHANGES.md",
        ROOT / "docs" / "submission" / "DEVPOST_SUBMISSION_COPY.md",
    )
    provenance_text = "\n".join(path.read_text(encoding="utf-8") for path in provenance_documents if path.is_file())
    false_provenance_phrases = (
        "extends two pre-existing personal projects",
        "two pre-existing personal projects",
        "sanitized predecessor branches",
    )
    false_claims = [phrase for phrase in false_provenance_phrases if phrase in provenance_text]
    required_provenance = (
        "June 22, 2026",
        "July 28, 2026",
        "Resume Tailor",
    )
    missing_provenance = [phrase for phrase in required_provenance if phrase not in provenance_text]
    provenance_ok = not false_claims and not missing_provenance
    detail_parts: list[str] = []
    if false_claims:
        detail_parts.append("False/stale provenance wording: " + "; ".join(false_claims))
    if missing_provenance:
        detail_parts.append("Missing provenance markers: " + "; ".join(missing_provenance))
    checks.append(
        Check(
            "Submission provenance consistency",
            "passed" if provenance_ok else "failed",
            "Resume Tailor, Réunia, and Career Bridge timing is consistently disclosed."
            if provenance_ok
            else " ".join(detail_parts),
        )
    )

    workflows = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / ".github" / "workflows" / "asset-budget.yml",
            ROOT / ".github" / "workflows" / "runtime-tests.yml",
        )
        if path.is_file()
    )
    expected_workflow_commands = (
        "python scripts/build_static_assets.py --check",
        "python scripts/check_asset_budgets.py",
        "npm run lint:css",
        "python -m pip install -r requirements-dev.txt",
        "python -m playwright install --with-deps chromium",
        "python tests/run_final_integration_checks.py --require-runtime",
    )
    missing_commands = [command for command in expected_workflow_commands if command not in workflows]
    checks.append(
        Check(
            "CI release gates",
            "passed" if not missing_commands else "failed",
            "Static, runtime, and browser release gates are configured."
            if not missing_commands
            else "Missing workflow commands: " + "; ".join(missing_commands),
        )
    )

    return checks


def _media_checks(*, require_screenshots: bool, video_url: str) -> list[Check]:
    checks: list[Check] = []
    screenshot_dir = ROOT / "docs" / "submission" / "screenshots"
    screenshots = sorted(
        path for path in screenshot_dir.glob("*.png") if path.is_file() and path.stat().st_size > 10_000
    )
    screenshot_status = "passed" if len(screenshots) >= 3 else ("failed" if require_screenshots else "warning")
    checks.append(
        Check(
            "Real browser screenshots",
            screenshot_status,
            f"Found {len(screenshots)} screenshot(s); at least 3 are required for strict submission readiness.",
        )
    )

    if video_url:
        valid = _valid_public_url(video_url)
        checks.append(
            Check(
                "Demo video URL",
                "passed" if valid else "failed",
                "A public HTTPS demo-video URL is configured."
                if valid
                else "The demo-video URL must be an absolute HTTPS URL.",
            )
        )
    else:
        checks.append(
            Check(
                "Demo video URL",
                "failed" if require_screenshots else "warning",
                "Set OPEN_ATLAS_DEMO_VIDEO_URL after recording the three-minute demo.",
            )
        )
    return checks


def _live_checks(base_url: str, *, timeout: float) -> list[Check]:
    checks: list[Check] = []
    if not _valid_public_url(base_url):
        return [Check("Live application", "failed", "The live base URL must be an absolute HTTPS URL.")]

    base = base_url.rstrip("/")
    try:
        with urlopen(Request(base + "/", headers={"User-Agent": "CareerBridgeSubmissionValidator/1.0"}), timeout=timeout) as response:
            body = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            final_url = response.geturl()
            status = response.getcode()
        problems: list[str] = []
        if status != 200:
            problems.append(f"homepage returned HTTP {status}")
        if not final_url.startswith("https://"):
            problems.append("homepage did not remain on HTTPS")
        if "Réunia Career Bridge" not in body and "Reunia Career Bridge" not in body:
            problems.append("current Career Bridge branding was not found")
        for retired in ("AI Meeting Coach", "record your meetings", "meeting assistant platform"):
            if retired.casefold() in body.casefold():
                problems.append(f"retired public branding found: {retired}")
        checks.append(
            Check(
                "Live homepage",
                "passed" if not problems else "failed",
                "HTTPS homepage returned current Career Bridge branding."
                if not problems
                else "; ".join(problems),
            )
        )
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        checks.append(Check("Live homepage", "failed", f"Could not load homepage: {exc}"))

    try:
        with urlopen(Request(base + "/health", headers={"Accept": "application/json"}), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        worker = payload.get("async_worker") if isinstance(payload, dict) else None
        healthy = (
            isinstance(payload, dict)
            and payload.get("status") == "ok"
            and isinstance(worker, dict)
            and worker.get("status") == "healthy"
        )
        checks.append(
            Check(
                "Live health and worker",
                "passed" if healthy else "failed",
                "Health endpoint and external worker heartbeat are healthy."
                if healthy
                else "Expected status=ok and async_worker.status=healthy.",
            )
        )
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        checks.append(Check("Live health and worker", "failed", f"Could not validate /health: {exc}"))
    return checks


def _print(checks: Iterable[Check]) -> None:
    for check in checks:
        marker = {"passed": "PASS", "warning": "WARN", "failed": "FAIL"}[check.status]
        print(f"{marker:4}  {check.name}: {check.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Open Atlas submission readiness.")
    parser.add_argument("--full", action="store_true", help="Run local test and static-quality commands.")
    parser.add_argument("--strict", action="store_true", help="Require repository URL, video URL, screenshots, and live checks.")
    parser.add_argument("--live", action="store_true", help="Check the deployed homepage and /health endpoint.")
    parser.add_argument("--base-url", default=os.getenv("CAREER_BRIDGE_BASE_URL", "https://career.reunia.app"))
    parser.add_argument("--repository-url", default=os.getenv("OPEN_ATLAS_REPOSITORY_URL", ""))
    parser.add_argument("--video-url", default=os.getenv("OPEN_ATLAS_DEMO_VIDEO_URL", ""))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)

    checks = _repository_checks()

    if args.repository_url:
        checks.append(
            Check(
                "Public repository URL",
                "passed" if _valid_public_url(args.repository_url) else "failed",
                "A public HTTPS repository URL is configured."
                if _valid_public_url(args.repository_url)
                else "The repository URL must be an absolute HTTPS URL.",
            )
        )
    else:
        checks.append(
            Check(
                "Public repository URL",
                "failed" if args.strict else "warning",
                "Set OPEN_ATLAS_REPOSITORY_URL after publishing the final repository.",
            )
        )

    checks.extend(_media_checks(require_screenshots=args.strict, video_url=args.video_url))

    if args.live or args.strict:
        checks.extend(_live_checks(args.base_url, timeout=args.timeout))

    if args.full:
        commands = (
            ("Python compilation", [sys.executable, "-m", "compileall", "-q", "app.py", "career_bridge", "job_discovery", "products", "scripts", "tests"]),
            ("Full Python test suite", [sys.executable, "-m", "pytest", "-q"]),
            ("Static asset generation check", [sys.executable, "scripts/build_static_assets.py", "--check"]),
            ("Static asset budgets", [sys.executable, "scripts/check_asset_budgets.py"]),
            ("CSS token policy", [sys.executable, "scripts/check_css_token_policy.py"]),
            ("Application Builder architecture", [sys.executable, "scripts/check_application_builder_route_architecture.py"]),
            ("Asynchronous AI architecture", [sys.executable, "scripts/check_async_ai_architecture.py"]),
        )
        checks.extend(_run(command, name=name) for name, command in commands)

    _print(checks)

    if args.json_output:
        output = args.json_output if args.json_output.is_absolute() else ROOT / args.json_output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps([asdict(check) for check in checks], indent=2), encoding="utf-8")
        print(f"Wrote {output}")

    return 1 if any(check.status == "failed" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
