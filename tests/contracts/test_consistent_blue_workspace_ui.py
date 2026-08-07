from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "products/reunia/templates/knowledge.html"
MOCK = ROOT / "products/reunia/templates/meeting-recorder.html"
REVIEW = ROOT / "products/reunia/templates/meeting-review.html"
ACTION = ROOT / "products/reunia/templates/action-center.html"
THEME = ROOT / "products/reunia/static/css/career-theme.css"
KNOWLEDGE_CSS = ROOT / "products/reunia/static/css/pages/knowledge.css"
MOCK_CSS = ROOT / "products/reunia/static/css/pages/meeting-recorder.css"
REVIEW_CSS = ROOT / "products/reunia/static/css/pages/meeting-review.css"
ACTION_CSS = ROOT / "products/reunia/static/css/pages/action-center.css"


class ConsistentBlueWorkspaceUIContractTests(unittest.TestCase):
    def test_career_profile_uses_progressive_disclosure_and_one_save_action(self) -> None:
        template = KNOWLEDGE.read_text(encoding="utf-8")
        self.assertEqual(3, template.count('class="workspace-card career-profile-section"'))
        self.assertIn('class="workspace-card career-profile-section" open', template)
        self.assertEqual(1, template.count('id="saveContextButton"'))
        self.assertNotIn('class="library-help-card"', template)
        self.assertIn('career-profile-source-notice', template)
        self.assertIn('career-profile-section-summary', KNOWLEDGE_CSS.read_text(encoding="utf-8"))

    def test_mock_interview_setup_is_compact_and_application_link_is_removed(self) -> None:
        template = MOCK.read_text(encoding="utf-8")
        self.assertIn("Set up a practice session", template)
        self.assertNotIn("Open Application Builder", template)
        css = MOCK_CSS.read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", css)
        self.assertIn("border-top: 4px solid var(--cb-color-primary", css)

    def test_interview_review_uses_short_tabs_and_compact_sidebar(self) -> None:
        template = REVIEW.read_text(encoding="utf-8")
        for label in (">Summary</button>", ">Scorecard</button>", ">Transcript</button>", ">Ask AI</button>"):
            self.assertIn(label, template)
        css = REVIEW_CSS.read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: 340px minmax(0, 1fr)", css)

    def test_action_plan_prioritizes_actions_over_secondary_overview(self) -> None:
        template = ACTION.read_text(encoding="utf-8")
        self.assertIn('<details class="action-application-overview"', template)
        self.assertIn("<h2>Actions</h2>", template)
        css = ACTION_CSS.read_text(encoding="utf-8")
        self.assertIn(".action-kpi-card em", css)
        self.assertIn("display: none", css)

    def test_authenticated_workspaces_use_blue_primary_actions_and_dashboard_header(self) -> None:
        css = THEME.read_text(encoding="utf-8")
        self.assertIn("background: linear-gradient(135deg, var(--cb-color-primary-dark), var(--cb-color-primary-bright))", css)
        self.assertIn('body[data-authenticated="true"] .home-header', css)
        self.assertNotIn('body[data-authenticated="true"] .button.primary,', css)


if __name__ == "__main__":
    unittest.main()
