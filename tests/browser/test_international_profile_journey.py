"""Playwright end-to-end validation for all international profile fixtures.

This test launches the real unified Flask application, seeds each profile into a
job application workflow, submits the real Interview Preparation form in
Chromium, and patches only the OpenAI boundary with a deliberately unsafe
structured response. The production post-generation grounding layer must remove
all invented candidate experience before the workspace is saved or rendered.

Run from the repository root after installing ``requirements.txt`` and
Playwright Chromium:

    python -m unittest -v tests.browser.test_international_profile_journey
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "international_profiles"

_REQUIRED_MODULES = (
    "flask",
    "dotenv",
    "redis",
    "openai",
    "docx",
    "reportlab",
    "openpyxl",
    "pypdf",
    "xlrd",
    "playwright.sync_api",
)
_MISSING_MODULES = tuple(
    name for name in _REQUIRED_MODULES if importlib.util.find_spec(name) is None
)
_CHROMIUM_EXECUTABLE = shutil.which("chromium") or shutil.which("chromium-browser")
_RUNTIME_AVAILABLE = not _MISSING_MODULES and bool(_CHROMIUM_EXECUTABLE)
_SKIP_REASON = (
    "Missing runtime dependencies: " + ", ".join(_MISSING_MODULES)
    if _MISSING_MODULES
    else "Chromium executable is unavailable"
)


@unittest.skipUnless(_RUNTIME_AVAILABLE, _SKIP_REASON)
class InternationalProfileBrowserJourneyTests(unittest.TestCase):
    """Exercise the actual browser route for every international profile."""

    def test_six_profiles_reject_adversarial_interview_claims_end_to_end(self) -> None:
        os.environ["APP_ENV"] = "testing"
        os.environ.setdefault("OPENAI_API_KEY", "browser-e2e-test-key")

        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server

        app_entry = importlib.import_module("app")
        builder_module = importlib.import_module("products.resume_taylor.app")
        interview_module = importlib.import_module(
            "products.resume_taylor.resume_tailor.interview_preparation"
        )
        models_module = importlib.import_module(
            "products.resume_taylor.resume_tailor.models"
        )

        app = app_entry.create_application("testing")
        app.config.update(
            TESTING=True,
            CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=True,
            SERVER_NAME=None,
        )
        owner_id = "international-browser-validation-user"
        application_store = app.extensions["career_bridge_application_store"]
        workflow_store = app.extensions["career_bridge_workflow_store"]

        scenarios: list[dict[str, object]] = []
        applications = []
        for path in sorted(FIXTURE_DIR.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            profile = models_module.CandidateProfile.model_validate(raw["profile"])
            background = models_module.NewcomerCareerProfile.model_validate(
                raw["career_background"]
            )
            analysis = models_module.JobAnalysis.model_validate(raw["job_analysis"])
            proposal = models_module.TailoringProposal.model_validate(raw["proposal"])
            company = f"E2E {raw['scenario_id']} Employer"
            application = application_store.create(
                owner_id,
                company=company,
                role=analysis.target_title,
                status="interviewing",
                job_description=raw["job_description"],
                workflow_step="evidence_export",
            )
            state = workflow_store.get(
                f"{owner_id}:application:{application.id}"
            )
            state.source_profile = profile
            state.confirmed_profile = profile
            state.confirmation_complete = True
            state.career_background = background
            state.job_description = raw["job_description"]
            state.target_title = analysis.target_title
            state.analysis = analysis
            state.initial_report_analysis = analysis
            state.initial_evidence_proposal = proposal
            state.final_proposal = proposal
            state.workflow_stage = "evidence_export"

            scenarios.append(raw)
            applications.append(application)

        self.assertEqual(len(applications), 6)

        captured_findings: list[object] = []

        class _AdversarialResumeAI:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def create_interview_preparation(
                self,
                *,
                company: str,
                role: str,
                job_description: str,
                evidence,
                resume_findings,
            ):
                del company, role, job_description
                captured_findings.append(resume_findings)
                safe = evidence.items[0]
                unsafe_claim = (
                    "Led a 99-person NebulaERP transformation at FictionalCorp."
                )
                return interview_module.InterviewPreparationWorkspace(
                    role_summary="The posting describes the target role and its responsibilities.",
                    company_summary="Only information supplied in the posting is used.",
                    expected_responsibilities=[
                        "Perform the central responsibilities stated in the job posting."
                    ],
                    likely_technical_questions=[
                        interview_module.InterviewQuestionPlan(
                            question="How would you approach a central technical responsibility?",
                            why_likely="The responsibility appears in the posting.",
                            answer_focus=unsafe_claim,
                            evidence_ids=[safe.id],
                        )
                    ],
                    likely_behavioral_questions=[
                        interview_module.InterviewQuestionPlan(
                            question="Describe a relevant verified example.",
                            why_likely="The employer may probe the resume.",
                            answer_focus=safe.text,
                            evidence_ids=[safe.id],
                        ),
                        interview_module.InterviewQuestionPlan(
                            question="Describe enterprise transformation leadership.",
                            why_likely="Adversarial generated content.",
                            answer_focus=unsafe_claim,
                            evidence_ids=[safe.id],
                        ),
                    ],
                    resume_challenge_areas=[],
                    candidate_strengths=[
                        interview_module.InterviewPreparationPoint(
                            title="Verified candidate evidence",
                            detail=safe.text,
                            evidence_ids=[safe.id],
                        ),
                        interview_module.InterviewPreparationPoint(
                            title="Invented enterprise leadership",
                            detail=unsafe_claim,
                            evidence_ids=[safe.id],
                        ),
                    ],
                    potential_experience_gaps=[],
                    questions_to_ask=[
                        "How will success be measured in the first six months?"
                    ],
                    personal_introduction=interview_module.PersonalIntroductionOutline(
                        opening=safe.text,
                        current_value=safe.text,
                        relevant_background=safe.text,
                        role_connection=safe.text,
                        closing=safe.text,
                        evidence_ids=[safe.id],
                    ),
                )

        server = make_server("127.0.0.1", 0, app, threaded=True)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        try:
            client = app.test_client()
            with client.session_transaction() as session:
                session["user_id"] = owner_id
                session["email"] = "browser.validation@example.test"
                session["full_name"] = "Browser Validation"
            response = client.get("/applications/")
            self.assertEqual(response.status_code, 200)
            session_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
            session_cookie = client.get_cookie(session_cookie_name)
            self.assertIsNotNone(session_cookie)

            with patch.object(builder_module, "ResumeAI", _AdversarialResumeAI):
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=_CHROMIUM_EXECUTABLE,
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    context = browser.new_context()
                    context.add_cookies(
                        [
                            {
                                "name": session_cookie_name,
                                "value": session_cookie.value,
                                "url": base_url,
                            }
                        ]
                    )
                    page = context.new_page()

                    for scenario, application in zip(scenarios, applications):
                        with self.subTest(scenario=scenario["scenario_id"]):
                            page.goto(
                                f"{base_url}/applications/interview-preparation"
                                f"?application_id={application.id}",
                                wait_until="networkidle",
                            )
                            button = page.locator(
                                "form.interview-generate-form button[type='submit']"
                            )
                            self.assertTrue(button.is_enabled())
                            button.click()
                            page.wait_for_url(
                                f"**/applications/interview-preparation?application_id={application.id}**"
                            )
                            page.locator("#interview-workspace-title").wait_for()

                            rendered = page.locator("body").inner_text()
                            record = application_store.get_interview_preparation(
                                owner_id, application.id
                            )
                            self.assertIsNotNone(record)
                            saved = record.content_json
                            for forbidden in (
                                "99-person",
                                "NebulaERP",
                                "FictionalCorp",
                            ):
                                self.assertNotIn(forbidden, rendered)
                                self.assertNotIn(forbidden, saved)
                            self.assertIn(
                                "No verified evidence supports a specific candidate claim",
                                saved,
                            )
                            self.assertIn(
                                "structured resume findings",
                                rendered.casefold(),
                            )
                            saved_findings = json.loads(
                                record.resume_findings_snapshot_json
                            )
                            self.assertEqual(
                                saved_findings["target_role"], application.role
                            )
                            self.assertEqual(
                                saved_findings["target_company"], application.company
                            )

                    context.close()
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        self.assertEqual(len(captured_findings), 6)
        self.assertTrue(
            all(
                getattr(snapshot, "application_context_fingerprint", "")
                for snapshot in captured_findings
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
