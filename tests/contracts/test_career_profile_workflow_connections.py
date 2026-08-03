from __future__ import annotations

import unittest
from pathlib import Path

from career_bridge.profile_context import (
    ReusableCareerProfile,
    text_not_already_in_profile,
    values_not_already_in_profile,
)
from job_discovery.ranking import CandidateJobProfile
from products.resume_taylor.resume_tailor.interview_preparation import (
    VerifiedEvidenceBundle,
    build_interview_preparation_prompt,
    job_description_fingerprint,
)
from products.resume_taylor.resume_tailor.models import (
    CandidateProfile,
    ContactInfo,
    NewcomerCareerProfile,
    VerifiedSkills,
)
from products.resume_taylor.resume_tailor.resume_findings import ResumeFindingsSnapshot


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = ROOT / "products/resume_taylor/app.py"
BUILDER_TEMPLATE = ROOT / "products/resume_taylor/templates/application_builder/index.html"
CAREER_TRANSLATION_TEMPLATE = ROOT / "products/resume_taylor/templates/application_builder/career_translation.html"
MOCK_SOURCE = ROOT / "products/reunia/meeting_assistant/services/mock_interview_service.py"
LIVE_QA_SOURCE = ROOT / "products/reunia/meeting_assistant/services/live_qa_service.py"
KNOWLEDGE_SEARCH_SOURCE = ROOT / "products/reunia/meeting_assistant/services/knowledge_search_service.py"
PROMPTS_SOURCE = ROOT / "products/resume_taylor/resume_tailor/prompts.py"


class CareerProfileWorkflowConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = ReusableCareerProfile.from_mapping(
            {
                "professional_headline": "Financial technology engineering leader",
                "current_role": "Lead Software Engineer",
                "preferred_roles": "Platform Engineer, Data Platform Engineer",
                "current_location": "Portland, Oregon",
                "relocation_preferences": "Dallas, Texas; Charlotte, North Carolina",
                "work_preferences": "Remote or hybrid",
                "industries": "Banking, regulatory reporting",
                "core_skills": "SQL, Python, AWS",
                "key_accomplishments": "Delivered regulatory reporting systems\nReduced release frequency by 40%",
                "countries_worked": "France, Singapore, United States",
                "languages": "French, English",
                "target_country": "United States",
                "target_country_experience": "Eight years in U.S. financial technology",
                "international_credentials": "French engineering degree",
                "certifications": "Machine Learning and AI certificate",
                "titles_needing_translation": "Ingénieur d'études",
                "career_transition": "Regulatory engineering toward AI-enabled data platforms",
                "work_authorization": "Authorized to work without sponsorship",
                "career_goals": "Lead evidence-grounded AI and data products",
                "constraints": "Avoid weekly travel above 25%",
            }
        )

    def test_profile_preserves_multiline_items_and_location_commas(self) -> None:
        self.assertEqual(
            (
                "Delivered regulatory reporting systems",
                "Reduced release frequency by 40%",
            ),
            self.profile.accomplishment_values,
        )
        self.assertEqual(
            (
                "Portland, Oregon",
                "Dallas, Texas",
                "Charlotte, North Carolina",
            ),
            self.profile.preferred_locations,
        )

    def test_disabled_profile_does_not_feed_workflows(self) -> None:
        disabled = ReusableCareerProfile.from_mapping(
            {
                "enabled": False,
                "current_role": "Should not be used",
                "core_skills": "Should not be used",
            }
        )
        self.assertFalse(disabled.has_context())
        self.assertEqual({}, disabled.as_prompt_dict())
        self.assertEqual((), disabled.target_titles)
        self.assertEqual((), disabled.skill_values)

    def test_profile_maps_to_resume_and_career_translation_context(self) -> None:
        background = NewcomerCareerProfile(**self.profile.newcomer_payload())
        self.assertEqual("Lead Software Engineer", background.current_role)
        self.assertIn("Platform Engineer", background.preferred_roles)
        self.assertIn("SQL", background.core_skills)
        self.assertIn("France", background.countries_worked)
        self.assertEqual("United States", background.target_country)
        self.assertIn("French engineering degree", background.international_credentials)
        self.assertEqual(self.profile.fingerprint, background.career_profile_fingerprint)
        self.assertTrue(background.has_context())

    def test_application_context_only_keeps_values_missing_from_profile(self) -> None:
        self.assertEqual(
            ["Singapore", "Insurance"],
            values_not_already_in_profile(
                ["France", "Singapore", "Banking", "Insurance"],
                ["France", "Banking"],
            ),
        )
        self.assertEqual(
            "",
            text_not_already_in_profile(
                "Eight years in U.S. financial technology",
                "Eight years in U.S. financial technology",
            ),
        )
        self.assertEqual(
            "Additional U.S. banking context",
            text_not_already_in_profile(
                "Additional U.S. banking context",
                "Eight years in U.S. financial technology",
            ),
        )

    def test_career_translation_is_reusable_and_does_not_repeat_profile_fields(self) -> None:
        source = CAREER_TRANSLATION_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Career Foundation · One-time setup", source)
        self.assertIn("A job description is not needed here", source)
        self.assertNotIn("Connected Career Profile", source)
        self.assertNotIn("Reusable career context", source)
        self.assertNotIn("Update Career Profile", source)
        self.assertIn("Target country from Career Profile", source)
        self.assertIn("Change in Career Profile", source)
        self.assertIn("reusable_career_profile.target_country", source)
        self.assertNotIn('name="target_country"', source)
        self.assertNotIn('id="foundation-target-country"', source)
        self.assertNotIn('name="job_description"', source)
        self.assertNotIn('id="job-description"', source)

        builder_source = BUILDER_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("application-specific exceptions", builder_source)
        self.assertIn("Target country for this application", builder_source)
        self.assertIn('<select id="target-country" name="target_country" data-target-country>', builder_source)
        self.assertIn("career_background.target_country|trim or 'United States'", builder_source)
        self.assertIn("{% for country in country_options %}", builder_source)
        self.assertIn("selected_target_country|trim|lower", builder_source)
        self.assertNotIn('<input id="target-country"', builder_source)
        self.assertIn("career_background_additions.countries_worked", builder_source)
        self.assertNotIn(">Countries where you worked<", builder_source)

    def test_job_discovery_uses_preferences_without_unverified_evidence(self) -> None:
        resume = CandidateProfile(
            name="Candidate",
            contact=ContactInfo(location="", phone="", email="candidate@example.test"),
            current_summary="Built documented banking software.",
            skills=VerifiedSkills(hard_skills=["Oracle SQL"]),
            education=[],
            experiences=[],
        )
        background = NewcomerCareerProfile(**self.profile.newcomer_payload())
        discovery = CandidateJobProfile.from_resume_workflow(resume, background)

        self.assertIn("Platform Engineer", discovery.target_titles)
        self.assertNotIn("Python", discovery.verified_skills)
        self.assertEqual(("Portland, Oregon",), discovery.preferred_locations)
        self.assertFalse(
            any(item.verification_status == "candidate_profile" for item in discovery.evidence_references)
        )

    def test_interview_preparation_uses_profile_as_context_not_evidence(self) -> None:
        prompt = build_interview_preparation_prompt(
            company="Example Bank",
            role="Platform Engineer",
            interview_audience="Hiring manager",
            job_description="Build reliable data platforms.",
            evidence=VerifiedEvidenceBundle((), "No verified evidence", "evidence-fingerprint"),
            resume_findings=ResumeFindingsSnapshot(captured_at="2026-07-31T00:00:00+00:00", source_stage="test"),
            career_profile_context=self.profile.as_prompt_dict(),
        )
        self.assertIn("REUSABLE CAREER PROFILE — CONTEXT ONLY", prompt)
        self.assertIn("Financial technology engineering leader", prompt)
        self.assertIn("It is not verified evidence", prompt)
        self.assertNotEqual(
            job_description_fingerprint("Build reliable data platforms."),
            job_description_fingerprint(
                "Build reliable data platforms.",
                career_profile_fingerprint=self.profile.fingerprint,
            ),
        )

    def test_profile_is_never_labeled_as_confirmed_evidence_in_ai_prompts(self) -> None:
        mock_source = MOCK_SOURCE.read_text(encoding="utf-8")
        live_qa_source = LIVE_QA_SOURCE.read_text(encoding="utf-8")
        knowledge_source = KNOWLEDGE_SEARCH_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            "Role context, reusable Career Profile context, and confirmed candidate evidence",
            mock_source,
        )
        self.assertNotIn("Role and verified candidate context", mock_source)
        self.assertIn("Reusable Career Profile context is self-entered", live_qa_source)
        self.assertIn("Career Profile context (unverified", knowledge_source)


    def test_all_five_workflows_use_the_shared_adapter(self) -> None:
        app_source = APP_SOURCE.read_text(encoding="utf-8")
        mock_source = MOCK_SOURCE.read_text(encoding="utf-8")
        prompts = PROMPTS_SOURCE.read_text(encoding="utf-8")

        self.assertIn("_discovery_search_preferences", app_source)
        self.assertIn("CandidateJobProfile.from_resume_workflow", app_source)
        self.assertIn("_career_background_with_profile", app_source)
        self.assertIn("_effective_career_background", app_source)
        self.assertNotIn(
            "g.workflow_state.career_background = _career_background_with_profile",
            app_source,
        )
        self.assertIn("career_profile_context=reusable_profile.as_prompt_dict()", app_source)
        self.assertIn("ReusableCareerProfile.from_mapping", mock_source)
        self.assertIn("REUSABLE CAREER PROFILE AND INTERNATIONAL BACKGROUND", prompts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
