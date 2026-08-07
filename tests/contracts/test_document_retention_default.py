"""Contracts for reusable Career Evidence Library document retention."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USER_SERVICE = (
    ROOT
    / "products"
    / "reunia"
    / "meeting_assistant"
    / "services"
    / "user_service.py"
)
SETTINGS_TEMPLATE = ROOT / "products" / "reunia" / "templates" / "settings.html"


class DocumentRetentionDefaultContractTests(unittest.TestCase):
    def test_new_accounts_keep_uploaded_documents_until_user_deletes_them(self) -> None:
        text = USER_SERVICE.read_text(encoding="utf-8")
        match = re.search(r'"documentRetentionDays"\s*:\s*(\d+)', text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "0")

    def test_settings_fallback_selects_keep_until_deleted(self) -> None:
        text = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        keep_option = (
            '<option value="0" {% if not settings or '
            'settings.documentRetentionDays == 0 %}selected{% endif %}>'
            'Keep until I delete them — recommended</option>'
        )
        seven_day_option = (
            '<option value="7" {% if settings and '
            'settings.documentRetentionDays == 7 %}selected{% endif %}>'
            'Delete after 7 days</option>'
        )
        self.assertIn(keep_option, text)
        self.assertIn(seven_day_option, text)
        self.assertNotIn(
            'not settings or settings.documentRetentionDays == 7',
            text,
        )

    def test_retention_copy_does_not_reference_retired_recorder_uploads(self) -> None:
        text = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn("Temporary recorder uploads", text)
        self.assertIn("applies only to newly uploaded documents", text)


if __name__ == "__main__":
    unittest.main()
