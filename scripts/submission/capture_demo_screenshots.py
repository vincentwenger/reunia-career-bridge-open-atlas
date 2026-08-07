#!/usr/bin/env python3
"""Capture real Career Bridge screenshots from a prepared deployed account."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import quote


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture submission screenshots with Playwright.")
    parser.add_argument("--base-url", default=os.getenv("CAREER_BRIDGE_BASE_URL", "https://career.reunia.app"))
    parser.add_argument("--email", default=os.getenv("DEPLOYMENT_VALIDATION_EMAIL", ""))
    parser.add_argument("--password", default=os.getenv("DEPLOYMENT_VALIDATION_PASSWORD", ""))
    parser.add_argument("--application-id", default=os.getenv("OPEN_ATLAS_DEMO_APPLICATION_ID", ""))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/submission/screenshots"))
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    if not args.email.strip() or not args.password:
        parser.error("--email and --password (or deployment validation environment variables) are required")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Playwright is required. Install requirements-dev.txt and Chromium.") from exc

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")
    application_query = (
        f"?application_id={quote(args.application_id, safe='')}" if args.application_id else ""
    )

    pages = (
        ("03-career-profile.png", "/career-profile"),
        ("04-career-translation.png", "/applications/career-translation"),
        ("05-evidence-library.png", "/career-evidence-library"),
        ("06-job-application.png", "/applications/?tab=applications" + ("&" + application_query[1:] if application_query else "")),
        ("07-resume-workflow.png", "/applications/?tab=tailoring" + ("&" + application_query[1:] if application_query else "")),
        ("08-interview-preparation.png", "/applications/interview-preparation" + application_query),
        ("09-mock-interview.png", "/mock-interview" + application_query),
        ("10-interview-review.png", "/interview-review" + application_query),
        ("11-action-plan.png", "/career-action-plan" + application_query),
        ("12-progress.png", "/progress" + application_query),
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page = context.new_page()

        page.goto(base + "/", wait_until="networkidle")
        page.screenshot(path=str(output_dir / "01-homepage.png"), full_page=True)

        page.goto(base + "/login.html", wait_until="networkidle")
        page.screenshot(path=str(output_dir / "02-login.png"), full_page=True)
        page.locator("#login-email").fill(args.email)
        page.locator("#login-password").fill(args.password)
        page.locator("#login-form button[type='submit']").click()
        page.wait_for_load_state("networkidle")
        if page.url.endswith("/login.html") or page.locator("#auth-error").count():
            raise SystemExit("Sign-in failed; verify the synthetic demo account credentials.")

        for filename, path in pages:
            page.goto(base + path, wait_until="networkidle")
            if page.url.endswith("/login.html"):
                raise SystemExit(f"Session was not authenticated while capturing {path}.")
            page.screenshot(path=str(output_dir / filename), full_page=True)
            print(f"Captured {filename}")

        context.close()
        browser.close()

    print(f"Screenshots written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
