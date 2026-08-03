"""Contracts for creating a Baseline Resume without an uploaded file."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "products" / "resume_taylor"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)


class ManualBaselineInterfaceContracts(unittest.TestCase):
    def test_baseline_start_screen_offers_import_and_manual_paths(self) -> None:
        template = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "career_translation.html"
        ).read_text(encoding="utf-8")
        self.assertIn("How would you like to create your Baseline Resume?", template)
        self.assertIn("Import an existing resume", template)
        self.assertIn("Build my Baseline Resume manually", template)
        self.assertIn("application_builder.start_manual_baseline", template)
        self.assertIn('name="import_strategy"', template)
        self.assertIn("Replace the current Baseline Resume", template)
        self.assertIn("Merge new information for review", template)

    def test_manual_entry_controls_cover_repeatable_sections(self) -> None:
        education = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "_baseline_education.html"
        ).read_text(encoding="utf-8")
        roles = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "_baseline_employment_roles.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Add education or credential", education)
        self.assertIn("data-add-baseline-education-form", education)
        self.assertIn("Add employment role", roles)
        self.assertIn("data-add-career-role-form", roles)
        self.assertIn("Enter one resume bullet per line", roles)

    def test_routes_and_javascript_keep_manual_creation_in_shared_baseline(self) -> None:
        app_source = (ROOT / "products" / "resume_taylor" / "app.py").read_text(
            encoding="utf-8"
        )
        fields_js = (
            ROOT / "products" / "resume_taylor" / "static" / "baseline_resume_fields.js"
        ).read_text(encoding="utf-8")
        roles_js = (
            ROOT / "products" / "resume_taylor" / "static" / "baseline_resume_roles.js"
        ).read_text(encoding="utf-8")
        self.assertIn('@application_builder_bp.post("/career-translation/manual")', app_source)
        self.assertIn('@application_builder_bp.post("/career-translation/education")', app_source)
        self.assertIn('@application_builder_bp.post("/career-translation/roles")', app_source)
        self.assertIn("append_manual_experience", app_source)
        self.assertIn("merge_candidate_profiles", app_source)
        self.assertIn("method: 'POST'", fields_js)
        self.assertIn("method: 'POST'", roles_js)
        self.assertIn("X-CSRFToken", fields_js)
        self.assertIn("X-CSRFToken", roles_js)


class ManualBaselineDataTests(unittest.TestCase):
    def _empty_profile(self):
        from resume_tailor.models import CandidateProfile, ContactInfo, VerifiedSkills

        return CandidateProfile(
            name="",
            contact=ContactInfo(location="", phone="", email=""),
            current_summary="",
            skills=VerifiedSkills(),
            education=[],
            experiences=[],
        )

    def test_manual_role_and_education_receive_reusable_structure(self) -> None:
        from resume_tailor.baseline_profile_updates import append_baseline_education
        from resume_tailor.baseline_role_updates import append_manual_experience

        profile = self._empty_profile()
        education_index = append_baseline_education(
            profile,
            {
                "credential": "Professional Certificate in Machine Learning and AI",
                "institution": "UC Berkeley Executive Education",
                "date": "2024",
            },
        )
        role = append_manual_experience(
            profile,
            {
                "official_title": "Software Engineer",
                "employer": "Nasdaq",
                "dates": "2013-2025",
                "responsibilities": "Built regulatory reports.\nOptimized SQL queries.",
            },
        )
        self.assertEqual(education_index, 0)
        self.assertEqual(role.id, "MAN-EXP-001")
        self.assertEqual(
            [item.id for item in role.bullets],
            ["MAN-EXP-001-BULLET-01", "MAN-EXP-001-BULLET-02"],
        )

    def test_merge_keeps_manual_facts_and_adds_distinct_imported_facts(self) -> None:
        from resume_tailor.baseline_manual import merge_candidate_profiles
        from resume_tailor.models import EducationItem, Experience, ResumeBullet

        manual = self._empty_profile()
        manual.current_summary = "Manual summary"
        manual.skills.hard_skills = ["SQL"]
        manual.experiences = [
            Experience(
                id="MAN-EXP-001",
                employer="Nasdaq",
                location="Portland, OR",
                dates="2013-2025",
                title="Software Engineer",
                bullets=[ResumeBullet(id="MAN-EXP-001-BULLET-01", text="Built reports.")],
            )
        ]
        imported = self._empty_profile()
        imported.current_summary = "Imported summary"
        imported.skills.hard_skills = ["Python", "SQL"]
        imported.education = [
            EducationItem(
                credential="Certificate",
                institution="UC Berkeley",
                date="2024",
            )
        ]
        imported.experiences = [
            Experience(
                id="EXP-001",
                employer="Nasdaq",
                location="Portland, OR",
                dates="2013-2025",
                title="Software Engineer",
                bullets=[ResumeBullet(id="EXP-001-B01", text="Optimized SQL queries.")],
            )
        ]

        merged = merge_candidate_profiles(imported, manual)

        self.assertEqual(merged.current_summary, "Manual summary")
        self.assertEqual(merged.skills.hard_skills, ["SQL", "Python"])
        self.assertEqual(len(merged.education), 1)
        self.assertEqual(len(merged.experiences), 1)
        self.assertEqual(
            [item.text for item in merged.experiences[0].bullets],
            ["Built reports.", "Optimized SQL queries."],
        )

    def test_manual_source_round_trips_with_workflow_state(self) -> None:
        from resume_tailor.web_state import WorkflowState
        from resume_tailor.workflow_serialization import (
            workflow_state_from_json_bytes,
            workflow_state_json_bytes,
        )

        profile = self._empty_profile()
        profile.current_summary = "User-confirmed manual summary"
        state = WorkflowState(
            source_profile=profile.model_copy(deep=True),
            baseline_creation_method="manual",
            manual_source_profile=profile.model_copy(deep=True),
        )

        restored = workflow_state_from_json_bytes(workflow_state_json_bytes(state))

        self.assertEqual(restored.baseline_creation_method, "manual")
        self.assertIsNotNone(restored.manual_source_profile)
        self.assertEqual(
            restored.manual_source_profile.current_summary,
            "User-confirmed manual summary",
        )


if __name__ == "__main__":
    unittest.main()
