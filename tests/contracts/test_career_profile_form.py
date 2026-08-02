from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "products/reunia/templates/knowledge.html"
JAVASCRIPT = ROOT / "products/reunia/static/js/pages/knowledge.js"
USER_SERVICE = ROOT / "products/reunia/meeting_assistant/services/user_service.py"
ROUTES = ROOT / "products/reunia/meeting_assistant/blueprints/knowledge/routes.py"


class CareerProfileFormContractTests(unittest.TestCase):
    def test_form_contains_reusable_professional_and_international_fields(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        for element_id in (
            "profileProfessionalHeadline",
            "profileCurrentRole",
            "profileYearsExperience",
            "profileCurrentLocation",
            "profilePreferredRoles",
            "profileIndustries",
            "profileCoreSkills",
            "profileKeyAccomplishments",
            "profileCountriesWorked",
            "profileLanguages",
            "profileTargetCountry",
            "profileTargetCountryExperience",
            "profileInternationalCredentials",
            "profileCertifications",
            "profileTitlesNeedingTranslation",
            "profileCareerTransition",
            "profileWorkPreferences",
            "profileRelocationPreferences",
            "profileWorkAuthorization",
            "profileCareerGoals",
            "profileConstraints",
        ):
            self.assertIn(f'id="{element_id}"', source)

        for heading in (
            "Professional identity",
            "Skills and career evidence",
            "International career background",
            "Career preferences and constraints",
        ):
            self.assertIn(heading, source)

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

    def test_backend_persists_and_renders_new_fields(self) -> None:
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

    def test_removed_ai_coaching_helpers_do_not_block_profile_initialization(self) -> None:
        source = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("updateResponsePreferencesSummary", source)
        self.assertNotIn("setResponsePreferencesExpanded", source)
        self.assertIn("markDefaultContextSaved(context)", source)
        self.assertIn("updateSaveContextButton()", source)

    def test_frontend_reads_writes_and_previews_new_fields(self) -> None:
        source = JAVASCRIPT.read_text(encoding="utf-8")
        for field in (
            "professional_headline",
            "current_role",
            "preferred_roles",
            "industries",
            "core_skills",
            "key_accomplishments",
            "countries_worked",
            "languages",
            "target_country",
            "international_credentials",
            "titles_needing_translation",
            "career_transition",
            "work_preferences",
            "career_goals",
            "constraints",
        ):
            self.assertIn(field, source)
        self.assertIn("const existing = normalizeContext(state.context", source)
        self.assertIn("...existing", source)


if __name__ == "__main__":
    unittest.main()
