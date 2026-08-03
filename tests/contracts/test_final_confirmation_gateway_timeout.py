"""Contracts preventing any Step 2 confirmation submission from timing out."""

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


class FinalConfirmationGatewayTimeoutContracts(unittest.TestCase):
    @staticmethod
    def _route_section() -> str:
        source = APP_SOURCE.read_text(encoding="utf-8")
        return source.split("def apply_confirmation():", 1)[1].split(
            '@application_builder_bp.post("/confirmation/save-to-library")', 1
        )[0]

    def test_all_confirmation_answers_use_local_deterministic_processing(self) -> None:
        section = self._route_section()
        self.assertIn("apply_final_follow_up_answers_locally(", section)
        self.assertIn("refined, _ = apply_all_until_valid(", section)
        self.assertIn("current.confirmation_complete = True", section)
        self.assertIn("current.confirmation_follow_up_count = 0", section)

    def test_confirmation_route_does_not_make_another_ai_request(self) -> None:
        section = self._route_section()
        self.assertNotIn("ResumeAI(", section)
        self.assertNotIn("refine_proposal(", section)
        self.assertNotIn("_run_post_confirmation_evidence_review(", section)
        self.assertNotIn("audit_proposal(", section)

    def test_loading_copy_no_longer_claims_an_ai_recheck(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Applying final evidence answers…", template)
        self.assertIn(
            "This step does not wait for another AI rewrite or evidence audit.",
            template,
        )
        self.assertNotIn("Rechecking evidence…", template)
        self.assertNotIn("A longer evidence review can take additional time", template)


if __name__ == "__main__":
    unittest.main()
