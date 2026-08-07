"""Contracts for useful answer-length guidance in Confirm Relevant Experience."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "index.html"
)


class ConfirmationAnswerLengthGuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = BUILDER_TEMPLATE.read_text(encoding="utf-8")

    def test_initial_confirmation_recommends_sixty_to_one_hundred_twenty_words(self) -> None:
        self.assertIn("Recommended answer length: 60–120 words", self.template)
        self.assertIn("Aim for 60–120 words (about 3–6 sentences).", self.template)
        self.assertIn("This is guidance, not a strict limit.", self.template)

    def test_guidance_requests_resume_relevant_evidence(self) -> None:
        self.assertIn("what you did, how you did it", self.template)
        self.assertIn("the tools or techniques used, and the result", self.template)

    def test_textareas_are_connected_to_their_guidance(self) -> None:
        self.assertGreaterEqual(
            self.template.count('aria-describedby="answer-guidance-{{ q.id }}"'),
            2,
        )
        self.assertGreaterEqual(
            self.template.count('id="answer-guidance-{{ q.id }}"'),
            2,
        )

    def test_follow_up_questions_keep_precise_fact_guidance(self) -> None:
        self.assertIn(
            "Add the precise factual detail requested above so the application can verify the statement safely.",
            self.template,
        )


if __name__ == "__main__":
    unittest.main()
