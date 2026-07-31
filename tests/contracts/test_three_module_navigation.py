"""Contract tests for Career Foundation plus the three application modules."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAVBAR_TEMPLATES = (
    ROOT / "products" / "reunia" / "templates" / "navbar.html",
    ROOT / "products" / "resume_taylor" / "templates" / "career_bridge_navbar.html",
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "career_bridge_navbar.html",
)
FOUNDATION_NAME = "Career Foundation"
MODULE_NAMES = (
    "Build Your Application",
    "Prepare and Practice",
    "Improve and Take Action",
)
ROOT_AREA_NAMES = (FOUNDATION_NAME, *MODULE_NAMES)
ADMIN_ANALYTICS_TEMPLATE = (
    ROOT / "products" / "reunia" / "templates" / "admin-analytics.html"
)
ADMIN_ANALYTICS_SCRIPT = (
    ROOT / "products" / "reunia" / "static" / "js" / "pages" / "admin-analytics.js"
)
ADMIN_ANALYTICS_SERVICE = (
    ROOT
    / "products"
    / "reunia"
    / "meeting_assistant"
    / "services"
    / "admin_analytics_service.py"
)


class ThreeModuleNavigationContractTests(unittest.TestCase):
    """Keep reusable setup separate from the same three application modules."""

    def test_all_navbar_copies_are_synchronized(self) -> None:
        contents = [path.read_text(encoding="utf-8") for path in NAVBAR_TEMPLATES]
        self.assertTrue(all(content == contents[0] for content in contents[1:]))

    def test_navbar_exposes_foundation_plus_exactly_three_product_modules(self) -> None:
        for path in NAVBAR_TEMPLATES:
            with self.subTest(template=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text.count("data-nav-group>"), 4)
                self.assertEqual(
                    text.count('class="nav-item nav-group" data-nav-group'),
                    3,
                )
                self.assertEqual(
                    text.count(
                        'class="nav-item nav-group nav-foundation-group" data-nav-group'
                    ),
                    1,
                )
                for area_name in ROOT_AREA_NAMES:
                    self.assertEqual(text.count(f"<span>{area_name}</span>"), 1)
                for obsolete_name in ("Career", "Applications", "Interviews", "Growth"):
                    self.assertNotIn(f"<span>{obsolete_name}</span>", text)

    def test_features_are_grouped_by_scope_and_product_outcome(self) -> None:
        text = NAVBAR_TEMPLATES[0].read_text(encoding="utf-8")
        foundation_start = text.index("<span>Career Foundation</span>")
        build_start = text.index("<span>Build Your Application</span>")
        prepare_start = text.index("<span>Prepare and Practice</span>")
        improve_start = text.index("<span>Improve and Take Action</span>")
        authenticated_end = text.index("{% endif %}\n      </ul>", improve_start)

        foundation_section = text[foundation_start:build_start]
        build_section = text[build_start:prepare_start]
        prepare_section = text[prepare_start:improve_start]
        improve_section = text[improve_start:authenticated_end]

        for label in ("Career Profile", "Career Translation", "Career Evidence Library"):
            self.assertIn(label, foundation_section)
            self.assertNotIn(label, build_section)

        for label in (
            "Job Discovery",
            "Job Applications",
            "Resume Workflow",
            "Resume Reports",
            "Application Materials",
        ):
            self.assertIn(label, build_section)
            self.assertNotIn(label, foundation_section)

        for label in ("Interview Preparation", "Mock Interview"):
            self.assertIn(label, prepare_section)
        self.assertNotIn("Live Assistance", prepare_section)
        self.assertNotIn("Interview Review", prepare_section)

        for label in ("Interview Review", "Career Action Plan", "Impact &amp; Progress"):
            self.assertIn(label, improve_section)

        self.assertLess(foundation_start, build_start)
        self.assertNotIn("AI Configuration", build_section)
        account_section = text[text.index('id="accountDropdownMenu"') :]
        self.assertIn("AI Configuration", account_section)
        self.assertIn("builder_configuration_url", account_section)

    def test_job_discovery_precedes_job_applications_in_build_workflow(self) -> None:
        text = NAVBAR_TEMPLATES[0].read_text(encoding="utf-8")
        build_start = text.index("<span>Build Your Application</span>")
        prepare_start = text.index("<span>Prepare and Practice</span>")
        build_section = text[build_start:prepare_start]
        self.assertLess(
            build_section.index("<strong>Job Discovery</strong>"),
            build_section.index("<strong>Job Applications</strong>"),
        )
        self.assertIn("{% set job_discovery_url = '/applications/job-discovery' %}", text)

    def test_home_is_not_an_additional_primary_navigation_item(self) -> None:
        for path in NAVBAR_TEMPLATES:
            with self.subTest(template=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("MVP Journey", text)
                self.assertNotIn("overview_active", text)

    def test_homepage_separates_foundation_from_three_modules(self) -> None:
        text = (ROOT / "products" / "reunia" / "templates" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertEqual(text.count('class="home-module-card '), 3)
        self.assertEqual(text.count("data-mvp-step"), 10)
        for module_name in MODULE_NAMES:
            self.assertIn(f">{module_name}</h3>", text)
        self.assertNotIn("Secondary features", text)
        self.assertNotIn("outside the core demo", text)

        self.assertEqual(text.count('class="home-foundation-card"'), 1)
        foundation_start = text.index('class="home-foundation-card"')
        module_start = text.index('class="home-module-journey"')
        foundation_section = text[foundation_start:module_start]
        build_start = text.index('aria-label="Build Your Application tools"')
        build_end = text.index("</nav>", build_start)
        build_tools = text[build_start:build_end]

        for label in ("Career Profile", "Career Translation", "Career Evidence Library"):
            self.assertIn(label, foundation_section)
            self.assertNotIn(label, build_tools)
        for label in ("Job Discovery", "Job Applications", "Resume Workflow"):
            self.assertIn(label, build_tools)

    def test_ai_configuration_is_account_scoped_not_builder_navigation(self) -> None:
        for relative_path in (
            "products/resume_taylor/templates/base.html",
            "products/resume_taylor/templates/application_builder/base.html",
        ):
            with self.subTest(template=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                subnav_start = text.index('class="application-builder-subnav"')
                subnav_end = text.index("{% endif %}", subnav_start)
                subnav = text[subnav_start:subnav_end]
                self.assertNotIn("Configuration", subnav)
                self.assertIn(
                    "active_tab not in ['interview_preparation', 'configuration']", text
                )
                self.assertIn("<h1>AI Configuration</h1>", text)
                self.assertIn("Account settings", text)

    def test_active_state_contract_separates_foundation_from_three_modules(self) -> None:
        text = NAVBAR_TEMPLATES[0].read_text(encoding="utf-8")
        self.assertIn(
            "{% set career_foundation_group_active = career_profile_active or "
            "career_translation_active or career_evidence_library_active %}",
            text,
        )
        self.assertIn(
            "{% set build_application_group_active = job_discovery_active or "
            "job_applications_active or resume_workflow_active or resume_reports_active "
            "or application_materials_active %}",
            text,
        )
        self.assertIn(
            "{% set prepare_practice_group_active = interview_preparation_active "
            "or mock_interview_active %}",
            text,
        )
        self.assertIn(
            "{% set improve_action_group_active = interview_review_active or "
            "career_action_plan_active or progress_active %}",
            text,
        )

    def test_root_area_names_are_available_in_french_navigation(self) -> None:
        text = (ROOT / "products" / "reunia" / "static" / "js" / "i18n.js").read_text(
            encoding="utf-8"
        )
        expected = {
            "Career Foundation": "Fondation de carrière",
            "Build Your Application": "Construire votre candidature",
            "Prepare and Practice": "Se préparer et s’entraîner",
            "Improve and Take Action": "S’améliorer et passer à l’action",
        }
        for source, translation in expected.items():
            self.assertIn(f"'{source}': '{translation}'", text)

    def test_public_workflow_and_user_guide_use_the_same_names(self) -> None:
        marketing = (
            ROOT / "products" / "reunia" / "templates" / "_marketing_content.html"
        ).read_text(encoding="utf-8")
        guide = (
            ROOT / "products" / "reunia" / "templates" / "user-guide.html"
        ).read_text(encoding="utf-8")

        for module_name in MODULE_NAMES:
            with self.subTest(module=module_name):
                self.assertIn(module_name, marketing)
                self.assertIn(module_name, guide)
        self.assertIn('href="#career-foundation">Career Foundation</a>', guide)
        self.assertIn('id="career-foundation"', guide)
        self.assertIn('id="build-application"', guide)

    def test_live_assistance_never_reappears_in_candidate_navigation(self) -> None:
        for path in NAVBAR_TEMPLATES:
            with self.subTest(template=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("Live Assistance", text)
                self.assertNotIn("live_interview_assistance_enabled", text)
                self.assertNotIn("live_interview_assistance_url", text)
                self.assertNotIn("live_interview_assistance_active", text)

    def test_admin_copy_uses_career_interview_terminology(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ADMIN_ANALYTICS_TEMPLATE,
                ADMIN_ANALYTICS_SCRIPT,
                ADMIN_ANALYTICS_SERVICE,
            )
        )
        for required in (
            "Interview practice funnel",
            "Saved mock interviews",
            "Interview Review opened",
            "Live Assistance",
        ):
            self.assertIn(required, combined)

        for obsolete in (
            "Meeting funnel",
            "Saved meetings",
            "Meeting Review opened",
            "Recorded and saved meeting reviews",
        ):
            self.assertNotIn(obsolete, combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
