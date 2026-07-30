"""Offline regression tests for diverse international Career Bridge profiles.

The fixtures intentionally avoid OpenAI calls. They exercise the deterministic
translation, evidence-protection, fit, validation, and interview-grounding layers
that must remain stable across different countries, credentials, career paths, and
resume structures.

Run from the repository root with:

    python -m unittest -v tests.regression.test_international_career_profiles
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from products.resume_taylor.resume_tailor.application_fit import (
    build_application_fit_assessment,
)
from products.resume_taylor.resume_tailor.career_translation import (
    ensure_career_translation_assessment,
)
from products.resume_taylor.resume_tailor.interview_preparation import (
    InterviewPreparationPoint,
    InterviewPreparationWorkspace,
    InterviewQuestionPlan,
    PersonalIntroductionOutline,
    build_verified_evidence_bundle,
    restrict_workspace_to_evidence,
)
from products.resume_taylor.resume_tailor.models import (
    CandidateProfile,
    CareerTranslationFinding,
    JobAnalysis,
    NewcomerCareerProfile,
    TailoringProposal,
)
from products.resume_taylor.resume_tailor.validation import validate_proposal
from products.resume_taylor.resume_tailor.web_state import WorkflowState

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "international_profiles"


class InternationalCareerProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenarios = []
        for path in sorted(FIXTURE_DIR.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            cls.scenarios.append(
                {
                    "path": path,
                    "scenario_id": raw["scenario_id"],
                    "label": raw["label"],
                    "profile": CandidateProfile.model_validate(raw["profile"]),
                    "background": NewcomerCareerProfile.model_validate(
                        raw["career_background"]
                    ),
                    "job_description": raw["job_description"],
                    "analysis": JobAnalysis.model_validate(raw["job_analysis"]),
                    "proposal": TailoringProposal.model_validate(raw["proposal"]),
                    "expectations": raw["expectations"],
                }
            )

    def test_suite_contains_all_required_international_scenarios(self) -> None:
        labels = {scenario["label"] for scenario in self.scenarios}
        self.assertGreaterEqual(len(self.scenarios), 6)
        self.assertEqual(
            labels,
            {
                "Internationally trained professional with unfamiliar credential",
                "Career changer with transferable experience",
                "Candidate with limited U.S. experience",
                "Candidate whose title translates poorly into U.S. terminology",
                "Candidate with unsupported target-job requirements",
                "Multilingual and differently structured resume content",
            },
        )

    def test_every_fixture_is_schema_valid_and_normalized(self) -> None:
        scenario_ids = [scenario["scenario_id"] for scenario in self.scenarios]
        self.assertEqual(len(scenario_ids), len(set(scenario_ids)))

        for scenario in self.scenarios:
            with self.subTest(scenario=scenario["scenario_id"]):
                profile = scenario["profile"]
                background = scenario["background"]
                analysis = scenario["analysis"]
                proposal = scenario["proposal"]

                self.assertTrue(profile.name)
                self.assertTrue(background.has_context())
                self.assertTrue(analysis.requirements)
                self.assertIn(analysis.target_title, scenario["job_description"])
                for requirement in analysis.requirements:
                    self.assertIn(requirement.requirement, scenario["job_description"])
                self.assertEqual(
                    {item.requirement_id for item in proposal.evidence_matches},
                    {item.id for item in analysis.requirements},
                )
                self.assertEqual(
                    len(background.languages),
                    len({item.casefold() for item in background.languages}),
                )

    def test_safe_fixture_proposals_pass_deterministic_validation(self) -> None:
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario["scenario_id"]):
                issues = validate_proposal(
                    scenario["profile"],
                    scenario["analysis"],
                    scenario["proposal"],
                )
                self.assertEqual(
                    issues,
                    [],
                    "Fixture should be a clean evidence-grounded baseline: "
                    + "; ".join(issue.issue for issue in issues),
                )

    def test_expected_translation_and_gap_findings_are_preserved(self) -> None:
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario["scenario_id"]):
                protected = ensure_career_translation_assessment(
                    scenario["profile"],
                    scenario["analysis"],
                    scenario["proposal"],
                    scenario["background"],
                )
                findings = protected.career_translation_assessment.findings
                finding_index = {
                    (item.category, item.source_text, item.disposition): item
                    for item in findings
                }

                for expected in scenario["expectations"]["expected_findings"]:
                    key = (
                        expected["category"],
                        expected["source_text"],
                        expected["disposition"],
                    )
                    self.assertIn(key, finding_index)

                transferable_count = sum(
                    item.category == "transferable_skill" for item in findings
                )
                self.assertGreaterEqual(
                    transferable_count,
                    scenario["expectations"]["expected_min_transferable_findings"],
                )

                valid_evidence_ids = {
                    "CANDIDATE-PROFILE",
                    *scenario["profile"].experience_lookup().keys(),
                    *scenario["profile"].bullet_lookup().keys(),
                    *(
                        f"EDUCATION-{index}"
                        for index, _ in enumerate(
                            scenario["profile"].education, start=1
                        )
                    ),
                    *(
                        item.id
                        for item in scenario["profile"].supplemental_evidence
                    ),
                }
                for finding in findings:
                    self.assertTrue(set(finding.evidence_ids) <= valid_evidence_ids)
                    if finding.disposition in {
                        "unsupported_claim",
                        "recommended_learning_or_future_action",
                    }:
                        self.assertEqual(finding.evidence_ids, [])

    def test_application_fit_flags_mandatory_unsupported_requirements(self) -> None:
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario["scenario_id"]):
                fit = build_application_fit_assessment(
                    scenario["analysis"],
                    scenario["proposal"],
                    scenario["profile"],
                    confirmation_complete=True,
                )
                expected_blockers = scenario["expectations"][
                    "expected_hard_blockers"
                ]
                self.assertEqual(fit.hard_blocker_count, expected_blockers)
                if expected_blockers:
                    self.assertLessEqual(fit.score, 45.0)
                    self.assertEqual(fit.recommendation_key, "low")
                    self.assertTrue(
                        any(
                            obstacle.startswith("Critical eligibility gap:")
                            for obstacle in fit.obstacles
                        )
                    )

    def test_background_context_never_becomes_verified_interview_evidence(self) -> None:
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario["scenario_id"]):
                state = WorkflowState(
                    source_profile=scenario["profile"],
                    confirmed_profile=scenario["profile"],
                    confirmation_complete=True,
                )
                bundle = build_verified_evidence_bundle(
                    state,
                    submitted_resume_bytes=None,
                )
                evidence_text = "\n".join(item.text for item in bundle.items)
                for forbidden in scenario["expectations"][
                    "forbidden_evidence_phrases"
                ]:
                    self.assertNotIn(forbidden, evidence_text)

                source_text = scenario["profile"].all_source_text()
                for token in scenario["expectations"]["unicode_tokens"]:
                    self.assertIn(token, source_text)
                    self.assertIn(token, evidence_text)

    def test_untraceable_model_translation_is_downgraded_to_clarification(self) -> None:
        scenario = self.scenarios[0]
        proposal = scenario["proposal"].model_copy(deep=True)
        proposal.career_translation_assessment.findings.append(
            CareerTranslationFinding(
                category="hidden_accomplishment",
                source_text="Managed a 40-person U.S. accounting department",
                translated_meaning="Large-team leadership",
                disposition="confirmed_experience",
                evidence_ids=["INVENTED-EVIDENCE-ID"],
                rationale="Model-generated assertion.",
                recommended_action="Use in the resume.",
            )
        )

        protected = ensure_career_translation_assessment(
            scenario["profile"],
            scenario["analysis"],
            proposal,
            scenario["background"],
        )
        finding = next(
            item
            for item in protected.career_translation_assessment.findings
            if item.source_text == "Managed a 40-person U.S. accounting department"
        )
        self.assertEqual(finding.disposition, "user_clarification_required")
        self.assertEqual(finding.evidence_ids, [])
        self.assertIn("could not trace", finding.rationale)

    def test_invented_translation_with_valid_evidence_id_is_downgraded(self) -> None:
        scenario = self.scenarios[0]
        proposal = scenario["proposal"].model_copy(deep=True)
        valid_id = scenario["profile"].experiences[0].bullets[0].id
        proposal.career_translation_assessment.findings.append(
            CareerTranslationFinding(
                category="job_title_translation",
                source_text="Managed a 60-person SAP S/4HANA transformation",
                translated_meaning="Global enterprise transformation leader",
                disposition="reasonable_rephrasing",
                evidence_ids=[valid_id],
                rationale="Attached to a real ID but not supported by its text.",
                recommended_action="Use this claim.",
            )
        )

        protected = ensure_career_translation_assessment(
            scenario["profile"],
            scenario["analysis"],
            proposal,
            scenario["background"],
        )
        finding = next(
            item
            for item in protected.career_translation_assessment.findings
            if item.source_text == "Managed a 60-person SAP S/4HANA transformation"
        )
        self.assertEqual(finding.disposition, "user_clarification_required")
        self.assertEqual(finding.evidence_ids, [])
        self.assertIn("not fully traceable", finding.rationale)

    def test_interview_workspace_removes_unverified_evidence_references(self) -> None:
        scenario = self.scenarios[0]
        state = WorkflowState(
            source_profile=scenario["profile"],
            confirmed_profile=scenario["profile"],
            confirmation_complete=True,
        )
        bundle = build_verified_evidence_bundle(state, submitted_resume_bytes=None)
        allowed_id = bundle.items[0].id
        workspace = InterviewPreparationWorkspace(
            role_summary="Prepare for the target role.",
            company_summary="Based only on the supplied job description.",
            expected_responsibilities=["Discuss the verified experience."],
            likely_technical_questions=[
                InterviewQuestionPlan(
                    question="How do you approach month-end close?",
                    why_likely="It is a core requirement.",
                    answer_focus="Use verified evidence.",
                    evidence_ids=[allowed_id, "invented-id"],
                )
            ],
            likely_behavioral_questions=[
                InterviewQuestionPlan(
                    question="Describe unsupported leadership experience.",
                    why_likely="Generated test item.",
                    answer_focus="This should be removed.",
                    evidence_ids=["invented-id"],
                )
            ],
            resume_challenge_areas=[],
            candidate_strengths=[
                InterviewPreparationPoint(
                    title="Verified strength",
                    detail="Use profile evidence.",
                    evidence_ids=[allowed_id],
                ),
                InterviewPreparationPoint(
                    title="Invented strength",
                    detail="Must not survive grounding.",
                    evidence_ids=["invented-id"],
                ),
            ],
            potential_experience_gaps=[],
            questions_to_ask=["How is success measured?"],
            personal_introduction=PersonalIntroductionOutline(
                opening="Opening",
                current_value="Current value",
                relevant_background="Relevant background",
                role_connection="Role connection",
                closing="Closing",
                evidence_ids=[allowed_id, "invented-id"],
            ),
        )

        grounded = restrict_workspace_to_evidence(
            workspace,
            [item.id for item in bundle.items],
        )
        self.assertEqual(
            grounded.likely_technical_questions[0].evidence_ids,
            [allowed_id],
        )
        self.assertEqual(grounded.likely_behavioral_questions, [])
        self.assertEqual(
            [item.title for item in grounded.candidate_strengths],
            ["Verified strength"],
        )
        self.assertEqual(grounded.personal_introduction.evidence_ids, [allowed_id])


if __name__ == "__main__":
    unittest.main(verbosity=2)
