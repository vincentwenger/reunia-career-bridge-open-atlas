"""Contracts preventing Step 3 -> Step 4 gateway timeouts."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.application_builder_source import application_builder_source

ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = ROOT / "products" / "resume_taylor" / "app.py"
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "index.html"
)
STATE_SOURCE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "web_state.py"
)
PROCESSOR_SOURCE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "resume_async_jobs.py"
)


class FinalOptimizationGatewayTimeoutContracts(unittest.TestCase):
    def test_interactive_final_optimization_only_queues_durable_work(self) -> None:
        source = application_builder_source()
        section = source.split("def start_final_stage():", 1)[1].split(
            '@application_builder_bp.post("/resume/save/<version>")', 1
        )[0]
        self.assertIn("AsyncJobType.RESUME_FINAL_OPTIMIZATION", section)
        self.assertIn("_queue_current_resume_job(", section)
        self.assertIn('current.optimization_status = "queued"', section)
        self.assertNotIn("route_started_at = perf_counter()", section)
        self.assertNotIn("apply_suggested_fixes(", section)
        self.assertNotIn("export_resume_pdf(", section)

    def test_optional_ai_pass_is_bounded_inside_the_worker(self) -> None:
        processor = PROCESSOR_SOURCE.read_text(encoding="utf-8")
        self.assertIn("final_optimization_actionable_issue_batches(", processor)
        self.assertIn("CAREER_BRIDGE_RESUME_ASYNC_AI_TIMEOUT_SECONDS", processor)
        self.assertIn("max_attempts=1", processor)
        self.assertIn("request_timeout_seconds=", processor)
        self.assertIn("optimizer.apply_suggested_fixes(", processor)

    def test_worker_fallback_preserves_resume_without_provider_details(self) -> None:
        processor = PROCESSOR_SOURCE.read_text(encoding="utf-8")
        self.assertIn('state.workflow_stage = "final"', processor)
        self.assertIn("state.final_proposal = optimized.model_copy(deep=True)", processor)
        self.assertIn('optimization_status = "skipped"', processor)
        self.assertIn("Optional final resume optimization was skipped", processor)
        self.assertIn("evidence-reviewed resume was preserved safely", processor)
        self.assertNotIn("OpenAI request using", processor)

    def test_workflow_state_persists_a_safe_optimization_outcome(self) -> None:
        source = STATE_SOURCE.read_text(encoding="utf-8")
        self.assertIn('optimization_status: str = "not_started"', source)
        self.assertIn('optimization_notice: str = ""', source)
        self.assertIn('self.optimization_status = "not_started"', source)
        self.assertIn('self.optimization_notice = ""', source)

    def test_final_page_explains_fallback_and_offers_retry(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Your approved resume was preserved safely", template)
        self.assertIn("Retry optional refinement", template)
        self.assertIn("state.optimization_notice", template)
        self.assertNotIn("OpenAI request using", template)

    def test_loading_message_explains_the_safe_fallback(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "Step 4 will open with your approved score-safe resume", template
        )


if __name__ == "__main__":
    unittest.main()
