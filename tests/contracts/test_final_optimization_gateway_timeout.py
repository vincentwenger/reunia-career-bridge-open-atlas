"""Contracts preventing Step 3 -> Step 4 gateway timeouts."""

from __future__ import annotations

import unittest
from pathlib import Path

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


class FinalOptimizationGatewayTimeoutContracts(unittest.TestCase):
    def test_interactive_final_optimization_has_a_gateway_safe_budget(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "CAREER_BRIDGE_FINAL_OPTIMIZATION_REQUEST_BUDGET_SECONDS", source
        )
        self.assertIn(
            "CAREER_BRIDGE_FINAL_OPTIMIZATION_EXPORT_RESERVE_SECONDS", source
        )
        self.assertIn(
            "CAREER_BRIDGE_FINAL_OPTIMIZATION_AI_TIMEOUT_SECONDS", source
        )
        self.assertIn(
            "def _final_optimization_ai_timeout_seconds(started_at: float)", source
        )
        self.assertIn("route_started_at = perf_counter()", source)

    def test_optional_ai_pass_uses_one_small_bounded_attempt(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")
        section = source.split("def start_final_stage():", 1)[1].split(
            '@application_builder_bp.post("/resume/save/<version>")', 1
        )[0]
        self.assertIn(
            "final_optimization_actionable_issue_batches(", section
        )
        self.assertIn(
            "report_issues = report_issue_batches[0] if report_issue_batches else []",
            section,
        )
        self.assertIn(
            "optimization_timeout = _final_optimization_ai_timeout_seconds(", section
        )
        self.assertIn("max_attempts=1", section)
        self.assertIn("request_timeout_seconds=optimization_timeout", section)

    def test_timeout_falls_back_without_exposing_provider_details(self) -> None:
        source = APP_SOURCE.read_text(encoding="utf-8")
        section = source.split("def start_final_stage():", 1)[1].split(
            '@application_builder_bp.post("/resume/save/<version>")', 1
        )[0]
        self.assertIn('current.workflow_stage = "final"', section)
        self.assertIn("current.final_proposal = optimized.model_copy(deep=True)", section)
        self.assertIn("build_exact_report=False", section)
        self.assertIn('optimization_status = "timed_out"', section)
        self.assertIn("current.optimization_notice = optimization_notice", section)
        self.assertIn("Optional final resume optimization was skipped", section)
        self.assertNotIn(
            '"The optional quality pass could not be completed, so the score-safe Job-Aligned Resume was preserved: "',
            section,
        )

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
