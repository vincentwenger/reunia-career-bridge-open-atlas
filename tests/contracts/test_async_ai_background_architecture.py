from __future__ import annotations

import unittest
from pathlib import Path

from tests.application_builder_source import application_builder_source


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
INTERVIEW_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "interview_preparation.html"
)
INTERVIEW_JS = ROOT / "products" / "resume_taylor" / "static" / "interview-preparation.js"
WORKER = ROOT / "job_discovery" / "background_worker.py"
ASYNC_JOBS = ROOT / "career_bridge" / "async_jobs.py"
HEALTH = ROOT / "products" / "reunia" / "meeting_assistant" / "__init__.py"
VALIDATOR = ROOT / "scripts" / "deployment" / "validate_lightsail_deployment.py"


class AsyncAIBackgroundArchitectureContractTests(unittest.TestCase):
    def test_interview_generation_route_only_enqueues(self) -> None:
        source = application_builder_source()
        start = source.index('@application_builder_bp.post("/interview-preparation/generate")')
        end = source.index('@application_builder_bp.get("/interview-preparation/jobs/<job_id>")', start)
        route = source[start:end]
        self.assertIn("AsyncJob.queued", route)
        self.assertIn("async_job_store.create(job)", route)
        self.assertIn("application_object_key", route)
        self.assertIn("document_store.put", route)
        self.assertIn('"snapshot_key": snapshot_key', route)
        self.assertIn("return jsonify(_interview_async_job_response(stored)), 202", route)
        self.assertNotIn("ResumeAI(", route)
        self.assertNotIn("create_interview_preparation(", route)

    def test_interview_page_reconnects_to_background_job(self) -> None:
        template = INTERVIEW_TEMPLATE.read_text(encoding="utf-8")
        javascript = INTERVIEW_JS.read_text(encoding="utf-8")
        self.assertIn("interview-preparation.js", template)
        self.assertIn("data-active-job-status-url", template)
        self.assertIn("data-interview-preparation-job-progress", template)
        self.assertIn("pollUntilTerminal", javascript)
        self.assertIn("status_url", javascript)
        self.assertIn("cancel_url", javascript)
        self.assertIn("retry_url", javascript)
        self.assertIn("worker continues independently", javascript)

    def test_worker_registers_resume_package_path_before_import(self) -> None:
        worker = WORKER.read_text(encoding="utf-8")
        path_setup = worker.index('RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"')
        resume_import = worker.index("from resume_tailor.resume_async_jobs import")
        self.assertLess(path_setup, resume_import)
        self.assertIn("for import_root in (ROOT, RESUME_TAYLOR_ROOT)", worker)
        self.assertIn("for path in (reunia_root, RESUME_TAYLOR_ROOT, ROOT)", worker)
        self.assertIn("_configure_application_import_paths()", worker)

    def test_external_worker_handles_all_queued_ai_types(self) -> None:
        worker = WORKER.read_text(encoding="utf-8")
        self.assertIn("AsyncJobType.JOB_DISCOVERY_ASSESSMENT", worker)
        self.assertIn("AsyncJobType.INTERVIEW_PREPARATION", worker)
        self.assertIn("_run_discovery_assessment", worker)
        self.assertIn("_run_interview_preparation", worker)
        self.assertIn("python -m job_discovery.background_worker --poll", worker)
        self.assertIn("lambda_handler", worker)

    def test_worker_heartbeat_is_durable_and_required_by_deployment_validation(self) -> None:
        worker = WORKER.read_text(encoding="utf-8")
        async_jobs = ASYNC_JOBS.read_text(encoding="utf-8")
        health = HEALTH.read_text(encoding="utf-8")
        validator = VALIDATOR.read_text(encoding="utf-8")
        self.assertIn("AsyncWorkerHeartbeatLoop", worker)
        self.assertIn("CAREER_BRIDGE_ASYNC_WORKER_HEARTBEAT_INTERVAL_SECONDS", worker)
        self.assertIn("ASYNC#WORKER#HEARTBEAT", async_jobs)
        self.assertIn("record_worker_heartbeat", async_jobs)
        self.assertIn('"async_worker": async_worker_health_status()', health)
        self.assertIn("Async worker heartbeat is not healthy", validator)


if __name__ == "__main__":
    unittest.main()
