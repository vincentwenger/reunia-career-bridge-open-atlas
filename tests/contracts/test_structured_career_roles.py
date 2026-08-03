"""Contracts for Baseline Resume employment-role extraction and reuse."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "products" / "reunia", ROOT / "products" / "resume_taylor"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

try:
    import flask  # noqa: F401
    import werkzeug  # noqa: F401
except ModuleNotFoundError:
    SERVICE_DEPENDENCIES_AVAILABLE = False
else:
    SERVICE_DEPENDENCIES_AVAILABLE = True
    from meeting_assistant.repositories.knowledge_repository import InMemoryKnowledgeRepository
    from meeting_assistant.services.knowledge_service import KnowledgeService


class _UnusedFileStore:
    pass


class _UnusedUserService:
    pass


class CareerRoleDateOrderingTests(unittest.TestCase):
    def test_sort_key_orders_current_recent_old_and_undated_roles(self) -> None:
        from career_bridge.career_role_dates import career_role_date_sort_key

        roles = [
            {"role_id": "old", "dates": "10/2007 - 08/2010"},
            {"role_id": "recent", "dates": "09/2013 - 05/2025"},
            {"role_id": "current", "dates": "June 2025 - Present"},
            {"role_id": "undated", "dates": ""},
        ]

        roles.sort(key=career_role_date_sort_key)

        self.assertEqual(
            [role["role_id"] for role in roles],
            ["current", "recent", "old", "undated"],
        )

    def test_sort_key_supports_french_months_and_current_marker(self) -> None:
        from career_bridge.career_role_dates import career_role_date_sort_key

        roles = [
            {"role_id": "may", "dates": "février 2025 - mai 2025"},
            {"role_id": "june", "dates": "mai 2025 - juin 2025"},
            {"role_id": "current", "dates": "juillet 2025 - présent"},
        ]

        roles.sort(key=career_role_date_sort_key)

        self.assertEqual(
            [role["role_id"] for role in roles],
            ["current", "june", "may"],
        )




class BaselineRoleProfileUpdateTests(unittest.TestCase):
    def test_reviewed_source_fields_update_baseline_experience_and_preserve_bullet_ids(self) -> None:
        from resume_tailor.baseline_role_updates import apply_career_role_to_profile
        from resume_tailor.models import (
            CandidateProfile,
            ContactInfo,
            Experience,
            ResumeBullet,
            VerifiedSkills,
        )

        profile = CandidateProfile(
            name="Vincent Wenger",
            contact=ContactInfo(location="Portland", phone="", email="vincent@example.com"),
            current_summary="Software engineer",
            skills=VerifiedSkills(),
            education=[],
            experiences=[
                Experience(
                    id="EXP-001",
                    employer="Nasdaq",
                    location="London",
                    dates="2011-2025",
                    title="Software Engineer",
                    bullets=[
                        ResumeBullet(id="EXP-001-B01", text="Built reporting systems."),
                        ResumeBullet(id="EXP-001-B02", text="Supported production."),
                    ],
                )
            ],
        )

        changed = apply_career_role_to_profile(
            profile,
            {
                "source_experience_id": "EXP-001",
                "official_title": "Senior Software Engineer",
                "employer": "Nasdaq Verafin",
                "dates": "2011 - Present",
                "location": "Portland, OR",
                "responsibilities": "• Designed regulatory reporting systems.\n- Tuned SQL performance.\n3. Supported high-availability production services.",
                "target_market_title": "Lead Engineer",
            },
        )

        self.assertTrue(changed)
        experience = profile.experiences[0]
        self.assertEqual(experience.title, "Senior Software Engineer")
        self.assertEqual(experience.employer, "Nasdaq Verafin")
        self.assertEqual(experience.dates, "2011 - Present")
        self.assertEqual(experience.location, "Portland, OR")
        self.assertEqual(
            [bullet.text for bullet in experience.bullets],
            [
                "Designed regulatory reporting systems.",
                "Tuned SQL performance.",
                "Supported high-availability production services.",
            ],
        )
        self.assertEqual(experience.bullets[0].id, "EXP-001-B01")
        self.assertEqual(experience.bullets[1].id, "EXP-001-B02")
        self.assertTrue(experience.bullets[2].id.startswith("EXP-001-EDIT-03"))
        self.assertNotEqual(experience.title, "Lead Engineer")


@unittest.skipUnless(
    SERVICE_DEPENDENCIES_AVAILABLE,
    "Flask/Werkzeug dependencies are not installed in this validation environment.",
)
class StructuredCareerRoleStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryKnowledgeRepository()
        self.service = KnowledgeService(
            repository=self.repository,
            file_store=_UnusedFileStore(),
            user_service=_UnusedUserService(),
        )
        self.entry = {
            "source_experience_id": "EXP-001",
            "official_title": "Software Engineer",
            "employer": "Nasdaq",
            "dates": "2011–2025",
            "location": "London, UK",
            "responsibilities": "• Developed regulatory reporting systems.\n• Supported production services.",
        }

    def test_baseline_sync_creates_stable_review_record(self) -> None:
        saved = self.service.sync_career_roles_from_baseline(
            "user-1", [self.entry], source_fingerprint="baseline-v1", target_market="United States"
        )
        self.assertEqual(saved[0]["role_id"], "EXP-001")
        self.assertEqual(saved[0]["status"], "needs_review")
        self.assertEqual(saved[0]["target_market_title"], "Software Engineer")
        self.assertIn("regulatory reporting", saved[0]["responsibilities"])

    def test_confirmed_interpretation_survives_unchanged_regeneration(self) -> None:
        role = self.service.sync_career_roles_from_baseline("user-1", [self.entry])[0]
        confirmed = self.service.update_career_role(
            "user-1",
            role["role_id"],
            {
                "target_market_title": "Software Engineer",
                "recruiter_explanation": "No translation is required in the U.S. market.",
                "status": "confirmed",
            },
        )
        regenerated = self.service.sync_career_roles_from_baseline(
            "user-1", [self.entry], source_fingerprint="baseline-v2"
        )[0]
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(regenerated["status"], "confirmed")
        self.assertIn("No translation", regenerated["recruiter_explanation"])

    def test_material_source_change_returns_role_to_review(self) -> None:
        role = self.service.sync_career_roles_from_baseline("user-1", [self.entry])[0]
        self.service.update_career_role(
            "user-1", role["role_id"], {"status": "confirmed", "target_market_title": "Software Engineer"}
        )
        changed = dict(self.entry)
        changed["official_title"] = "Senior Software Engineer"
        regenerated = self.service.sync_career_roles_from_baseline("user-1", [changed])[0]
        self.assertEqual(regenerated["status"], "needs_review")
        self.assertEqual(regenerated["target_market_title"], "Senior Software Engineer")

    def test_removed_baseline_role_becomes_inactive_not_deleted(self) -> None:
        self.service.sync_career_roles_from_baseline("user-1", [self.entry])
        self.service.sync_career_roles_from_baseline("user-1", [])
        roles = self.service.list_career_roles("user-1")
        self.assertEqual(len(roles), 1)
        self.assertFalse(roles[0]["source_active"])

    def test_roles_are_ordered_by_dates_with_most_recent_first(self) -> None:
        entries = [
            {
                **self.entry,
                "source_experience_id": "EXP-OLD",
                "employer": "Older Employer",
                "dates": "10/2007 - 08/2010",
            },
            {
                **self.entry,
                "source_experience_id": "EXP-RECENT",
                "employer": "Recent Employer",
                "dates": "09/2013 - 05/2025",
            },
            {
                **self.entry,
                "source_experience_id": "EXP-CURRENT",
                "employer": "Current Employer",
                "dates": "June 2025 - Present",
            },
            {
                **self.entry,
                "source_experience_id": "EXP-UNDATED",
                "employer": "Undated Employer",
                "dates": "",
            },
        ]
        self.service.sync_career_roles_from_baseline("user-1", entries)

        roles = self.service.list_career_roles("user-1")

        self.assertEqual(
            [role["role_id"] for role in roles],
            ["EXP-CURRENT", "EXP-RECENT", "EXP-OLD", "EXP-UNDATED"],
        )

    def test_date_sort_supports_french_current_marker_and_month_precision(self) -> None:
        entries = [
            {
                **self.entry,
                "source_experience_id": "EXP-MAY",
                "employer": "May Employer",
                "dates": "mai 2025 - juin 2025",
            },
            {
                **self.entry,
                "source_experience_id": "EXP-FEB",
                "employer": "February Employer",
                "dates": "février 2025 - mai 2025",
            },
            {
                **self.entry,
                "source_experience_id": "EXP-PRESENT",
                "employer": "Present Employer",
                "dates": "juillet 2025 - présent",
            },
        ]
        self.service.sync_career_roles_from_baseline("user-1", entries)

        roles = self.service.list_career_roles("user-1")

        self.assertEqual(
            [role["role_id"] for role in roles],
            ["EXP-PRESENT", "EXP-MAY", "EXP-FEB"],
        )

    def test_user_edit_fingerprint_survives_matching_baseline_sync(self) -> None:
        role = self.service.sync_career_roles_from_baseline("user-1", [self.entry])[0]
        updated_entry = dict(self.entry)
        updated_entry["official_title"] = "Senior Software Engineer"
        self.service.update_career_role(
            "user-1",
            role["role_id"],
            {
                **updated_entry,
                "target_market_title": "Senior Software Engineer",
                "status": "confirmed",
            },
        )

        regenerated = self.service.sync_career_roles_from_baseline(
            "user-1", [updated_entry], source_fingerprint="baseline-v2"
        )[0]

        self.assertEqual(regenerated["official_title"], "Senior Software Engineer")
        self.assertEqual(regenerated["status"], "confirmed")


class StructuredCareerRoleInterfaceContracts(unittest.TestCase):
    def test_baseline_resume_has_employment_role_panel_and_actions(self) -> None:
        baseline_template = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "career_translation.html"
        ).read_text(encoding="utf-8")
        panel = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "_baseline_employment_roles.html"
        ).read_text(encoding="utf-8")
        evidence_library = (
            ROOT / "products" / "reunia" / "templates" / "knowledge.html"
        ).read_text(encoding="utf-8")
        self.assertIn("_baseline_employment_roles.html", baseline_template)
        self.assertIn('id="employment-roles"', panel)
        self.assertIn("Baseline Resume information", panel)
        self.assertIn("Employment roles and job titles", panel)
        self.assertIn("Closest target-market title", panel)
        self.assertIn("Confirm as written", panel)
        self.assertIn("automatically updates the Baseline Resume", panel)
        self.assertIn("application_builder.career_translation_workspace", panel)
        self.assertIn("data-delete-career-role", panel)
        self.assertIn('class="employment-role-responsibilities-cell"', panel)
        self.assertIn('rows="6"', panel)
        self.assertIn('class="employment-role-status-cell"', panel)
        role_css = (
            ROOT / "products" / "resume_taylor" / "static" / "career_bridge.css"
        ).read_text(encoding="utf-8")
        self.assertIn("min-width: 420px", role_css)
        self.assertIn("width: 150px", role_css)
        self.assertNotIn('id="employment-roles"', evidence_library)
        self.assertIn("Additional and confirmed career evidence", evidence_library)

    def test_role_api_is_owner_scoped(self) -> None:
        routes = (
            ROOT
            / "products"
            / "reunia"
            / "meeting_assistant"
            / "blueprints"
            / "knowledge"
            / "routes.py"
        ).read_text(encoding="utf-8")
        self.assertIn('@knowledge_bp.put("/api/career/baseline/roles/<role_id>")', routes)
        self.assertIn('@knowledge_bp.delete("/api/career/baseline/roles/<role_id>")', routes)
        self.assertIn('@knowledge_bp.put("/api/career/evidence/roles/<role_id>")', routes)
        self.assertIn("g.current_user_id,", routes)

    def test_baseline_generation_syncs_roles_and_title_findings_link_to_baseline(self) -> None:
        app_source = (ROOT / "products" / "resume_taylor" / "app.py").read_text(encoding="utf-8")
        template = (
            ROOT
            / "products"
            / "resume_taylor"
            / "templates"
            / "application_builder"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("_sync_baseline_roles_to_evidence_library(current)", app_source)
        self.assertIn("career_roles = _knowledge_evidence_service().list_career_roles", app_source)
        self.assertIn("_apply_confirmed_title_interpretations(", app_source)
        self.assertIn('@application_builder_bp.put("/career-translation/roles/<role_id>")', app_source)
        self.assertIn("apply_career_role_to_profile(current.source_profile, updated_role)", app_source)
        self.assertIn('"application_builder.update_baseline_career_role"', app_source)
        self.assertIn("career_translation_assessment_view(\n            proposal\n        )", app_source)
        self.assertIn("Review title evidence", template)
        self.assertIn("career_translation_workspace') }}#employment-roles", template)

    def test_baseline_javascript_saves_confirms_and_removes_roles(self) -> None:
        javascript = (
            ROOT
            / "products"
            / "resume_taylor"
            / "static"
            / "baseline_resume_roles.js"
        ).read_text(encoding="utf-8")
        knowledge_javascript = (
            ROOT / "products" / "reunia" / "static" / "js" / "pages" / "knowledge.js"
        ).read_text(encoding="utf-8")
        self.assertIn("[data-career-role-form]", javascript)
        self.assertIn("[data-confirm-career-role]", javascript)
        self.assertIn("[data-delete-career-role]", javascript)
        self.assertIn("method: 'PUT'", javascript)
        self.assertIn("method: 'DELETE'", javascript)
        self.assertIn("result.baseline_updated", javascript)
        self.assertIn("window.location.reload()", javascript)
        self.assertNotIn("[data-career-role-form]", knowledge_javascript)


if __name__ == "__main__":
    unittest.main()
