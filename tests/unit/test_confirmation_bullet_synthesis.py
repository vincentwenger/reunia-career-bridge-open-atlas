from __future__ import annotations

import re
import unittest

from products.resume_taylor.resume_tailor.bullet_text import (
    summarize_confirmation_answer_as_bullet,
)
from products.resume_taylor.resume_tailor.confirmation import (
    build_profile_with_candidate_answers,
    ensure_confirmed_answers_visible,
)
from products.resume_taylor.resume_tailor.models import (
    BulletProposal,
    CandidateAnswer,
    CandidateProfile,
    CandidateQuestion,
    ContactInfo,
    Experience,
    JobAnalysis,
    JobRequirement,
    ResumeBullet,
    SkillSet,
    TailoringProposal,
    VerifiedSkills,
)


PERFORMANCE_ANSWER = (
    "Over my 15 years as a software engineer, performance optimization has been "
    "a constant necessity, particularly when managing large-scale data platforms "
    "and strict regulatory reporting systems. I have optimized complex SQL queries, "
    "analyzed execution plans, created and tuned indexes, reduced unnecessary data "
    "processing, and improved batch-processing workflows. I also monitored production "
    "performance, investigated bottlenecks, and worked with development and "
    "infrastructure teams to resolve issues affecting throughput, response times, "
    "and system availability."
)


class ConfirmationBulletSynthesisTests(unittest.TestCase):
    def test_uses_specific_details_from_later_sentences(self) -> None:
        bullet = summarize_confirmation_answer_as_bullet(PERFORMANCE_ANSWER)

        self.assertTrue(bullet.startswith("Optimized complex SQL queries"))
        self.assertIn("execution plans", bullet)
        self.assertIn("tuned indexes", bullet)
        self.assertIn("batch-processing workflows", bullet)
        self.assertNotIn("Over my 15 years", bullet)
        self.assertNotIn("I have", bullet)
        self.assertLessEqual(len(re.findall(r"\b[\w'’-]+\b", bullet)), 35)

    def test_removes_conversational_lead_in_and_starts_with_action_verb(self) -> None:
        answer = (
            "From there, I developed automated transformation workflows utilizing "
            "core data models to enrich the data with new columns and custom attributes."
        )

        bullet = summarize_confirmation_answer_as_bullet(answer)

        self.assertEqual(
            bullet,
            "Developed automated transformation workflows utilizing core data models "
            "to enrich the data with new columns and custom attributes.",
        )
        self.assertFalse(bullet.casefold().startswith("from there"))
        self.assertTrue(bullet.startswith("Developed "))

    def test_removes_generic_first_person_opening_for_communication_answer(self) -> None:
        answer = (
            "Throughout my 15 years as a software engineer, strong communication has "
            "been just as critical to my success as my technical skills. I regularly "
            "collaborated with business analysts, QA engineers, infrastructure teams, "
            "and banking clients to clarify requirements, explain technical tradeoffs, "
            "coordinate releases, and resolve production issues."
        )

        bullet = summarize_confirmation_answer_as_bullet(answer)

        self.assertTrue(bullet.startswith("Collaborated with business analysts"))
        self.assertIn("clarify requirements", bullet)
        self.assertIn("resolve production issues", bullet)
        self.assertNotIn("Throughout my 15 years", bullet)

    def test_moves_verified_context_after_the_action_verb(self) -> None:
        answer = (
            "In a financial platform environment, I designed and implemented an "
            "end-to-end data pipeline supporting a Broker-Dealer solution."
        )

        bullet = summarize_confirmation_answer_as_bullet(answer)

        self.assertEqual(
            bullet,
            "Designed and implemented an end-to-end data pipeline supporting a "
            "Broker-Dealer solution in a financial platform environment.",
        )
        self.assertNotIn(" I ", f" {bullet} ")

    def test_removes_generic_context_and_converts_example_aside(self) -> None:
        answer = (
            "On the infrastructure side, I have managed the underlying database "
            "environments that house AML data-for example, orchestrating the migration "
            "and backup of dedicated AML raw data files during large-scale Oracle "
            "database upgrades."
        )

        bullet = summarize_confirmation_answer_as_bullet(answer)

        self.assertEqual(
            bullet,
            "Managed the underlying database environments that house AML data, "
            "including orchestrating the migration and backup of dedicated AML raw "
            "data files during large-scale Oracle database upgrades.",
        )
        self.assertNotIn("On the infrastructure side", bullet)
        self.assertNotIn("for example", bullet.casefold())

    def test_completed_role_uses_past_tense_but_current_role_keeps_present_tense(self) -> None:
        answer = "In this role, I manage production database environments."

        self.assertEqual(
            summarize_confirmation_answer_as_bullet(answer, use_past_tense=True),
            "Managed production database environments.",
        )
        self.assertEqual(
            summarize_confirmation_answer_as_bullet(answer, use_past_tense=False),
            "Manage production database environments.",
        )

    def test_profile_keeps_full_answer_as_evidence_and_adds_resume_ready_bullet(self) -> None:
        profile = CandidateProfile(
            name="Alex Morgan",
            contact=ContactInfo(
                location="Portland, OR",
                phone="",
                email="alex.morgan@example.com",
            ),
            current_summary="Software engineer specializing in regulatory reporting.",
            skills=VerifiedSkills(hard_skills=["SQL", "PL/SQL"]),
            education=[],
            experiences=[
                Experience(
                    id="EXP-001",
                    employer="Northstar Financial Systems",
                    location="",
                    dates="",
                    title="Lead Software Engineer",
                    bullets=[
                        ResumeBullet(
                            id="NAS-01",
                            text="Delivered regulatory reporting solutions for banking clients.",
                        )
                    ],
                )
            ],
        )
        analysis = JobAnalysis(
            target_title="Senior Software Engineer",
            requirements=[
                JobRequirement(
                    id="REQ-PERF",
                    category="technical_skill",
                    priority="important",
                    requirement="Performance optimization",
                    keywords=["SQL", "performance optimization"],
                )
            ],
        )
        question = CandidateQuestion(
            id="Q-PERF",
            requirement_id="REQ-PERF",
            question=(
                "What techniques or approaches have you utilized for performance "
                "optimization in your projects?"
            ),
            answer_type="long_text",
        )
        answer = CandidateAnswer(
            question_id=question.id,
            question=question.question,
            requirement_id=question.requirement_id,
            answer_type=question.answer_type,
            text=PERFORMANCE_ANSWER,
            experience_id="EXP-001",
            placement="new_bullet",
        )

        updated = build_profile_with_candidate_answers(
            profile,
            analysis,
            [question],
            [answer],
        )

        confirmed_bullet = updated.experiences[0].bullets[-1]
        self.assertEqual(confirmed_bullet.id, "NAS-CONF-01")
        self.assertTrue(confirmed_bullet.text.startswith("Optimized complex SQL queries"))
        self.assertNotIn("Over my 15 years", confirmed_bullet.text)
        self.assertEqual(updated.supplemental_evidence[0].statement, PERFORMANCE_ANSWER)
        self.assertEqual(updated.supplemental_evidence[0].source_bullet_id, "NAS-CONF-01")

        # Simulate a proposal saved by an older build before conversational lead-ins
        # were normalized. Reopening the workflow should repair it automatically.
        legacy_confirmation_text = (
            "From there, I developed automated transformation workflows utilizing "
            "core data models to enrich the data with new columns and custom attributes."
        )
        provisional = TailoringProposal(
            professional_summary=updated.current_summary,
            skills=SkillSet(hard_skills=["SQL", "PL/SQL"]),
            bullet_proposals=[
                BulletProposal(
                    source_bullet_id="NAS-01",
                    include=True,
                    proposed_text=updated.experiences[0].bullets[0].text,
                    evidence_note="Directly supported by source bullet NAS-01.",
                ),
                BulletProposal(
                    source_bullet_id="NAS-CONF-01",
                    include=True,
                    proposed_text=legacy_confirmation_text,
                    evidence_note="Candidate-confirmed experience from CONF-Q-PERF.",
                ),
            ],
            evidence_matches=[],
        )
        restored = ensure_confirmed_answers_visible(updated, provisional)
        restored_confirmed = next(
            item
            for item in restored.bullet_proposals
            if item.source_bullet_id == "NAS-CONF-01"
        )
        self.assertTrue(restored_confirmed.include)
        self.assertEqual(
            restored_confirmed.proposed_text,
            "Developed automated transformation workflows utilizing core data models "
            "to enrich the data with new columns and custom attributes",
        )
        self.assertFalse(restored_confirmed.proposed_text.startswith("From there"))


if __name__ == "__main__":
    unittest.main()
