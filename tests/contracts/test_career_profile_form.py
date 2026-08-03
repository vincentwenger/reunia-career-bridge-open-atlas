from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "products/reunia/templates/knowledge.html"
JAVASCRIPT = ROOT / "products/reunia/static/js/pages/knowledge.js"
USER_SERVICE = ROOT / "products/reunia/meeting_assistant/services/user_service.py"
ROUTES = ROOT / "products/reunia/meeting_assistant/blueprints/knowledge/routes.py"


class CareerProfileFormContractTests(unittest.TestCase):
    def test_form_contains_only_direction_context_and_preference_fields(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        for element_id in (
            "profileProfessionalHeadline",
            "profileCurrentLocation",
            "profilePreferredRoles",
            "profileIndustries",
            "profileCountriesWorked",
            "profileTargetCountry",
            "profileTargetCountryExperience",
            "profileTitlesNeedingTranslation",
            "profileCareerTransition",
            "profileWorkPreferences",
            "profileRelocationPreferences",
            "profileWorkAuthorization",
            "profileCareerGoals",
            "profileConstraints",
        ):
            self.assertIn(f'id="{element_id}"', source)

        for removed_resume_field in (
            "profileCurrentRole",
            "profileYearsExperience",
            "profileCoreSkills",
            "profileKeyAccomplishments",
            "profileLanguages",
            "profileInternationalCredentials",
            "profileCertifications",
        ):
            self.assertNotIn(f'id="{removed_resume_field}"', source)

        for heading in (
            "Career direction",
            "International and transition context",
            "Preferences and constraints",
        ):
            self.assertIn(heading, source)
        for removed_heading in (
            "Professional identity",
            "Skills and career evidence",
            "International career background",
            "Career preferences and constraints",
        ):
            self.assertNotIn(removed_heading, source)

        self.assertIn('<select id="profileTargetCountry" name="target_country">', source)
        self.assertIn("Select a country", source)
        self.assertIn("{% for country in country_options %}", source)
        self.assertNotIn('type="text" id="profileTargetCountry"', source)

    def test_resume_source_notice_links_to_extracted_baseline_information(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Resume facts are managed in Baseline Resume", source)
        self.assertIn("Review extracted resume information", source)
        self.assertIn(
            "{{ request.script_root }}/applications/career-translation#professional-summary",
            source,
        )
        for fact in (
            "Employment history",
            "official job titles",
            "professional summary",
            "skills",
            "languages",
            "education",
            "credentials",
            "certifications",
            "resume accomplishments",
        ):
            self.assertIn(fact, source)

    def test_application_specific_and_live_response_fields_are_not_in_profile_form(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        for removed_id in (
            "contextCompany",
            "contextReferenceLink",
            "contextType",
            "contextAudience",
            "contextAnswerStyle",
            "contextResponseMode",
            "contextAudioResponseInstructions",
            "contextClipboardResponseInstructions",
            "meetingContextObjective",
            "meetingContextParticipants",
            "meetingContextSpecialInstructions",
        ):
            self.assertNotIn(f'id="{removed_id}"', source)
        for removed_attribute in (
            "data-context-audience",
            "data-context-answer-style",
            "data-context-response-mode",
            "data-context-audio-response-instructions",
            "data-context-clipboard-response-instructions",
        ):
            self.assertNotIn(removed_attribute, source)

        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("contextAudience", javascript)
        self.assertNotIn("context_audience", javascript)

    def test_backend_keeps_legacy_resume_fields_for_older_saved_profiles(self) -> None:
        service_source = USER_SERVICE.read_text(encoding="utf-8")
        route_source = ROUTES.read_text(encoding="utf-8")
        for field in (
            "professional_headline",
            "current_role",
            "preferred_roles",
            "industries",
            "core_skills",
            "countries_worked",
            "target_country",
            "international_credentials",
            "titles_needing_translation",
            "career_transition",
            "work_preferences",
            "career_goals",
            "constraints",
        ):
            self.assertIn(f'"{field}"', service_source)
            self.assertIn(f'context["{field}"]', route_source)
        self.assertIn("COUNTRY_OPTIONS", route_source)
        self.assertIn("country_options=COUNTRY_OPTIONS", route_source)

    def test_removed_ai_coaching_helpers_do_not_block_profile_initialization(self) -> None:
        source = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("updateResponsePreferencesSummary", source)
        self.assertNotIn("setResponsePreferencesExpanded", source)
        self.assertIn("markDefaultContextSaved(context)", source)
        self.assertIn("updateSaveContextButton()", source)

    def test_frontend_preserves_hidden_legacy_values_but_reuses_only_visible_profile_fields(self) -> None:
        source = JAVASCRIPT.read_text(encoding="utf-8")
        for field in (
            "professional_headline",
            "current_location",
            "preferred_roles",
            "industries",
            "countries_worked",
            "target_country",
            "target_country_experience",
            "titles_needing_translation",
            "career_transition",
            "work_preferences",
            "relocation_preferences",
            "work_authorization",
            "career_goals",
            "constraints",
        ):
            self.assertIn(f"'{field}'", source)
        self.assertIn("const ACTIVE_CAREER_PROFILE_FIELDS", source)
        self.assertIn("const existing = normalizeContext(state.context", source)
        self.assertIn("...existing", source)
        for removed_form_lookup in (
            "document.getElementById('profileCurrentRole')?.value",
            "document.getElementById('profileYearsExperience')?.value",
            "document.getElementById('profileCoreSkills')?.value",
            "document.getElementById('profileKeyAccomplishments')?.value",
            "document.getElementById('profileLanguages')?.value",
            "document.getElementById('profileInternationalCredentials')?.value",
            "document.getElementById('profileCertifications')?.value",
        ):
            self.assertNotIn(removed_form_lookup, source)


if __name__ == "__main__":
    unittest.main()
