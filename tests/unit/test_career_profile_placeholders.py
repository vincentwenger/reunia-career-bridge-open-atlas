from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "products" / "reunia" / "templates" / "knowledge.html"
I18N_FR = ROOT / "products" / "reunia" / "static" / "js" / "i18n-fr.js"


class CareerProfilePlaceholderTests(unittest.TestCase):
    def test_career_profile_uses_fictional_non_personal_examples(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")

        expected_examples = (
            "Bilingual healthcare operations manager focused on patient access",
            "Minneapolis, Minnesota, United States",
            "Clinic Operations Manager, Patient Access Manager",
            "Healthcare services, community health, nonprofit organizations",
            "Philippines, United Arab Emirates, Canada",
        )
        for example in expected_examples:
            with self.subTest(example=example):
                self.assertIn(example, template)

        personal_examples = (
            "Software engineering leader specializing in financial regulatory platforms",
            "Portland, Oregon, United States",
            "Lead Software Engineer, Data Platform Engineer, IT Audit Manager",
            "Banking, financial technology, regulatory reporting",
            "France, Luxembourg, Singapore, United States",
            "8 years in U.S. financial technology",
            "Open to Texas, Ohio, or North Carolina",
        )
        for example in personal_examples:
            with self.subTest(example=example):
                self.assertNotIn(example, template)

    def test_french_translation_dictionary_matches_new_examples(self) -> None:
        translations = I18N_FR.read_text(encoding="utf-8")
        self.assertIn(
            "Example: Bilingual healthcare operations manager focused on patient access",
            translations,
        )
        self.assertIn("Example: Minneapolis, Minnesota, United States", translations)
        self.assertNotIn(
            "Example: Software engineering leader specializing in financial regulatory platforms",
            translations,
        )
        self.assertNotIn("Example: Portland, Oregon, United States", translations)


if __name__ == "__main__":
    unittest.main()
