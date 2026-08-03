from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
MODELS = ROOT / "products" / "resume_taylor" / "resume_tailor" / "models.py"
SELECTOR = ROOT / "products" / "resume_taylor" / "resume_tailor" / "bullet_selection.py"
TEMPLATE = ROOT / "products" / "resume_taylor" / "templates" / "application_builder" / "index.html"


class ExcludedBulletCompetitorExplanationContractTests(unittest.TestCase):
    def test_selection_persists_specific_competing_bullets_and_reasons(self) -> None:
        models = MODELS.read_text(encoding="utf-8")
        selector = SELECTOR.read_text(encoding="utf-8")

        self.assertIn("selected_instead_ids", models)
        self.assertIn("selection_comparison_reasons", models)
        self.assertIn("selected_instead_ids=selected_instead_ids", selector)

    def test_finalize_resume_renders_competing_bullet_text_reasons_and_scores(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("Higher-ranked related accomplishments", template)
        self.assertIn("alternative.id", template)
        self.assertIn("alternative.text", template)
        self.assertIn("alternative.reasons", template)
        self.assertIn("alternative.score.total", template)

    def test_status_prefix_is_not_repeated_in_evidence_explanation(self) -> None:
        app = APP.read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("def display_evidence_note", app)
        self.assertIn("bullet.evidence_note_display", template)
        self.assertNotIn("{{ bullet.evidence_note }}", template)


if __name__ == "__main__":
    unittest.main()
