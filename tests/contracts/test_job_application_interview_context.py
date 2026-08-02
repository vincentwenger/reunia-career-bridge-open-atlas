from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"
TEMPLATE = RESUME_TAYLOR_ROOT / "templates" / "application_builder" / "applications.html"
TRACKER = RESUME_TAYLOR_ROOT / "resume_tailor" / "application_tracker.py"
DYNAMO = RESUME_TAYLOR_ROOT / "resume_tailor" / "dynamodb_storage.py"
MOCK_INTERVIEW = ROOT / "products" / "reunia" / "meeting_assistant" / "services" / "mock_interview_service.py"


class JobApplicationInterviewContextTests(unittest.TestCase):
    def test_new_application_form_contains_only_essential_job_fields(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        creation_form = source.split('id="new-application"', 1)[1].split(
            '<section class="applications-list"', 1
        )[0]
        for field in ("company", "role", "job_url", "job_description"):
            self.assertIn(f'name="{field}"', creation_form)
        for deferred_field in (
            "status",
            "application_date",
            "interview_audience",
            "next_action",
            "upcoming_event_type",
            "upcoming_event_date",
            "notes",
        ):
            self.assertNotIn(f'name="{deferred_field}"', creation_form)
        self.assertIn("Provide a job posting link, the job description, or both.", creation_form)
        self.assertIn(">Create application</button>", creation_form)
        self.assertIn('name="start_builder" value="1"', creation_form)

    def test_application_edit_form_keeps_interview_audience(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn('id="new-interview-audience"', source)
        self.assertIn('name="interview_audience"', source)
        self.assertIn('for="interview-audience-{{ application.id }}"', source)
        self.assertNotIn("Default audience", source)

    def test_dynamodb_round_trips_interview_audience(self) -> None:
        from tests.helpers.dynamodb_application_store import make_application_store

        store = make_application_store()
        created = store.create(
            "owner",
            company="Example",
            role="Engineer",
            job_url="https://example.com/jobs/1",
            interview_audience="Hiring manager and technical panel",
            job_description="Build reliable systems.",
        )
        self.assertEqual(
            created.interview_audience,
            "Hiring manager and technical panel",
        )
        updated = store.update(
            "owner",
            created.id,
            company=created.company,
            role=created.role,
            job_url=created.job_url,
            application_date=created.application_date,
            status=created.status,
            screening_received=False,
            interview_received=False,
            offer_received=False,
            notes="",
            next_follow_up_date="",
            interview_audience="Recruiter",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.interview_audience, "Recruiter")

    def test_dynamodb_and_mock_interview_include_application_audience(self) -> None:
        dynamo = DYNAMO.read_text(encoding="utf-8")
        mock = MOCK_INTERVIEW.read_text(encoding="utf-8")
        self.assertIn('"interview_audience": record.interview_audience', dynamo)
        self.assertIn('getattr(application, "interview_audience"', mock)
        self.assertIn('"interview_audience": interview_audience', mock)


if __name__ == "__main__":
    unittest.main(verbosity=2)
