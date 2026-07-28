from __future__ import annotations

import unittest
from pathlib import Path

from career_bridge.presentation.navigation import (
    career_navigation,
    validate_navigation_model_alignment,
)


class CareerNavigationTests(unittest.TestCase):
    def test_navigation_matches_requested_order(self) -> None:
        sections = career_navigation()
        self.assertEqual([item.order for item in sections], list(range(1, 9)))
        self.assertEqual(
            [item.label for item in sections],
            [
                "Career Profile",
                "Application Builder",
                "Interview Preparation",
                "Mock Interview",
                "Interview Review",
                "Career Action Plan",
                "Progress",
                "Help & Support",
            ],
        )

    def test_navigation_relationships_exist_on_job_application(self) -> None:
        validate_navigation_model_alignment()

    def test_reunia_template_follows_the_canonical_navigation(self) -> None:
        root = Path(__file__).resolve().parents[2]
        template = (root / "products/reunia/templates/navbar.html").read_text(encoding="utf-8")
        positions = [
            template.index(f'data-career-section="{section.key}"')
            for section in career_navigation()
        ]
        self.assertEqual(positions, sorted(positions))

    def test_help_is_not_owned_by_job_application(self) -> None:
        help_section = career_navigation()[-1]
        self.assertEqual(help_section.key, "help_support")
        self.assertEqual(help_section.aggregate_fields, ())


if __name__ == "__main__":
    unittest.main()
