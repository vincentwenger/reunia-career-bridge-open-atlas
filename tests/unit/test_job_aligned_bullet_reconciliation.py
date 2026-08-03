from __future__ import annotations

import unittest

from products.resume_taylor.resume_tailor.deterministic_fixes import _clean_bullets
from products.resume_taylor.resume_tailor.models import (
    BulletProposal,
    CandidateProfile,
    ContactInfo,
    EvidenceMatch,
    Experience,
    JobAnalysis,
    JobRequirement,
    ResumeBullet,
    SkillSet,
    TailoringProposal,
    VerifiedSkills,
)
from products.resume_taylor.resume_tailor.proposal_integrity import (
    BULLET_MAPPING_FALLBACK_NOTE,
    is_auto_reconciled_inclusion,
    is_missing_selection_decision,
    repair_missing_bullet_proposals,
    selection_consistency_warnings,
)


class JobAlignedBulletReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        source_bullets = [
            ResumeBullet(
                id=f"EXP-001-B{index:02d}",
                text=(
                    f"Delivered verified accomplishment {index} for banking clients "
                    f"using structured engineering practices and measurable results {index}."
                ),
            )
            for index in range(1, 10)
        ]
        source_bullets[6] = ResumeBullet(
            id="EXP-001-B07",
            text=(
                "Delivered 10 complex software projects using Agile Scrum by breaking "
                "work into sprints and collaborating cross-functionally."
            ),
        )
        source_bullets[8] = ResumeBullet(
            id="EXP-001-B09",
            text=(
                "Resolved 1000 client-reported technical issues for 20 clients by "
                "managing structured JIRA workflows and troubleshooting production issues."
            ),
        )
        self.profile = CandidateProfile(
            name="Candidate",
            contact=ContactInfo(
                location="Portland, OR",
                phone="",
                email="candidate@example.com",
            ),
            current_summary="Software engineer supporting banking clients.",
            skills=VerifiedSkills(
                hard_skills=["Issue resolution"],
                tools_software=["JIRA"],
            ),
            education=[],
            experiences=[
                Experience(
                    id="EXP-001",
                    employer="Example Bank Technology",
                    location="Portland, OR",
                    dates="2020-present",
                    title="Lead Software Engineer",
                    bullets=source_bullets,
                )
            ],
        )
        self.analysis = JobAnalysis(
            target_title="Production Support Engineer",
            requirements=[
                JobRequirement(
                    id="R9",
                    category="responsibility",
                    priority="critical",
                    requirement="Issue resolution",
                ),
                JobRequirement(
                    id="R11",
                    category="responsibility",
                    priority="important",
                    requirement="Troubleshoot production issues",
                ),
            ],
        )

    def proposal(
        self,
        bullets: list[BulletProposal],
        evidence_matches: list[EvidenceMatch] | None = None,
    ) -> TailoringProposal:
        return TailoringProposal(
            professional_summary=(
                "Software engineer with verified experience supporting banking clients "
                "and resolving production issues. Brings structured delivery practices, "
                "technical troubleshooting, and cross-functional collaboration. Uses "
                "evidence-backed accomplishments to support reliable systems and client "
                "outcomes. Focuses on clear communication, issue ownership, and practical "
                "problem solving."
            ),
            skills=SkillSet(
                hard_skills=["Issue resolution"],
                tools_software=["JIRA"],
            ),
            bullet_proposals=bullets,
            evidence_matches=evidence_matches or [],
        )

    def test_missing_mapping_record_is_restored_without_user_facing_error(self) -> None:
        proposal = self.proposal(
            [
                BulletProposal(
                    source_bullet_id=bullet.id,
                    include=True,
                    proposed_text=bullet.text,
                    matched_requirement_ids=[],
                    evidence_note=f"Supported by {bullet.id}.",
                )
                for bullet in self.profile.experiences[0].bullets
                if bullet.id != "EXP-001-B07"
            ]
        )

        repaired = repair_missing_bullet_proposals(self.profile, proposal)
        restored = next(
            item
            for item in repaired.bullet_proposals
            if item.source_bullet_id == "EXP-001-B07"
        )

        self.assertFalse(restored.include)
        self.assertFalse(is_missing_selection_decision(restored))
        self.assertEqual(restored.evidence_note, BULLET_MAPPING_FALLBACK_NOTE)

    def test_matched_bullet_displaces_unmatched_included_bullet(self) -> None:
        bullets: list[BulletProposal] = []
        matched_ids = {
            "EXP-001-B01",
            "EXP-001-B02",
            "EXP-001-B03",
            "EXP-001-B04",
            "EXP-001-B05",
            "EXP-001-B09",
        }
        initially_included = {
            "EXP-001-B01",
            "EXP-001-B02",
            "EXP-001-B03",
            "EXP-001-B04",
            "EXP-001-B05",
            "EXP-001-B07",
        }
        for source in self.profile.experiences[0].bullets:
            requirement_ids = []
            if source.id in matched_ids:
                requirement_ids = ["R9"]
            if source.id == "EXP-001-B09":
                requirement_ids = ["R9", "R11"]
            bullets.append(
                BulletProposal(
                    source_bullet_id=source.id,
                    include=source.id in initially_included,
                    proposed_text=source.text,
                    matched_requirement_ids=requirement_ids,
                    evidence_note=f"Directly supported by source bullet {source.id}.",
                )
            )

        reconciled = _clean_bullets(
            self.profile,
            self.analysis,
            self.proposal(bullets),
        )
        lookup = {item.source_bullet_id: item for item in reconciled}

        self.assertTrue(lookup["EXP-001-B09"].include)
        self.assertFalse(lookup["EXP-001-B07"].include)
        self.assertEqual(sum(1 for item in reconciled if item.include), 6)

    def test_missing_matched_b09_is_automatically_reconciled_and_included(self) -> None:
        initially_included = {
            "EXP-001-B01",
            "EXP-001-B02",
            "EXP-001-B03",
            "EXP-001-B04",
            "EXP-001-B05",
            "EXP-001-B07",
        }
        bullets = [
            BulletProposal(
                source_bullet_id=source.id,
                include=source.id in initially_included,
                proposed_text=source.text,
                matched_requirement_ids=(
                    ["R9"]
                    if source.id
                    in {
                        "EXP-001-B01",
                        "EXP-001-B02",
                        "EXP-001-B03",
                        "EXP-001-B04",
                        "EXP-001-B05",
                    }
                    else []
                ),
                evidence_note=f"Directly supported by source bullet {source.id}.",
            )
            for source in self.profile.experiences[0].bullets
            if source.id != "EXP-001-B09"
        ]
        evidence_matches = [
            EvidenceMatch(
                requirement_id="R9",
                status="supported",
                evidence_ids=["EXP-001-B09"],
                rationale="The verified bullet documents issue resolution.",
            ),
            EvidenceMatch(
                requirement_id="R11",
                status="supported",
                evidence_ids=["EXP-001-B09"],
                rationale="The verified bullet documents production troubleshooting.",
            ),
        ]

        reconciled = _clean_bullets(
            self.profile,
            self.analysis,
            self.proposal(bullets, evidence_matches),
        )
        lookup = {item.source_bullet_id: item for item in reconciled}

        self.assertTrue(lookup["EXP-001-B09"].include)
        self.assertEqual(lookup["EXP-001-B09"].matched_requirement_ids, ["R9", "R11"])
        self.assertTrue(is_auto_reconciled_inclusion(lookup["EXP-001-B09"]))
        self.assertFalse(is_missing_selection_decision(lookup["EXP-001-B09"]))
        self.assertFalse(lookup["EXP-001-B07"].include)
        self.assertEqual(sum(1 for item in reconciled if item.include), 6)


    def test_model_include_flags_do_not_control_selection(self) -> None:
        def build(include_value: bool) -> list[BulletProposal]:
            return [
                BulletProposal(
                    source_bullet_id=source.id,
                    include=include_value,
                    proposed_text=source.text,
                    matched_requirement_ids=(
                        ["R9", "R11"] if source.id == "EXP-001-B09" else []
                    ),
                    evidence_note=f"Supported by {source.id}.",
                )
                for source in self.profile.experiences[0].bullets
            ]

        selected_from_true = {
            item.source_bullet_id
            for item in _clean_bullets(
                self.profile, self.analysis, self.proposal(build(True))
            )
            if item.include
        }
        selected_from_false = {
            item.source_bullet_id
            for item in _clean_bullets(
                self.profile, self.analysis, self.proposal(build(False))
            )
            if item.include
        }

        self.assertEqual(selected_from_true, selected_from_false)
        self.assertIn("EXP-001-B09", selected_from_true)

    def test_selector_uses_natural_outcome_labels(self) -> None:
        bullets = [
            BulletProposal(
                source_bullet_id=source.id,
                include=False,
                proposed_text=source.text,
                matched_requirement_ids=(
                    ["R9", "R11"] if source.id == "EXP-001-B09" else []
                ),
                evidence_note=f"Supported by {source.id}.",
            )
            for source in self.profile.experiences[0].bullets
        ]

        result = _clean_bullets(self.profile, self.analysis, self.proposal(bullets))
        lookup = {item.source_bullet_id: item for item in result}

        self.assertTrue(
            lookup["EXP-001-B09"].evidence_note.startswith(
                "Included — strong job match."
            )
        )
        self.assertNotIn("automatic reconciliation", lookup["EXP-001-B09"].evidence_note)
        self.assertNotIn("Selection decision missing", lookup["EXP-001-B09"].evidence_note)


    def test_excluded_bullet_records_higher_ranked_selected_alternatives(self) -> None:
        profile = self.profile.model_copy(deep=True)
        profile.experiences[0].bullets[0] = ResumeBullet(
            id="EXP-001-B01",
            text="Led implementation.",
        )
        bullets = []
        for source in profile.experiences[0].bullets:
            requirement_ids = ["R9"]
            if source.id == "EXP-001-B09":
                requirement_ids = ["R9", "R11"]
            bullets.append(
                BulletProposal(
                    source_bullet_id=source.id,
                    include=False,
                    proposed_text=source.text,
                    matched_requirement_ids=requirement_ids,
                    evidence_note=f"Supported by {source.id}.",
                )
            )

        reconciled = _clean_bullets(
            profile,
            self.analysis,
            self.proposal(bullets),
        )
        lookup = {item.source_bullet_id: item for item in reconciled}
        excluded = lookup["EXP-001-B05"]

        self.assertFalse(excluded.include)
        self.assertTrue(excluded.selected_instead_ids)
        selected_id = excluded.selected_instead_ids[0]
        self.assertTrue(lookup[selected_id].include)
        self.assertIn(selected_id, excluded.selection_comparison_reasons)
        self.assertTrue(excluded.selection_comparison_reasons[selected_id])
        self.assertIn(
            "higher-ranked related accomplishments are identified below",
            excluded.evidence_note.casefold(),
        )

    def test_warns_when_zero_match_is_included_over_matched_evidence(self) -> None:
        bullets = [
            BulletProposal(
                source_bullet_id=source.id,
                include=source.id == "EXP-001-B07",
                proposed_text=source.text,
                matched_requirement_ids=(
                    ["R9", "R11"] if source.id == "EXP-001-B09" else []
                ),
                evidence_note=f"Directly supported by source bullet {source.id}.",
            )
            for source in self.profile.experiences[0].bullets
        ]

        warnings = selection_consistency_warnings(
            self.profile,
            self.analysis,
            self.proposal(bullets),
        )

        conflict = next(
            warning
            for warning in warnings
            if warning["code"] == "zero_match_displaces_matched"
        )
        self.assertIn("EXP-001-B07", conflict["detail"])
        self.assertIn("EXP-001-B09", conflict["detail"])
        self.assertIn("R9: Issue resolution", conflict["detail"])
        self.assertIn("R11: Troubleshoot production issues", conflict["detail"])


if __name__ == "__main__":
    unittest.main()
