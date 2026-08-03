"""Contracts for editable Baseline Resume summary and education extraction."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "products" / "resume_taylor"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)


class BaselineSummaryEducationUpdateTests(unittest.TestCase):
    def _profile(self):
        from resume_tailor.models import (
            CandidateProfile,
            ContactInfo,
            EducationItem,
            VerifiedSkills,
        )

        return CandidateProfile(
            name="Vincent Wenger",
            contact=ContactInfo(
                location="Portland, OR",
                phone="",
                email="vincent@example.com",
            ),
            current_summary="Software engineer with regulatory reporting experience.",
            skills=VerifiedSkills(),
            education=[
                EducationItem(
                    credential="Professional Certificate in Machine Learning and Artificial Intelligence",
                    institution="University of California, Berkeley",
                    location="Berkeley, CA",
                    date="2024",
                    detail="Executive Education",
                )
            ],
            experiences=[],
        )

    def test_summary_update_changes_only_reusable_baseline_profile(self) -> None:
        from resume_tailor.baseline_profile_updates import apply_baseline_summary

        profile = self._profile()
        changed = apply_baseline_summary(
            profile,
            "Senior software engineer specializing in regulated data platforms.",
        )

        self.assertTrue(changed)
        self.assertEqual(
            profile.current_summary,
            "Senior software engineer specializing in regulated data platforms.",
        )
        self.assertFalse(apply_baseline_summary(profile, profile.current_summary))

    def test_education_update_preserves_record_position(self) -> None:
        from resume_tailor.baseline_profile_updates import apply_baseline_education

        profile = self._profile()
        changed = apply_baseline_education(
            profile,
            0,
            {
                "credential": "Professional Certificate in Machine Learning and AI",
                "institution": "UC Berkeley Executive Education",
                "location": "Berkeley, CA",
                "date": "2024",
                "detail": "Machine learning, deep learning, and applied AI",
            },
        )

        self.assertTrue(changed)
        self.assertEqual(len(profile.education), 1)
        self.assertEqual(
            profile.education[0].credential,
            "Professional Certificate in Machine Learning and AI",
        )
        self.assertEqual(profile.education[0].institution, "UC Berkeley Executive Education")

    def test_education_remove_updates_profile(self) -> None:
        from resume_tailor.baseline_profile_updates import remove_baseline_education

        profile = self._profile()
        deleted = remove_baseline_education(profile, 0)

        self.assertIn("Machine Learning", deleted.credential)
        self.assertEqual(profile.education, [])
        with self.assertRaises(IndexError):
            remove_baseline_education(profile, 0)


class BaselineSummaryEducationInterfaceContracts(unittest.TestCase):
    def test_baseline_page_includes_summary_education_and_roles(self) -> None:
        template = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "career_translation.html"
        ).read_text(encoding="utf-8")
        summary = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "_baseline_summary.html"
        ).read_text(encoding="utf-8")
        education = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "_baseline_education.html"
        ).read_text(encoding="utf-8")

        self.assertIn("_baseline_summary.html", template)
        self.assertIn("_baseline_education.html", template)
        self.assertLess(
            template.index("_baseline_summary.html"),
            template.index("_baseline_employment_roles.html"),
        )
        self.assertIn('id="professional-summary"', summary)
        self.assertIn("Professional summary", summary)
        self.assertIn("source_profile.current_summary", summary)
        self.assertIn('id="education-credentials"', education)
        self.assertIn("Education and credentials", education)
        self.assertIn("source_profile.education", education)
        self.assertIn("Official credential", education)
        self.assertIn("Remove from baseline", education)

    def test_update_routes_change_baseline_source_profile(self) -> None:
        app_source = (ROOT / "products" / "resume_taylor" / "app.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('@application_builder_bp.put("/career-translation/summary")', app_source)
        self.assertIn("apply_baseline_summary(current.source_profile, summary)", app_source)
        self.assertIn(
            '@application_builder_bp.put("/career-translation/education/<int:education_index>")',
            app_source,
        )
        self.assertIn("apply_baseline_education(", app_source)
        self.assertIn(
            '@application_builder_bp.delete("/career-translation/education/<int:education_index>")',
            app_source,
        )
        self.assertIn("remove_baseline_education(", app_source)
        self.assertIn("Applications that have not started tailoring", app_source)

    def test_javascript_saves_with_csrf_and_reloads_updated_baseline(self) -> None:
        javascript = (
            ROOT
            / "products"
            / "resume_taylor"
            / "static"
            / "baseline_resume_fields.js"
        ).read_text(encoding="utf-8")
        role_javascript = (
            ROOT
            / "products"
            / "resume_taylor"
            / "static"
            / "baseline_resume_roles.js"
        ).read_text(encoding="utf-8")

        self.assertIn("data-baseline-summary-form", javascript)
        self.assertIn("data-baseline-education-form", javascript)
        self.assertIn("data-delete-baseline-education", javascript)
        self.assertIn("'X-CSRFToken': csrfToken", javascript)
        self.assertIn("window.location.reload()", javascript)
        self.assertIn("'X-CSRFToken': csrfToken", role_javascript)


if __name__ == "__main__":
    unittest.main()
