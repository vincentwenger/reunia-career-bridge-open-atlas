from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "applications.html"
)
INTERVIEW_PREPARATION = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "interview_preparation.html"
)
APP_SHELL_JS = ROOT / "products" / "resume_taylor" / "static" / "app-shell.js"


class CollapsedFormLinkTests(unittest.TestCase):
    def test_create_application_anchor_is_on_the_collapsible_panel(self) -> None:
        source = APPLICATIONS.read_text(encoding="utf-8")
        self.assertIn('<details id="new-application"', source)
        self.assertNotIn('<section class="card application-add-card" id="new-application">', source)

    def test_each_edit_panel_has_a_direct_hash_target(self) -> None:
        source = APPLICATIONS.read_text(encoding="utf-8")
        self.assertIn(
            'class="application-edit-details" id="edit-application-{{ application.id }}"',
            source,
        )
        self.assertIn('id="job-description-{{ application.id }}"', source)

    def test_interview_actions_target_content_inside_collapsed_edit_panel(self) -> None:
        source = INTERVIEW_PREPARATION.read_text(encoding="utf-8")
        self.assertIn(
            '#edit-application-{{ selected_application.id }}">Edit application</a>',
            source,
        )
        self.assertIn(
            '#job-description-{{ selected_application.id }}">Add job description</a>',
            source,
        )
        self.assertNotIn(
            '#application-{{ selected_application.id }}">Edit application</a>',
            source,
        )
        self.assertNotIn(
            '#application-{{ selected_application.id }}">Add job description</a>',
            source,
        )

    def test_hash_reveal_script_opens_target_details(self) -> None:
        source = APP_SHELL_JS.read_text(encoding="utf-8")
        self.assertIn("const revealHashTarget = () =>", source)
        self.assertIn("let parentDetails = target.closest('details');", source)
        self.assertIn("parentDetails.open = true;", source)
        self.assertIn("window.addEventListener('hashchange', revealHashTarget);", source)
        self.assertIn("revealHashTarget();", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
