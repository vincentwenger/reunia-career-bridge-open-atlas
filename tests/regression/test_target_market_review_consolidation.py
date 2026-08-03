"""Regression coverage for clear, non-duplicated Target-Market Review findings."""

from __future__ import annotations

import unittest
from pathlib import Path

from products.resume_taylor.resume_tailor.career_translation import (
    ensure_career_translation_assessment,
)
from products.resume_taylor.resume_tailor.models import (
    CandidateProfile,
    CandidateQuestion,
    CareerTranslationAssessment,
    CareerTranslationFinding,
    EvidenceMatch,
    JobAnalysis,
    JobRequirement,
    SkillSet,
    TailoringProposal,
)

ROOT = Path(__file__).resolve().parents[2]


class TargetMarketReviewConsolidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = CandidateProfile.model_validate_json(
            (ROOT / "products" / "resume_taylor" / "data" / "candidate_profile.json").read_text(
                encoding="utf-8"
            )
        )

    @staticmethod
    def _proposal(
        requirements: list[JobRequirement],
        questions: list[CandidateQuestion],
    ) -> TailoringProposal:
        findings: list[CareerTranslationFinding] = []
        question_ids = {question.requirement_id for question in questions}
        for requirement in requirements:
            findings.append(
                CareerTranslationFinding(
                    category="unsupported_requirement",
                    source_text=requirement.requirement,
                    translated_meaning="Target-job requirement not supported by current evidence.",
                    disposition=(
                        "user_clarification_required"
                        if requirement.id in question_ids
                        else "recommended_learning_or_future_action"
                    ),
                    evidence_ids=[],
                    rationale="Legacy unsupported card.",
                    recommended_action="Legacy action.",
                )
            )
        for question in questions:
            requirement = next(
                item for item in requirements if item.id == question.requirement_id
            )
            findings.append(
                CareerTranslationFinding(
                    category="missing_evidence",
                    source_text=requirement.requirement,
                    translated_meaning="A specific candidate fact could change the resume decision.",
                    disposition="user_clarification_required",
                    evidence_ids=[question.source_id] if question.source_id else [],
                    rationale=question.help_text,
                    recommended_action=question.details_prompt,
                )
            )
        return TailoringProposal(
            professional_summary="Evidence-grounded proposal.",
            skills=SkillSet(),
            bullet_proposals=[],
            evidence_matches=[
                EvidenceMatch(
                    requirement_id=requirement.id,
                    status="unsupported",
                    evidence_ids=[],
                    rationale="No exact phrase match was found.",
                )
                for requirement in requirements
            ],
            unsupported_requirements=[item.requirement for item in requirements],
            candidate_questions=questions,
            career_translation_assessment=CareerTranslationAssessment(
                findings=findings
            ),
        )

    def test_duplicate_unsupported_and_question_cards_are_merged(self) -> None:
        requirement = JobRequirement(
            id="REQ-K8S",
            category="technical_skill",
            priority="important",
            requirement="Administer Kubernetes clusters",
            keywords=["Kubernetes"],
        )
        question = CandidateQuestion(
            id="Q-K8S",
            requirement_id=requirement.id,
            source_id="NAS-01",
            question="Have you administered Kubernetes clusters?",
            answer_type="yes_no_with_details",
            help_text="A specific example would determine whether this can be claimed.",
            details_prompt="Describe the cluster, your responsibilities, and the result.",
        )

        result = ensure_career_translation_assessment(
            self.profile,
            JobAnalysis(target_title="Platform Engineer", requirements=[requirement]),
            self._proposal([requirement], [question]),
        )
        matching = [
            item
            for item in result.career_translation_assessment.findings
            if item.source_text == requirement.requirement
        ]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].category, "missing_evidence")
        self.assertEqual(matching[0].disposition, "user_clarification_required")
        self.assertIn("cluster", matching[0].recommended_action.casefold())

    def test_common_broad_capabilities_use_existing_verified_evidence(self) -> None:
        requirement_texts = [
            "Troubleshoot production issues",
            "Strong collaboration skills",
            "Application Development",
            "Create and maintain technical documentation",
            "Performance Optimization",
        ]
        requirements = [
            JobRequirement(
                id=f"REQ-{index}",
                category="responsibility",
                priority="important",
                requirement=text,
                keywords=text.split(),
            )
            for index, text in enumerate(requirement_texts, start=1)
        ]
        questions = [
            CandidateQuestion(
                id=f"Q-{index}",
                requirement_id=requirement.id,
                source_id="NAS-01",
                question=f"Do you have experience with {requirement.requirement}?",
                answer_type="yes_no_with_details",
                help_text="Provide a specific example.",
                details_prompt="Provide one verified example.",
            )
            for index, requirement in enumerate(requirements, start=1)
        ]

        result = ensure_career_translation_assessment(
            self.profile,
            JobAnalysis(target_title="Data Engineer", requirements=requirements),
            self._proposal(requirements, questions),
        )

        for requirement in requirements:
            with self.subTest(requirement=requirement.requirement):
                match = next(
                    item
                    for item in result.evidence_matches
                    if item.requirement_id == requirement.id
                )
                findings = [
                    item
                    for item in result.career_translation_assessment.findings
                    if item.source_text == requirement.requirement
                ]
                self.assertEqual(match.status, "supported")
                self.assertTrue(match.evidence_ids)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].category, "transferable_skill")
                self.assertEqual(findings[0].disposition, "confirmed_experience")

        self.assertEqual(result.candidate_questions, [])
        self.assertEqual(result.unsupported_requirements, [])

    def test_data_role_broad_capabilities_reuse_traceable_evidence(self) -> None:
        requirement_texts = [
            "Familiarity with data architecture principles",
            "Identify opportunities to improve processes and automation",
            "Analytical Thinking",
            "Analyze complex data problems",
            "Contribute to platform resiliency and performance tuning",
        ]
        requirements = [
            JobRequirement(
                id=f"REQ-BROAD-{index}",
                category="responsibility",
                priority="important",
                requirement=text,
                keywords=text.split(),
            )
            for index, text in enumerate(requirement_texts, start=1)
        ]
        questions = [
            CandidateQuestion(
                id=f"Q-BROAD-{index}",
                requirement_id=requirement.id,
                source_id="NAS-01",
                question=f"Do you have experience with {requirement.requirement}?",
                answer_type="yes_no_with_details",
                help_text="Provide a specific example.",
                details_prompt="Provide one verified example.",
            )
            for index, requirement in enumerate(requirements, start=1)
        ]

        result = ensure_career_translation_assessment(
            self.profile,
            JobAnalysis(target_title="Data Engineer", requirements=requirements),
            self._proposal(requirements, questions),
        )

        for requirement in requirements:
            with self.subTest(requirement=requirement.requirement):
                match = next(
                    item
                    for item in result.evidence_matches
                    if item.requirement_id == requirement.id
                )
                finding = next(
                    item
                    for item in result.career_translation_assessment.findings
                    if item.source_text == requirement.requirement
                )
                self.assertEqual(match.status, "supported")
                self.assertTrue(match.evidence_ids)
                self.assertEqual(finding.category, "transferable_skill")
                self.assertEqual(finding.disposition, "confirmed_experience")

        self.assertEqual(result.candidate_questions, [])
        self.assertEqual(result.unsupported_requirements, [])

    def test_domain_specific_aml_and_code_review_claims_are_not_inferred(self) -> None:
        aml = JobRequirement(
            id="REQ-AML",
            category="responsibility",
            priority="important",
            requirement="Support day-to-day operation of AML data platforms",
            keywords=["AML", "data platforms"],
        )
        code_review = JobRequirement(
            id="REQ-CODE-REVIEW",
            category="responsibility",
            priority="important",
            requirement="Participate in code reviews and testing activities",
            keywords=["code reviews", "testing"],
        )
        question = CandidateQuestion(
            id="Q-CODE-REVIEW",
            requirement_id=code_review.id,
            source_id="NAS-07",
            question="Did you regularly review code written by other developers?",
            answer_type="yes_no_with_details",
            help_text="Testing is documented, but code-review responsibility needs confirmation.",
            details_prompt="Describe your code-review role and one example.",
        )

        result = ensure_career_translation_assessment(
            self.profile,
            JobAnalysis(target_title="AML Data Engineer", requirements=[aml, code_review]),
            self._proposal([aml, code_review], [question]),
        )

        aml_match = next(
            item for item in result.evidence_matches if item.requirement_id == aml.id
        )
        aml_finding = next(
            item
            for item in result.career_translation_assessment.findings
            if item.source_text == aml.requirement
        )
        code_review_finding = next(
            item
            for item in result.career_translation_assessment.findings
            if item.source_text == code_review.requirement
        )

        self.assertEqual(aml_match.status, "unsupported")
        self.assertEqual(aml_finding.disposition, "unsupported_claim")
        self.assertEqual(code_review_finding.category, "missing_evidence")
        self.assertEqual(code_review_finding.disposition, "user_clarification_required")
        self.assertEqual(
            [item.requirement_id for item in result.candidate_questions],
            [code_review.id],
        )

    def test_profile_skill_without_specific_example_stays_one_evidence_question(self) -> None:
        requirement = JobRequirement(
            id="REQ-TRANSLATE",
            category="responsibility",
            priority="important",
            requirement=(
                "Analyze data requirements and translate business needs into technical solutions"
            ),
            keywords=["data requirements", "business needs", "technical solutions"],
        )
        question = CandidateQuestion(
            id="Q-TRANSLATE",
            requirement_id=requirement.id,
            source_id="NAS-01",
            question="Have you translated business requirements into a technical solution?",
            answer_type="yes_no_with_details",
            help_text="A concrete example would strengthen the application.",
            details_prompt="Describe the requirement, solution, and outcome.",
        )

        result = ensure_career_translation_assessment(
            self.profile,
            JobAnalysis(target_title="Data Engineer", requirements=[requirement]),
            self._proposal([requirement], [question]),
        )
        matching = [
            item
            for item in result.career_translation_assessment.findings
            if item.source_text == requirement.requirement
        ]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].category, "missing_evidence")
        self.assertEqual(matching[0].disposition, "user_clarification_required")
        self.assertEqual(
            [item.requirement_id for item in result.candidate_questions],
            [requirement.id],
        )

    def test_missing_secondary_requirement_is_not_automatically_a_learning_gap(self) -> None:
        requirement = JobRequirement(
            id="REQ-REUSE",
            category="responsibility",
            priority="secondary",
            requirement="Develop scalable and reusable data components",
            keywords=["scalable", "reusable", "data components"],
        )

        result = ensure_career_translation_assessment(
            self.profile,
            JobAnalysis(target_title="Data Engineer", requirements=[requirement]),
            self._proposal([requirement], []),
        )
        finding = next(
            item
            for item in result.career_translation_assessment.findings
            if item.source_text == requirement.requirement
        )

        self.assertEqual(finding.category, "unsupported_requirement")
        self.assertEqual(finding.disposition, "unsupported_claim")
        self.assertIn("not proof", finding.rationale)
        self.assertNotIn("learning", finding.recommended_action.casefold())
        self.assertFalse(
            any(
                requirement.requirement in item.translated_meaning
                for item in result.career_translation_assessment.findings
                if item is not finding
            )
        )

    def test_explicit_negative_answer_can_remain_a_development_opportunity(self) -> None:
        requirement = JobRequirement(
            id="REQ-SALESFORCE",
            category="technical_skill",
            priority="secondary",
            requirement="Maintain customer activity in Salesforce CRM",
            keywords=["Salesforce", "CRM"],
        )
        proposal = self._proposal([requirement], [])
        proposal.career_translation_assessment.findings = [
            CareerTranslationFinding(
                category="unsupported_requirement",
                source_text=requirement.requirement,
                translated_meaning="The candidate confirmed this is not current experience.",
                disposition="recommended_learning_or_future_action",
                evidence_ids=[],
                rationale="The candidate confirmed they do not have Salesforce CRM experience.",
                recommended_action="Treat Salesforce practice as an optional development opportunity.",
            )
        ]

        result = ensure_career_translation_assessment(
            self.profile,
            JobAnalysis(target_title="Customer Success Manager", requirements=[requirement]),
            proposal,
        )
        finding = next(
            item
            for item in result.career_translation_assessment.findings
            if item.source_text == requirement.requirement
        )

        self.assertEqual(
            finding.disposition,
            "recommended_learning_or_future_action",
        )
        self.assertIn("confirmed", finding.rationale.casefold())


if __name__ == "__main__":
    unittest.main()
