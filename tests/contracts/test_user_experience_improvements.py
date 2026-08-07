from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class UserExperienceImprovementContracts(unittest.TestCase):
    def test_application_workspace_navigation_connects_core_surfaces(self) -> None:
        nav = (ROOT / "products/reunia/templates/components/application_workspace_nav.html").read_text(encoding="utf-8")
        for label in ("Overview", "Resume", "Materials", "Interview", "Actions", "Activity"):
            self.assertIn(f">{label}</a>", nav)
        self.assertIn("application_id=", nav)

    def test_application_cards_link_directly_to_materials(self) -> None:
        template = (ROOT / "products/resume_taylor/templates/application_builder/applications.html").read_text(encoding="utf-8")
        self.assertIn("/application-materials?application_id=", template)
        self.assertIn("data-application-menu", template)
        self.assertIn('role="menuitem"', template)

    def test_baseline_edits_use_partial_refresh_and_preserve_focus(self) -> None:
        fields = (ROOT / "products/resume_taylor/static/baseline_resume_fields.js").read_text(encoding="utf-8")
        roles = (ROOT / "products/resume_taylor/static/baseline_resume_roles.js").read_text(encoding="utf-8")
        self.assertIn("DOMParser", fields)
        self.assertIn("window.scrollTo", fields)
        self.assertIn("preventScroll", fields)
        self.assertNotIn("window.location.reload()", fields)
        self.assertNotIn("window.location.reload()", roles)

    def test_builder_language_and_language_explanation_are_explicit(self) -> None:
        base = (ROOT / "products/resume_taylor/templates/application_builder/base.html").read_text(encoding="utf-8")
        translation = (ROOT / "products/resume_taylor/templates/application_builder/career_translation.html").read_text(encoding="utf-8")
        self.assertIn("<html lang=\"{{ app_language | default('en') }}\">", base)
        self.assertIn("Interface language", translation)
        self.assertIn("Imported resume language", translation)
        self.assertIn("Baseline Resume language", translation)
        self.assertIn("Target job market", translation)

    def test_accessibility_hooks_cover_focus_menus_and_dialogs(self) -> None:
        styles = (ROOT / "products/reunia/static/css/career-theme.css").read_text(encoding="utf-8")
        builder_js = (ROOT / "products/resume_taylor/static/app-shell.js").read_text(encoding="utf-8")
        action_js = (ROOT / "products/reunia/static/js/pages/action-center.js").read_text(encoding="utf-8")
        resume = (ROOT / "products/resume_taylor/templates/application_builder/index.html").read_text(encoding="utf-8")
        self.assertIn(":focus-visible", styles)
        self.assertIn("ArrowDown", builder_js)
        self.assertIn("Escape", builder_js)
        self.assertIn("event.key === 'Tab'", action_js)
        self.assertIn('for="resume-hard-skills-{{ version }}"', resume)
        self.assertIn('id="resume-hard-skills-{{ version }}"', resume)


if __name__ == "__main__":
    unittest.main()
