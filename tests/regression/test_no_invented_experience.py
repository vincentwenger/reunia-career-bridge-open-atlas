"""Regression tests for evidence-grounding across Career Bridge outputs."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REUNIA_ROOT = ROOT / "products" / "reunia"
RESUME_ROOT = ROOT / "products" / "resume_taylor"
for path in (str(REUNIA_ROOT), str(RESUME_ROOT), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    import flask  # noqa: F401
except ImportError:
    RUNTIME_SERVICES_AVAILABLE = False
    _safe_interview_practice_action = None
    _grounded_mock_text = None
    _mock_interview_text_is_grounded = None
else:
    RUNTIME_SERVICES_AVAILABLE = True
    from meeting_assistant.services.action_service import _safe_interview_practice_action
    from meeting_assistant.services.mock_interview_service import (
        _grounded_mock_text,
        _mock_interview_text_is_grounded,
    )
from products.resume_taylor.resume_tailor.deterministic_fixes import (
    repair_unsupported_candidate_claims,
)
from products.resume_taylor.resume_tailor.interview_preparation import (
    InterviewPreparationPoint,
    InterviewPreparationWorkspace,
    PersonalIntroductionOutline,
    restrict_workspace_to_evidence,
)
from products.resume_taylor.resume_tailor.models import (
    CandidateProfile,
    JobAnalysis,
    TailoringProposal,
)
from products.resume_taylor.resume_tailor.resume_report import build_resume_report
from products.resume_taylor.resume_tailor.validation import validate_proposal

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "international_profiles"


class NoInventedExperienceRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenarios = []
        for path in sorted(FIXTURE_DIR.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            cls.scenarios.append(
                (
                    raw["scenario_id"],
                    CandidateProfile.model_validate(raw["profile"]),
                    JobAnalysis.model_validate(raw["job_analysis"]),
                    TailoringProposal.model_validate(raw["proposal"]),
                    raw["job_description"],
                )
            )

    def test_every_international_profile_rejects_invented_summary_claims(self) -> None:
        invented_sentence = (
            "Managed a 40-person U.S. department and implemented SAP S/4HANA "
            "to deliver a company-wide transformation."
        )
        for scenario_id, profile, analysis, proposal, _job_description in self.scenarios:
            with self.subTest(scenario=scenario_id):
                unsafe = proposal.model_copy(deep=True)
                unsafe.professional_summary = (
                    unsafe.professional_summary.rsplit(".", 1)[0]
                    + ". "
                    + invented_sentence
                )
                issues = validate_proposal(profile, analysis, unsafe)
                grounding = [
                    issue
                    for issue in issues
                    if issue.section == "Professional Summary"
                    and issue.issue.startswith("Generated candidate claim")
                ]
                self.assertTrue(grounding)
                self.assertTrue(
                    any(
                        marker in issue.issue
                        for issue in grounding
                        for marker in ("40", "SAP", "leadership", "scope")
                    )
                )

    def test_every_international_profile_rejects_invented_bullet_responsibilities(self) -> None:
        for scenario_id, profile, analysis, proposal, _job_description in self.scenarios:
            with self.subTest(scenario=scenario_id):
                unsafe = proposal.model_copy(deep=True)
                included = next(item for item in unsafe.bullet_proposals if item.include)
                included.proposed_text = (
                    "Negotiated enterprise vendor contracts and led a global transformation "
                    "using SAP S/4HANA."
                )
                issues = validate_proposal(profile, analysis, unsafe)
                grounding = [
                    issue
                    for issue in issues
                    if issue.source_id == included.source_bullet_id
                    and issue.issue.startswith("Generated candidate claim")
                ]
                self.assertTrue(grounding)

    def test_generation_repair_removes_unsupported_summary_and_bullet_claims(self) -> None:
        for scenario_id, profile, analysis, proposal, _job_description in self.scenarios:
            with self.subTest(scenario=scenario_id):
                unsafe = proposal.model_copy(deep=True)
                unsafe.professional_summary = (
                    "Managed a 40-person U.S. department and implemented SAP S/4HANA. "
                    "Led a global company-wide transformation for executive stakeholders. "
                    "Delivered certified enterprise results across international operations."
                )
                included = next(item for item in unsafe.bullet_proposals if item.include)
                source_text = profile.bullet_lookup()[included.source_bullet_id]
                included.proposed_text = (
                    "Negotiated enterprise vendor contracts and led a global SAP S/4HANA "
                    "transformation for 90 employees."
                )

                repaired = repair_unsupported_candidate_claims(profile, analysis, unsafe)

                self.assertFalse(
                    [
                        issue
                        for issue in validate_proposal(profile, analysis, repaired)
                        if issue.issue.startswith("Generated candidate claim")
                    ]
                )
                repaired_bullet = next(
                    item
                    for item in repaired.bullet_proposals
                    if item.source_bullet_id == included.source_bullet_id
                )
                self.assertEqual(repaired_bullet.proposed_text, source_text)
                self.assertNotIn("SAP S/4HANA", repaired.professional_summary)
                self.assertNotIn("40-person", repaired.professional_summary)

    def test_interview_preparation_rejects_valid_ids_attached_to_invented_claims(self) -> None:
        evidence_by_id = {
            "E1": "Built Python REST APIs and stored payment data in PostgreSQL."
        }
        workspace = InterviewPreparationWorkspace(
            role_summary="Prepare for the target role.",
            company_summary="Only the supplied posting is summarized.",
            expected_responsibilities=[],
            likely_technical_questions=[],
            likely_behavioral_questions=[],
            resume_challenge_areas=[],
            candidate_strengths=[
                InterviewPreparationPoint(
                    title="Global SAP leadership",
                    detail="Led a 50-person SAP S/4HANA transformation.",
                    evidence_ids=["E1"],
                ),
                InterviewPreparationPoint(
                    title="Verified backend delivery",
                    detail="Built Python REST APIs backed by PostgreSQL.",
                    evidence_ids=["E1"],
                ),
            ],
            potential_experience_gaps=[],
            questions_to_ask=[],
            personal_introduction=PersonalIntroductionOutline(
                opening="Built Python REST APIs.",
                current_value="Stored payment data in PostgreSQL.",
                relevant_background="Built Python REST APIs.",
                role_connection="Stored payment data in PostgreSQL.",
                closing="Built Python REST APIs.",
                evidence_ids=["E1"],
            ),
        )
        grounded = restrict_workspace_to_evidence(
            workspace,
            ["E1"],
            evidence_by_id=evidence_by_id,
        )
        self.assertEqual(
            [item.title for item in grounded.candidate_strengths],
            ["Verified backend delivery"],
        )

    @unittest.skipUnless(RUNTIME_SERVICES_AVAILABLE, "Flask runtime dependencies are not installed")
    def test_mock_interview_sample_answer_is_sanitized_before_scorecard_use(self) -> None:
        session = {
            "workspace_context": {
                "verified_candidate_evidence": [
                    {
                        "id": "E1",
                        "text": "Built Python REST APIs and stored payment data in PostgreSQL.",
                    }
                ]
            }
        }
        answer = "I built Python REST APIs and stored payment data in PostgreSQL."
        unsafe = "I led a 75-person SAP S/4HANA transformation across global operations."
        self.assertFalse(
            _mock_interview_text_is_grounded(
                unsafe,
                session=session,
                answer_text=answer,
                require_overlap=True,
            )
        )
        sanitized, accepted = _grounded_mock_text(
            unsafe,
            answer,
            session=session,
            answer_text=answer,
            require_overlap=True,
        )
        self.assertFalse(accepted)
        self.assertEqual(sanitized, answer)

    @unittest.skipUnless(RUNTIME_SERVICES_AVAILABLE, "Flask runtime dependencies are not installed")
    def test_legacy_interview_actions_do_not_republish_invented_experience(self) -> None:
        safe = _safe_interview_practice_action(
            "Highlight your 12 years leading SAP S/4HANA transformations at Google.",
            answer_text="I described how I built a Python API.",
            question_number="2",
        )
        self.assertNotIn("SAP", safe)
        self.assertNotIn("Google", safe)
        self.assertEqual(
            safe,
            "Review weak interview answer 2 using one confirmed example and a clear result",
        )

    def test_resume_report_contains_deterministic_grounding_result(self) -> None:
        _scenario_id, profile, analysis, proposal, job_description = self.scenarios[0]
        unsafe = proposal.model_copy(deep=True)
        unsafe.bullet_proposals[0].proposed_text = (
            "Led a 90-person SAP S/4HANA transformation across global operations."
        )
        report = build_resume_report(
            profile,
            analysis,
            unsafe,
            generated_filename="Candidate_Target_Role_Resume.docx",
            job_description=job_description,
        )
        checks = [
            check
            for section in report.sections()
            for subsection in section.subsections
            for check in subsection.checks
            if check.label == "Generated candidate claims are traceable to verified evidence"
        ]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status, "fail")
        self.assertIn("Unsupported generated claim", checks[0].detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
