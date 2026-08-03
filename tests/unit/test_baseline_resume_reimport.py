from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "products" / "resume_taylor"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from resume_tailor.models import (
    CandidateProfile,
    ContactInfo,
    Experience,
    ResumeBullet,
    VerifiedSkills,
)
from resume_tailor.resume_import import (
    extract_explicit_resume_summary,
    sanitize_imported_candidate_profile,
)


class BaselineResumeReplacementImportTests(unittest.TestCase):
    def _profile(self) -> CandidateProfile:
        return CandidateProfile(
            name="Vincent Wenger",
            contact=ContactInfo(
                location="Portland, OR",
                phone="",
                email="vincent@example.com",
            ),
            current_summary=(
                "Software Engineer at Nasdaq. "
                "Led a global SAP S/4HANA transformation for 80 employees."
            ),
            skills=VerifiedSkills(
                hard_skills=["Python", "Kubernetes"],
                tools_software=["SQL"],
            ),
            education=[],
            experiences=[
                Experience(
                    id="EXP-001",
                    employer="Nasdaq",
                    location="Portland, OR",
                    dates="2013-2025",
                    title="Software Engineer",
                    bullets=[
                        ResumeBullet(
                            id="EXP-001-B01",
                            text="Developed regulatory reporting solutions using Python and SQL.",
                        ),
                        ResumeBullet(
                            id="EXP-001-B02",
                            text="Led a global SAP S/4HANA transformation for 80 employees.",
                        ),
                    ],
                )
            ],
        )

    def test_untraceable_extractor_content_is_omitted_not_import_blocking(self) -> None:
        source = """Vincent Wenger
Portland, OR
Software Engineer at Nasdaq, 2013-2025
Developed regulatory reporting solutions using Python and SQL.
"""
        cleaned, removed = sanitize_imported_candidate_profile(self._profile(), source)

        self.assertEqual(cleaned.current_summary, "Software Engineer at Nasdaq.")
        self.assertEqual(cleaned.skills.hard_skills, ["Python"])
        self.assertEqual(cleaned.skills.tools_software, ["SQL"])
        self.assertEqual(
            [bullet.id for bullet in cleaned.experiences[0].bullets],
            ["EXP-001-B01"],
        )
        self.assertIn("professional summary wording", removed)
        self.assertIn("hard skills", removed)
        self.assertIn("experience bullet", removed)

    def test_replacement_cleanup_never_reuses_supplemental_evidence(self) -> None:
        profile = self._profile()
        # Replacement uploads must rebuild source evidence from the new document;
        # application-specific confirmations are stored elsewhere and are not
        # copied into the new imported profile.
        from resume_tailor.models import SupplementalEvidence

        profile.supplemental_evidence = [
            SupplementalEvidence(
                id="EXP-CONF-01",
                statement="Previously confirmed answer",
            )
        ]
        cleaned, _removed = sanitize_imported_candidate_profile(
            profile,
            "Software Engineer at Nasdaq. Python SQL.",
        )
        self.assertEqual(cleaned.supplemental_evidence, [])

    def test_explicit_professional_summary_is_extracted_verbatim(self) -> None:
        source = """Vincent Wenger
Portland, Oregon

PROFESSIONAL SUMMARY
Software engineer with 15 years of experience building data and regulatory reporting systems.
Combines hands-on engineering with IT audit and machine-learning experience.

TECHNICAL SKILLS
Python, SQL, AWS
"""

        self.assertEqual(
            extract_explicit_resume_summary(source),
            "Software engineer with 15 years of experience building data and regulatory reporting systems.\n"
            "Combines hands-on engineering with IT audit and machine-learning experience.",
        )

    def test_inline_profile_heading_is_supported(self) -> None:
        source = """Vincent Wenger
Professional Profile: Software engineer focused on financial technology and regulatory reporting.
EXPERIENCE
Nasdaq
"""

        self.assertEqual(
            extract_explicit_resume_summary(source),
            "Software engineer focused on financial technology and regulatory reporting.",
        )

    def test_no_explicit_summary_allows_generated_fallback(self) -> None:
        source = """Vincent Wenger
EXPERIENCE
Software Engineer, Nasdaq
Developed regulatory reporting systems.
"""

        self.assertEqual(extract_explicit_resume_summary(source), "")

class BaselineResumeReplacementRouteContracts(unittest.TestCase):
    def test_upload_route_explicitly_treats_reimport_as_replacement(self) -> None:
        source = (
            ROOT / "products" / "resume_taylor" / "app.py"
        ).read_text(encoding="utf-8")

        self.assertIn("replacing_existing_baseline = bool(", source)
        self.assertIn("The new resume replaced the existing Baseline Resume", source)
        self.assertIn("current.original_source_profile = profile.model_copy(deep=True)", source)
        self.assertIn("current.source_profile = profile", source)
        self.assertIn("current.clear_results()", source)
        self.assertIn("_persist_workflow_state_now()", source)
        self.assertIn("document_store.delete(previous_source_key)", source)
        self.assertIn("baseline_revision=source_fingerprint[:12]", source)
        self.assertIn("last_resume_import_adjustments", source)

        upload_route_start = source.index(
            '@application_builder_bp.post("/profile/upload")'
        )
        upload_route_end = source.index(
            '@application_builder_bp.post("/profile/default")',
            upload_route_start,
        )
        upload_route = source[upload_route_start:upload_route_end]
        self.assertLess(
            upload_route.index("_persist_workflow_state_now()"),
            upload_route.index("document_store.delete(previous_source_key)"),
        )
        self.assertNotIn(
            "The imported Verified Resume Evidence introduced content that could not be traced",
            source,
        )

    def test_importer_preserves_an_explicit_uploaded_summary_after_cleanup(self) -> None:
        ai_source = (
            ROOT / "products" / "resume_taylor" / "resume_tailor" / "ai.py"
        ).read_text(encoding="utf-8")
        import_source = (
            ROOT
            / "products"
            / "resume_taylor"
            / "resume_tailor"
            / "resume_import.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "explicit_summary = extract_explicit_resume_summary(resume_text)",
            ai_source,
        )
        self.assertIn("profile.current_summary = explicit_summary", ai_source)
        self.assertGreater(
            ai_source.index("profile.current_summary = explicit_summary"),
            ai_source.index("sanitize_imported_candidate_profile"),
        )
        self.assertIn(
            "copy its wording verbatim into current_summary",
            import_source,
        )


if __name__ == "__main__":
    unittest.main()
