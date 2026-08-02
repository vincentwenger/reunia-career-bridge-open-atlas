from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "applications.html"
)


class SimplifiedApplicationCreationTests(unittest.TestCase):
    def test_application_creation_uses_only_essential_fields(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        creation_form = source.split('id="new-application"', 1)[1].split(
            '<section class="applications-list"', 1
        )[0]
        for required_field in ("company", "role", "job_url", "job_description"):
            self.assertIn(f'name="{required_field}"', creation_form, TEMPLATE)
        for deferred_field in (
            "status",
            "application_date",
            "interview_audience",
            "next_action",
            "upcoming_event_type",
            "upcoming_event_date",
            "next_follow_up_date",
            "notes",
        ):
            self.assertNotIn(f'name="{deferred_field}"', creation_form, TEMPLATE)

    def test_creation_route_requires_a_posting_link_or_description(self) -> None:
        source = APP.read_text(encoding="utf-8")
        route = source.split('def create_application_record():', 1)[1].split(
            '@application_builder_bp.post("/applications/<application_id>/update")', 1
        )[0]
        self.assertIn("job_url = normalize_job_url(raw_job_url)", route)
        self.assertIn("if not job_url and not job_description:", route)
        self.assertIn("Add a job posting link or paste the job description.", route)
        self.assertIn('status="draft"', route)
        self.assertIn('interview_audience=""', route)
        self.assertIn('notes=""', route)


if __name__ == "__main__":
    unittest.main(verbosity=2)
