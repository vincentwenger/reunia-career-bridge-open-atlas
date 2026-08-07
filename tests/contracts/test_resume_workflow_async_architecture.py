from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROUTE_ROOT = ROOT / "products/resume_taylor/application_builder_routes/resume_workflow_routes"
TAILORING_ROUTES = ROUTE_ROOT / "tailoring_routes.py"
FINALIZATION_ROUTES = ROUTE_ROOT / "finalization_routes.py"
APPLICATION_ROUTES = ROOT / "products/resume_taylor/application_builder_routes/applications.py"
ASYNC_TYPES = ROOT / "career_bridge/async_jobs.py"
PROCESSOR = ROOT / "products/resume_taylor/resume_tailor/resume_async_jobs.py"
WORKER = ROOT / "job_discovery/background_worker.py"
TEMPLATE = ROOT / "products/resume_taylor/templates/application_builder/_resume_async_job.html"
JAVASCRIPT = ROOT / "products/resume_taylor/static/resume-async-jobs.js"
DOCKERFILE = ROOT / "Dockerfile"


def segment(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


class ResumeWorkflowAsyncArchitectureTests(unittest.TestCase):
    def test_resume_job_types_are_durable_async_jobs(self) -> None:
        source = ASYNC_TYPES.read_text(encoding="utf-8")
        for value in (
            "RESUME_BASELINE_TRANSLATION",
            "RESUME_TAILORING",
            "RESUME_REPORT",
            "RESUME_FINAL_OPTIMIZATION",
            "RESUME_EXPORT",
        ):
            self.assertIn(value, source)

    def test_long_resume_routes_only_enqueue(self) -> None:
        tailoring = TAILORING_ROUTES.read_text(encoding="utf-8")
        finalization = FINALIZATION_ROUTES.read_text(encoding="utf-8")
        blocks = {
            "start": segment(
                tailoring,
                "@_routes.post('/workflow/start')",
                "@_routes.post('/reports/initial')",
            ),
            "reports": tailoring[tailoring.index("@_routes.post('/reports/initial')"):],
            "final": segment(
                finalization,
                "@_routes.post('/workflow/start-final')",
                "@_routes.post('/resume/save/<version>')",
            ),
        }
        forbidden = (
            "ResumeAI(",
            ".analyze_job(",
            ".create_proposal(",
            "apply_suggested_fixes(",
            "_run_post_confirmation_evidence_review(",
            "export_resume_pdf(",
        )
        for label, block in blocks.items():
            self.assertIn("_queue_", block, label)
            for token in forbidden:
                self.assertNotIn(token, block, f"{label} performs foreground work: {token}")

    def test_final_download_does_not_generate_pdf_in_http_request(self) -> None:
        source = APPLICATION_ROUTES.read_text(encoding="utf-8")
        block = segment(
            source,
            '@application_builder_bp.get("/download/final-resume")',
            '@application_builder_bp.get("/download/final-resume-word")',
        )
        self.assertNotIn("export_resume_pdf(", block)
        self.assertIn("not ready yet", block)

    def test_worker_executes_resume_phases_and_final_evidence_review(self) -> None:
        worker = WORKER.read_text(encoding="utf-8")
        processor = PROCESSOR.read_text(encoding="utf-8")
        for value in (
            "RESUME_BASELINE_TRANSLATION",
            "RESUME_TAILORING",
            "RESUME_REPORT",
            "RESUME_FINAL_OPTIMIZATION",
            "RESUME_EXPORT",
        ):
            self.assertIn(value, worker)
        self.assertIn("ResumeWorkflowAsyncProcessor", worker)
        self.assertIn("allow_candidate_questions=False", processor)
        self.assertGreaterEqual(
            processor.count("_run_post_confirmation_evidence_review("), 2
        )
        self.assertIn("_store_optimized_final_export", processor)
        self.assertIn("export_resume_pdf", processor)

    def test_worker_uses_the_same_career_profile_fingerprint_as_web_requests(self) -> None:
        processor = PROCESSOR.read_text(encoding="utf-8")
        jobs = (ROUTE_ROOT / "jobs.py").read_text(encoding="utf-8")
        self.assertIn("_bind_reusable_career_profile_context(job.owner_id)", processor)
        self.assertIn("workflow_input_fingerprint=self.builder.input_fingerprint", processor)
        self.assertIn("workflow_input_fingerprint=input_fingerprint(current, models)", jobs)
        self.assertIn("active_guard == current_guard", jobs)
        self.assertIn("cancellation was requested", jobs)

    def test_pages_poll_explicit_durable_job_status(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        for token in (
            "data-resume-async-job",
            "data-status-url",
            "data-resume-job-progress",
            "data-resume-job-cancel",
            "data-resume-job-retry",
        ):
            self.assertIn(token, template)
        for token in (
            "pollResumeJob",
            "status_url",
            "cancel_url",
            "retry_url",
            "result_url",
        ):
            self.assertIn(token, javascript)

    def test_web_timeout_is_no_longer_ten_minutes(self) -> None:
        source = DOCKERFILE.read_text(encoding="utf-8")
        match = re.search(r'"--timeout",\s*"(\d+)"', source)
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 180)


if __name__ == "__main__":
    unittest.main()
