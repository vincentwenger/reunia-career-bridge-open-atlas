from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "career_translation.html"
)


class BaselineResumePlaceholderTests(unittest.TestCase):
    def test_general_target_role_uses_fictional_example(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            'placeholder="Community Health Program Coordinator"',
            template,
        )
        self.assertNotIn('placeholder="Data Platform Engineer"', template)
        self.assertNotIn('placeholder="Data Plaform Engineer"', template)


if __name__ == "__main__":
    unittest.main()
