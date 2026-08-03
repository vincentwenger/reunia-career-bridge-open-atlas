from __future__ import annotations

import unittest
from io import BytesIO
from pathlib import Path

from docx import Document

from products.resume_taylor.resume_tailor.docx_export import export_resume_docx
from products.resume_taylor.resume_tailor.models import (
    ApprovedResume,
    CandidateProfile,
    ContactInfo,
    EducationItem,
    Experience,
    NewcomerCareerProfile,
    ResumeBullet,
    SkillSet,
    VerifiedSkills,
)
from products.resume_taylor.resume_tailor.prompts import build_proposal_prompt
from products.resume_taylor.resume_tailor.resume_language import (
    build_resume_translation_prompt,
    detect_text_language,
    resolve_resume_language,
    restore_translation_protected_fields,
    resume_format_headings,
    resume_labels,
    translated_profile_fingerprint,
    validate_translated_profile,
)
from products.resume_taylor.resume_tailor.models import JobAnalysis
from products.resume_taylor.resume_tailor.web_state import WorkflowState
from products.resume_taylor.resume_tailor.workflow_serialization import (
    workflow_state_from_json_bytes,
    workflow_state_json_bytes,
)


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "products/resume_taylor/data/resume_template_professional.docx"
BUILDER_TEMPLATE = ROOT / "products/resume_taylor/templates/application_builder/index.html"


def french_profile() -> CandidateProfile:
    return CandidateProfile(
        name="Marie Dupont",
        contact=ContactInfo(
            location="Paris, France",
            phone="+33 1 23 45 67 89",
            email="marie@example.test",
        ),
        current_summary="Ingénieure logicielle avec dix ans d'expérience dans les services financiers.",
        skills=VerifiedSkills(
            hard_skills=["Conception de bases de données"],
            soft_skills=["Communication avec les parties prenantes"],
            tools_software=["Oracle", "Python"],
            industry_knowledge=["Services financiers"],
            languages=["French", "English"],
        ),
        education=[
            EducationItem(
                credential="Diplôme d'ingénieur",
                institution="École Exemple",
                location="Paris, France",
                date="2012",
                detail="Spécialisation en systèmes d'information.",
            )
        ],
        experiences=[
            Experience(
                id="EXP-001",
                employer="Banque Exemple",
                location="Paris, France",
                dates="2018–2026",
                title="Ingénieure logicielle principale",
                bullets=[
                    ResumeBullet(
                        id="EXP-001-B01",
                        text="Développé 12 rapports réglementaires avec Oracle et Python.",
                    )
                ],
            )
        ],
    )


def english_translation() -> CandidateProfile:
    return CandidateProfile(
        name="Marie Dupont",
        contact=ContactInfo(
            location="Paris, France",
            phone="+33 1 23 45 67 89",
            email="marie@example.test",
        ),
        current_summary="Software engineer with ten years of experience in financial services.",
        skills=VerifiedSkills(
            hard_skills=["Database design"],
            soft_skills=["Stakeholder communication"],
            tools_software=["Oracle", "Python"],
            industry_knowledge=["Financial services"],
            languages=["French", "English"],
        ),
        education=[
            EducationItem(
                credential="Engineering degree",
                institution="École Exemple",
                location="Paris, France",
                date="2012",
                detail="Specialization in information systems.",
            )
        ],
        experiences=[
            Experience(
                id="EXP-001",
                employer="Banque Exemple",
                location="Paris, France",
                dates="2018–2026",
                title="Lead Software Engineer",
                bullets=[
                    ResumeBullet(
                        id="EXP-001-B01",
                        text="Developed 12 regulatory reports using Oracle and Python.",
                    )
                ],
            )
        ],
    )


class ResumeLanguageTranslationTests(unittest.TestCase):
    def test_target_country_selects_default_language(self) -> None:
        self.assertEqual("English", resolve_resume_language("United States").name)
        self.assertEqual("French", resolve_resume_language("France").name)
        self.assertEqual("German", resolve_resume_language("Germany").name)

    def test_multilingual_country_uses_job_description_language(self) -> None:
        choice = resolve_resume_language(
            "Canada",
            job_description=(
                "Nous recherchons une personne avec de l'expérience dans les services "
                "financiers et la gestion de projets avec les équipes techniques."
            ),
        )
        self.assertEqual("French", choice.name)
        self.assertEqual("job_description", choice.source)

    def test_unmapped_country_uses_job_description_language(self) -> None:
        choice = resolve_resume_language(
            "Côte d’Ivoire",
            job_description=(
                "Nous recherchons une personne expérimentée pour gérer les projets, "
                "travailler avec les équipes et développer les services financiers."
            ),
        )
        self.assertEqual("French", choice.name)
        self.assertEqual("job_description", choice.source)

    def test_user_override_has_priority(self) -> None:
        choice = resolve_resume_language(
            "United States",
            explicit_language="Spanish",
            job_description="English job description",
        )
        self.assertEqual("Spanish", choice.name)
        self.assertEqual("user_override", choice.source)

    def test_english_import_is_detected_as_matching_english_target(self) -> None:
        source_language = detect_text_language(english_translation().all_source_text())
        target = resolve_resume_language("United States")

        self.assertEqual("en", source_language)
        self.assertEqual(target.code, source_language)

    def test_same_language_bypass_occurs_before_translation_request(self) -> None:
        app_source = (ROOT / "products/resume_taylor/app.py").read_text(encoding="utf-8")
        helper_start = app_source.index("def _ensure_target_language_profile")
        helper_end = app_source.index("def _backfill_professional_contact_links", helper_start)
        helper_source = app_source[helper_start:helper_end]

        bypass_position = helper_source.index("if source_language and source_language == choice.code")
        translation_position = helper_source.index("ai.translate_candidate_profile")
        self.assertLess(bypass_position, translation_position)
        self.assertIn("state.source_profile = original.model_copy(deep=True)", helper_source)

    def test_translation_prompt_redacts_contact_values(self) -> None:
        profile = french_profile()
        profile.contact.linkedin_url = "https://linkedin.example/marie"
        prompt = build_resume_translation_prompt(
            profile,
            target_language="en",
            target_country="United States",
        )

        self.assertNotIn("marie@example.test", prompt)
        self.assertNotIn("+33 1 23 45 67 89", prompt)
        self.assertNotIn("https://linkedin.example/marie", prompt)
        self.assertIn('"email": ""', prompt)
        self.assertIn('"phone": ""', prompt)

    def test_translation_integrity_accepts_faithful_translation(self) -> None:
        self.assertEqual([], validate_translated_profile(french_profile(), english_translation(), "English"))

    def test_translation_integrity_accepts_localized_date_wording(self) -> None:
        source = french_profile()
        source.experiences[0].dates = "janvier 2018 – présent"
        source.education[0].date = "juin 2012"
        translated = english_translation()
        translated.experiences[0].dates = "January 2018 – Present"
        translated.education[0].date = "June 2012"

        self.assertEqual([], validate_translated_profile(source, translated, "English"))

    def test_translation_integrity_rejects_changed_date_facts(self) -> None:
        source = french_profile()
        source.experiences[0].dates = "janvier 2018 – présent"
        translated = english_translation()
        translated.experiences[0].dates = "January 2019 – Present"

        issues = validate_translated_profile(source, translated, "English")
        self.assertTrue(any("Dates for EXP-001" in issue for issue in issues))

    def test_translation_restores_protected_fields_without_discarding_translation(self) -> None:
        source = french_profile()
        translated = english_translation()
        translated.name = "Mary Dupont"
        translated.experiences[0].id = "EXPERIENCE-1"
        translated.experiences[0].employer = "Example Bank"
        translated.experiences[0].location = "Paris"
        translated.experiences[0].bullets[0].id = "BULLET-1"
        translated.education[0].institution = "Example School"
        translated.skills.tools_software = ["Oracle Database", "Python 3"]

        repaired = restore_translation_protected_fields(source, translated)

        self.assertEqual(source.name, repaired.name)
        self.assertEqual("Banque Exemple", repaired.experiences[0].employer)
        self.assertEqual("Paris, France", repaired.experiences[0].location)
        self.assertEqual("EXP-001", repaired.experiences[0].id)
        self.assertEqual("EXP-001-B01", repaired.experiences[0].bullets[0].id)
        self.assertEqual("École Exemple", repaired.education[0].institution)
        self.assertEqual(["Oracle", "Python"], repaired.skills.tools_software)
        self.assertEqual("Lead Software Engineer", repaired.experiences[0].title)
        self.assertIn("Developed 12 regulatory reports", repaired.experiences[0].bullets[0].text)
        self.assertEqual([], validate_translated_profile(source, repaired, "English"))

    def test_translation_integrity_rejects_mixed_language_sections(self) -> None:
        mixed = french_profile().model_copy(deep=True)
        mixed.current_summary = (
            "Software engineer with extensive experience in financial services and "
            "professional delivery for banking clients across multiple projects."
        )
        issues = validate_translated_profile(french_profile(), mixed, "French")
        self.assertTrue(any("Professional summary appears to be English" in issue for issue in issues))

    def test_translation_integrity_rejects_changed_evidence(self) -> None:
        changed = english_translation().model_copy(deep=True)
        changed.experiences[0].employer = "Different Bank"
        changed.experiences[0].bullets[0].text = "Developed 25 regulatory reports using Oracle and Python."
        issues = validate_translated_profile(french_profile(), changed, "English")
        self.assertTrue(any("Employer" in issue for issue in issues))
        self.assertTrue(any("Numeric evidence" in issue for issue in issues))

    def test_french_resume_labels_are_available(self) -> None:
        labels = resume_labels("French")
        self.assertEqual("Profil professionnel", labels["professional_summary"])
        self.assertEqual("Expérience professionnelle", labels["experience"])
        self.assertEqual("Formation et développement professionnel", labels["education"])
        self.assertEqual(
            "Compétences techniques",
            resume_format_headings("French", "technical")["skills"],
        )

    def test_docx_export_uses_target_language_headings(self) -> None:
        profile = english_translation()
        approved = ApprovedResume(
            target_title="Lead Software Engineer",
            professional_summary=profile.current_summary,
            skills=SkillSet(
                hard_skills=profile.skills.hard_skills,
                soft_skills=profile.skills.soft_skills,
                tools_software=profile.skills.tools_software,
                industry_knowledge=profile.skills.industry_knowledge,
            ),
            bullets_by_experience={
                "EXP-001": [profile.experiences[0].bullets[0].text]
            },
        )
        payload = export_resume_docx(
            TEMPLATE,
            profile,
            approved,
            resume_language="French",
        )
        text = "\n".join(paragraph.text for paragraph in Document(BytesIO(payload)).paragraphs)
        self.assertIn("Profil professionnel", text)
        self.assertIn("Expérience professionnelle", text)
        self.assertIn("Formation et développement professionnel", text)
        self.assertNotIn("Professional Summary", text)

    def test_proposal_prompt_requires_one_resume_language(self) -> None:
        background = NewcomerCareerProfile(
            target_country="United States",
            resume_language="English",
        )
        prompt = build_proposal_prompt(
            english_translation(),
            JobAnalysis(target_title="Lead Software Engineer", target_company="", requirements=[], ignored_boilerplate=[]),
            background,
        )
        self.assertIn("every candidate-facing resume field in English", prompt)
        self.assertIn("Do not mix prose from another language", prompt)

    def test_workflow_serialization_preserves_original_and_translated_profiles(self) -> None:
        state = WorkflowState(
            source_profile=english_translation(),
            original_source_profile=french_profile(),
            source_resume_language="fr",
            source_profile_language="en",
            source_profile_translation_fingerprint="translation-fingerprint",
            career_background=NewcomerCareerProfile(
                target_country="United States",
                resume_language="English",
            ),
        )
        restored = workflow_state_from_json_bytes(workflow_state_json_bytes(state))
        self.assertEqual("fr", restored.source_resume_language)
        self.assertEqual("en", restored.source_profile_language)
        self.assertEqual("translation-fingerprint", restored.source_profile_translation_fingerprint)
        self.assertIsNotNone(restored.original_source_profile)
        self.assertIn("Ingénieure", restored.original_source_profile.current_summary)
        self.assertIn("Software engineer", restored.source_profile.current_summary)

    def test_translation_fingerprint_changes_with_target_market(self) -> None:
        profile = french_profile()
        us_fingerprint = translated_profile_fingerprint(
            profile, "English", "United States"
        )
        uk_fingerprint = translated_profile_fingerprint(
            profile, "English", "United Kingdom"
        )
        self.assertNotEqual(us_fingerprint, uk_fingerprint)
        self.assertEqual(
            us_fingerprint,
            translated_profile_fingerprint(profile, "en", "  United States  "),
        )

    def test_career_translation_form_has_language_control_and_localized_resume_labels(self) -> None:
        for path in (BUILDER_TEMPLATE,):
            source = path.read_text(encoding="utf-8")
            self.assertIn('name="resume_language"', source)
            self.assertIn("Automatic — {{ resume_language_choice.name }}", source)
            self.assertIn("Career Bridge generates the Application Resume in the selected target language", source)
            self.assertIn("{{ resume_labels.professional_summary }}", source)
            self.assertIn("{{ resume_labels.experience }}", source)
            self.assertIn("{{ resume_labels.education }}", source)

        career_translation_source = (
            ROOT
            / "products/resume_taylor/templates/application_builder/career_translation.html"
        ).read_text(encoding="utf-8")
        self.assertIn("'Baseline Resume' if translation_ready else 'Imported Resume preview'", career_translation_source)
        self.assertIn("translation pending", career_translation_source)
        self.assertIn("Baseline Resume language", career_translation_source)
        self.assertIn("language of the uploaded resume is detected automatically", career_translation_source)
        self.assertIn("Imported resume language", career_translation_source)
        self.assertIn("No translation needed", career_translation_source)

        application_source = BUILDER_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Application Resume language", application_source)
        self.assertIn("not the language of the uploaded file", application_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
