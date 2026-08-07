"""Contracts for a focused, action-oriented Step 2 Target-Market Review."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_MODULE = ROOT / "products" / "resume_taylor" / "app.py"
BUILDER_TEMPLATE = (
    ROOT / "products" / "resume_taylor" / "templates" / "application_builder" / "index.html"
)
STYLESHEET = ROOT / "products" / "resume_taylor" / "static" / "styles.css"


class TargetMarketReviewProgressiveDisclosureTests(unittest.TestCase):
    def _classification_namespace(self) -> dict[str, object]:
        tree = ast.parse(APP_MODULE.read_text(encoding="utf-8"))
        selected: list[ast.stmt] = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = {
                    target.id
                    for target in node.targets
                    if isinstance(target, ast.Name)
                }
                if "CAREER_TRANSLATION_MATERIAL_REPHRASING_CATEGORIES" in names:
                    selected.append(node)
            elif (
                isinstance(node, ast.FunctionDef)
                and node.name
                in {
                    "_career_translation_finding_needs_review",
                    "_career_translation_review_bucket",
                }
            ):
                selected.append(node)
        namespace: dict[str, object] = {}
        exec(
            compile(ast.Module(body=selected, type_ignores=[]), str(APP_MODULE), "exec"),
            namespace,
        )
        return namespace

    def test_only_questions_and_material_rephrasings_need_attention(self) -> None:
        namespace = self._classification_namespace()
        needs_review = namespace["_career_translation_finding_needs_review"]
        bucket = namespace["_career_translation_review_bucket"]

        self.assertFalse(needs_review("transferable_skill", "confirmed_experience"))
        self.assertFalse(needs_review("regional_terminology", "reasonable_rephrasing"))
        self.assertTrue(needs_review("job_title_translation", "reasonable_rephrasing"))
        self.assertTrue(needs_review("credential_explanation", "reasonable_rephrasing"))
        self.assertTrue(needs_review("missing_evidence", "user_clarification_required"))
        self.assertFalse(needs_review("unsupported_requirement", "unsupported_claim"))
        self.assertFalse(
            needs_review(
                "unsupported_requirement",
                "recommended_learning_or_future_action",
            )
        )

        self.assertEqual(bucket("confirmed_experience"), "evidence_found")
        self.assertEqual(bucket("reasonable_rephrasing"), "evidence_found")
        self.assertEqual(bucket("user_clarification_required"), "confirmation_needed")
        self.assertEqual(bucket("unsupported_claim"), "no_evidence")

    def test_template_groups_findings_by_candidate_action(self) -> None:
        template = BUILDER_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("career_translation_assessment.evidence_found_groups", template)
        self.assertIn("career_translation_assessment.confirmation_needed_groups", template)
        self.assertIn("career_translation_assessment.no_evidence_groups", template)
        self.assertIn("Evidence already found", template)
        self.assertIn("Confirmation needed", template)
        self.assertIn('id="confirmation-needed"', template)
        self.assertIn("No evidence found — no action required", template)
        self.assertIn("No action is required unless you have relevant experience to add", template)
        self.assertIn('class="career-translation-section-details career-translation-no-evidence-details"', template)
        self.assertNotIn("Requirements kept outside the resume\n", template)
        self.assertIn('href="#confirmation">Answer this question</a>', template)

    def test_no_evidence_cards_remove_repeated_generic_explanations(self) -> None:
        template = BUILDER_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("career_translation_no_evidence_card", template)
        self.assertIn("Kept outside resume", template)
        self.assertNotIn(
            "No Verified Resume Evidence is linked to this requirement. Absence from the resume is not proof",
            template,
        )

    def test_labels_explain_actions_without_implying_missing_skill(self) -> None:
        app_text = APP_MODULE.read_text(encoding="utf-8")
        self.assertIn('"missing_evidence": "Experience questions to answer"', app_text)
        self.assertIn(
            '"unsupported_claim": "Keep outside resume for now"',
            app_text,
        )
        self.assertIn(
            '"recommended_learning_or_future_action": "Confirmed development opportunity"',
            app_text,
        )

    def test_action_buckets_have_responsive_styles(self) -> None:
        css = STYLESHEET.read_text(encoding="utf-8")
        self.assertIn(".career-translation-review-summary", css)
        self.assertIn(".career-translation-summary-evidence", css)
        self.assertIn(".career-translation-summary-confirmation", css)
        self.assertIn(".career-translation-summary-no-evidence", css)
        self.assertIn(".career-translation-section-details > summary", css)
        self.assertIn(".career-translation-no-evidence-item", css)


if __name__ == "__main__":
    unittest.main()
