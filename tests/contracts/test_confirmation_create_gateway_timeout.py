"""Contracts keeping Target-Market Review -> Review Tailored Resume gateway-safe."""

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


class ConfirmationCreateGatewayTimeoutContracts(unittest.TestCase):
    @staticmethod
    def _route_section() -> str:
        source = APP_SOURCE.read_text(encoding="utf-8")
        return source.split("def apply_confirmation():", 1)[1].split(
            '@application_builder_bp.post("/confirmation/save-to-library")', 1
        )[0]

    def test_create_tailored_resume_uses_local_deterministic_processing(self) -> None:
        section = self._route_section()
        self.assertIn("build_profile_with_candidate_answers(", section)
        self.assertIn("apply_final_follow_up_answers_locally(", section)
        self.assertIn("ensure_confirmed_answers_visible(", section)
        self.assertIn("apply_all_until_valid(", section)
        self.assertIn("current.draft_proposal = refined.model_copy(deep=True)", section)

    def test_interactive_confirmation_route_has_no_ai_round_trip(self) -> None:
        section = self._route_section()
        self.assertNotIn("ResumeAI(", section)
        self.assertNotIn("refine_proposal(", section)
        self.assertNotIn("_run_post_confirmation_evidence_review(", section)
        self.assertNotIn("audit_proposal(", section)

    def test_confirmation_completes_without_creating_another_question_round(self) -> None:
        section = self._route_section()
        self.assertIn("current.confirmation_complete = True", section)
        self.assertIn("current.confirmation_follow_up_count = 0", section)
        self.assertNotIn("build_targeted_follow_up_questions(", section)

    def test_loading_copy_describes_bounded_local_processing(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "This step does not wait for another AI rewrite or evidence audit.",
            template,
        )
        self.assertIn("Selecting job-aligned bullets", template)
        self.assertNotIn("A longer evidence review can take additional time", template)


if __name__ == "__main__":
    unittest.main()
