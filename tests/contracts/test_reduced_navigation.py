"""Contracts for the reduced, shared Career Bridge navigation."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAVBAR = ROOT / "products" / "reunia" / "templates" / "navbar.html"
BUILDER_BASE = ROOT / "products" / "resume_taylor" / "templates" / "application_builder" / "base.html"
DUPLICATE_NAVBAR = ROOT / "products" / "resume_taylor" / "templates" / "application_builder" / "career_bridge_navbar.html"
ROOT_AREA_NAMES = ("Foundation", "Jobs &amp; Applications", "Interviews", "Progress")


class ReducedNavigationContractTests(unittest.TestCase):
    def test_application_builder_uses_the_single_shared_navbar(self) -> None:
        base = BUILDER_BASE.read_text(encoding="utf-8")
        self.assertIn("{% include 'navbar.html' %}", base)
        self.assertNotIn("career_bridge_navbar.html", base)
        self.assertFalse(DUPLICATE_NAVBAR.exists())

    def test_navbar_exposes_exactly_four_compact_groups(self) -> None:
        text = NAVBAR.read_text(encoding="utf-8")
        self.assertEqual(text.count("data-nav-group>"), 4)
        for area_name in ROOT_AREA_NAMES:
            self.assertEqual(text.count(f"<span>{area_name}</span>"), 1)
        for obsolete_name in (
            "Career Foundation",
            "Build Your Application",
            "Prepare and Practice",
            "Improve and Take Action",
        ):
            self.assertNotIn(f"<span>{obsolete_name}</span>", text)

    def test_only_global_destinations_are_in_primary_navigation(self) -> None:
        text = NAVBAR.read_text(encoding="utf-8")
        foundation_start = text.index("<span>Foundation</span>")
        jobs_start = text.index("<span>Jobs &amp; Applications</span>")
        interviews_start = text.index("<span>Interviews</span>")
        progress_start = text.index("<span>Progress</span>")
        authenticated_end = text.index("{% endif %}\n      </ul>", progress_start)

        foundation = text[foundation_start:jobs_start]
        jobs = text[jobs_start:interviews_start]
        interviews = text[interviews_start:progress_start]
        progress = text[progress_start:authenticated_end]

        for label in ("Career Profile", "Baseline Resume", "Evidence Library"):
            self.assertIn(label, foundation)
        for label in ("Job Discovery", "Applications"):
            self.assertIn(label, jobs)
        for label in ("Interview Preparation", "Mock Interview", "Interview Review"):
            self.assertIn(label, interviews)
        for label in ("Action Plan", "Progress &amp; Outcomes"):
            self.assertIn(label, progress)

        for application_specific_label in (
            "Resume Workflow",
            "Resume Reports",
            "Application Materials",
        ):
            self.assertNotIn(f"<strong>{application_specific_label}</strong>", text)

    def test_job_discovery_precedes_applications(self) -> None:
        text = NAVBAR.read_text(encoding="utf-8")
        jobs = text[text.index("<span>Jobs &amp; Applications</span>"):text.index("<span>Interviews</span>")]
        self.assertLess(jobs.index("<strong>Job Discovery</strong>"), jobs.index("<strong>Applications</strong>"))
        self.assertIn("{% set job_discovery_url = '/applications/job-discovery' %}", text)

    def test_active_groups_cover_hidden_application_specific_pages(self) -> None:
        text = NAVBAR.read_text(encoding="utf-8")
        self.assertIn("{% set foundation_group_active = career_profile_active or baseline_resume_active or career_evidence_library_active %}", text)
        self.assertIn("{% set jobs_group_active = job_discovery_active or job_applications_active or resume_workflow_active or resume_reports_active or application_materials_active %}", text)
        self.assertIn("{% set interviews_group_active = interview_preparation_active or mock_interview_active or interview_review_active %}", text)
        self.assertIn("{% set progress_group_active = career_action_plan_active or progress_active %}", text)

    def test_ai_configuration_remains_in_the_admin_account_menu(self) -> None:
        text = NAVBAR.read_text(encoding="utf-8")
        account = text[text.index('id="accountDropdownMenu"'):]
        self.assertIn("{% if is_admin_session %}", account)
        self.assertIn("AI Configuration", account)
        self.assertIn("builder_configuration_url", account)
        self.assertNotIn("AI Configuration", text[text.index('<ul class="nav-menu">'):text.index('</ul>')])

    def test_homepage_remains_a_minimal_action_dashboard(self) -> None:
        template = (ROOT / "products" / "reunia" / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "products" / "reunia" / "static" / "js" / "pages" / "index.js").read_text(encoding="utf-8")
        for obsolete_copy in (
            "Career Bridge workspace",
            "Quick actions",
            'class="home-module-card"',
            'class="home-module-grid"',
            'class="home-metrics"',
            'class="home-metric"',
            'class="home-hero-panel"',
        ):
            self.assertNotIn(obsolete_copy, template)
        for label in ("Your next step", "Current application", "Career Foundation", "Other actions"):
            self.assertIn(label, template)
        for foundation_label in ("Career Profile", "Baseline Resume", "Career Evidence Library"):
            self.assertIn(foundation_label, template)
        self.assertIn("Open application", script)

    def test_new_group_names_are_available_in_french(self) -> None:
        text = (ROOT / "products" / "reunia" / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
        expected = {
            "Foundation": "Fondation",
            "Jobs & Applications": "Emplois et candidatures",
            "Interviews": "Entretiens",
            "Progress": "Progression",
        }
        for source, translation in expected.items():
            self.assertIn(f"'{source}': '{translation}'", text)

    def test_live_assistance_never_reappears_in_candidate_navigation(self) -> None:
        self.assertNotIn("Live Assistance", NAVBAR.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
