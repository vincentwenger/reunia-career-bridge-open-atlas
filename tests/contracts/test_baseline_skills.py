"""Contracts for editable Baseline Resume skill extraction."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "products" / "resume_taylor"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)


class BaselineSkillUpdateTests(unittest.TestCase):
    def _profile(self):
        from resume_tailor.models import CandidateProfile, ContactInfo, VerifiedSkills

        return CandidateProfile(
            name="Vincent Wenger",
            contact=ContactInfo(
                location="Portland, OR",
                phone="",
                email="vincent@example.com",
            ),
            current_summary="Software engineer.",
            skills=VerifiedSkills(
                hard_skills=["SQL", "Python"],
                soft_skills=["Communication"],
                tools_software=["Oracle"],
                industry_knowledge=["Regulatory reporting"],
                languages=["English", "French"],
            ),
            education=[],
            experiences=[],
        )

    def test_skill_update_replaces_categories_and_deduplicates(self) -> None:
        from resume_tailor.baseline_profile_updates import apply_baseline_skills

        profile = self._profile()
        changed = apply_baseline_skills(
            profile,
            {
                "hard_skills": [" SQL ", "Data modeling", "sql"],
                "soft_skills": ["Stakeholder communication"],
                "tools_software": ["AWS", "Jenkins"],
                "industry_knowledge": ["Financial services"],
                "languages": ["English", "French"],
            },
        )

        self.assertTrue(changed)
        self.assertEqual(profile.skills.hard_skills, ["SQL", "Data modeling"])
        self.assertEqual(profile.skills.tools_software, ["AWS", "Jenkins"])
        self.assertFalse(
            apply_baseline_skills(profile, profile.skills.model_dump(mode="json"))
        )

    def test_empty_category_removes_skills_from_baseline(self) -> None:
        from resume_tailor.baseline_profile_updates import apply_baseline_skills

        profile = self._profile()
        apply_baseline_skills(
            profile,
            {
                "hard_skills": [],
                "soft_skills": [],
                "tools_software": [],
                "industry_knowledge": [],
                "languages": [],
            },
        )
        self.assertEqual(profile.skills.all_non_language_skills(), [])
        self.assertEqual(profile.skills.languages, [])


class BaselineSkillInterfaceContracts(unittest.TestCase):
    def test_baseline_page_includes_editable_skills_between_summary_and_education(self) -> None:
        template = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "career_translation.html"
        ).read_text(encoding="utf-8")
        skills = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "_baseline_skills.html"
        ).read_text(encoding="utf-8")

        self.assertIn("_baseline_skills.html", template)
        self.assertLess(
            template.index("_baseline_summary.html"),
            template.index("_baseline_skills.html"),
        )
        self.assertLess(
            template.index("_baseline_skills.html"),
            template.index("_baseline_education.html"),
        )
        self.assertIn('id="skills"', skills)
        self.assertIn("Hard skills", skills)
        self.assertIn("Tools and software", skills)
        self.assertIn("Industry knowledge", skills)
        self.assertIn("Soft skills", skills)
        self.assertIn("Languages", skills)
        self.assertIn("one skill per line", skills.lower())
        self.assertIn("Save skills", skills)

    def test_skill_route_updates_foundation_baseline_and_clears_results(self) -> None:
        app_source = (ROOT / "products" / "resume_taylor" / "app.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('@application_builder_bp.put("/career-translation/skills")', app_source)
        self.assertIn("apply_baseline_skills(current.source_profile, normalized)", app_source)
        self.assertIn("current.clear_results()", app_source)
        self.assertIn('"application_builder.update_baseline_skills"', app_source)
        self.assertIn('"application_builder.update_baseline_summary"', app_source)
        self.assertIn('"application_builder.update_baseline_education"', app_source)

    def test_javascript_saves_skill_arrays_with_csrf_and_reloads(self) -> None:
        javascript = (
            ROOT
            / "products"
            / "resume_taylor"
            / "static"
            / "baseline_resume_fields.js"
        ).read_text(encoding="utf-8")

        self.assertIn("data-baseline-skills-form", javascript)
        self.assertIn("hard_skills: skillLines", javascript)
        self.assertIn("tools_software: skillLines", javascript)
        self.assertIn("industry_knowledge: skillLines", javascript)
        self.assertIn("'X-CSRFToken': csrfToken", javascript)
        self.assertIn("window.location.reload()", javascript)


if __name__ == "__main__":
    unittest.main()
