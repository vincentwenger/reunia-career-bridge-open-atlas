"""Contracts for consolidated Career Translation and resume terminology."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TERMINOLOGY = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "terminology.py"
)
CAREER_TRANSLATION_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "career_translation.html"
)
WORKFLOW_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "index.html"
)
USER_GUIDE = ROOT / "products" / "reunia" / "templates" / "user-guide.html"
TRACKER = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "application_tracker.py"
)


class ResumeTerminologyContractTests(unittest.TestCase):
    def test_canonical_terms_are_defined_once_for_python_surfaces(self) -> None:
        text = TERMINOLOGY.read_text(encoding="utf-8")
        for assignment in (
            'IMPORTED_RESUME_LABEL = "Imported Resume"',
            'VERIFIED_RESUME_EVIDENCE_LABEL = "Verified Resume Evidence"',
            'CAREER_BASELINE_RESUME_LABEL = "Baseline Resume"',
            'APPLICATION_BASELINE_LABEL = "Application Baseline"',
            'TARGET_MARKET_REVIEW_LABEL = "Target-Market Review"',
        ):
            self.assertIn(assignment, text)

    def test_career_translation_uses_reusable_baseline_terms(self) -> None:
        text = CAREER_TRANSLATION_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Imported Resume", text)
        self.assertIn("Verified Resume Evidence", text)
        self.assertIn("Baseline Resume", text)
        self.assertNotIn("Reusable Initial Resume", text)
        self.assertNotIn("Candidate Profile", text)

    def test_application_workflow_uses_application_specific_terms(self) -> None:
        text = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Application Baseline", text)
        self.assertIn("Target-Market Review", text)
        self.assertIn("Verified Resume Evidence", text)
        self.assertNotIn("Career Translation Assessment", text)
        self.assertNotIn("Current source profile", text)

    def test_user_guide_explains_the_five_terms(self) -> None:
        text = USER_GUIDE.read_text(encoding="utf-8")
        self.assertIn("Resume terminology used across Career Bridge", text)
        for label in (
            "Imported Resume",
            "Verified Resume Evidence",
            "Baseline Resume",
            "Application Baseline",
            "Target-Market Review",
        ):
            self.assertIn(label, text)

    def test_legacy_initial_resume_value_displays_with_new_label(self) -> None:
        terminology = TERMINOLOGY.read_text(encoding="utf-8")
        tracker = TRACKER.read_text(encoding="utf-8")
        self.assertIn('"Initial Resume": APPLICATION_BASELINE_LABEL', terminology)
        self.assertIn('"Career Baseline Resume": CAREER_BASELINE_RESUME_LABEL', terminology)
        self.assertIn("LEGACY_RESUME_VERSION_LABELS.get", tracker)


if __name__ == "__main__":
    unittest.main()
