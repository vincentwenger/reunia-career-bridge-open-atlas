from __future__ import annotations

import unittest
from datetime import datetime, timezone

import career_bridge
from career_bridge.domain.enums import (
    ActionPriority,
    ApplicationStatus,
    EvidenceType,
    EvidenceVerificationStatus,
    ImprovementArea,
    PreparationStatus,
    ProcessingStatus,
    ScoreKind,
)
from career_bridge.domain.models import (
    CandidateProfile,
    CareerBackground,
    EducationRecord,
    EvidenceItem,
    EvidenceLibrary,
    ImprovementAction,
    InterviewPreparation,
    InterviewQuestion,
    JobApplication,
    JobApplicationBundle,
    MockInterviewSession,
    Resume,
    Score,
    TailoredResumeVersion,
    TargetJobDescription,
    UserProfile,
)


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 28, 13, 0, tzinfo=timezone.utc)


def make_application(**overrides: object) -> JobApplication:
    values: dict[str, object] = {
        "id": "application-1",
        "user_id": "user-1",
        "candidate_profile_id": "candidate-1",
        "career_background_id": "background-1",
        "resume_id": "resume-1",
        "target_job_description_id": "job-1",
        "evidence_library_id": "library-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return JobApplication(**values)  # type: ignore[arg-type]


class DomainModelTests(unittest.TestCase):
    def test_user_email_is_normalized(self) -> None:
        profile = UserProfile(id="user-1", email=" Vincent@Example.com ", created_at=NOW)
        self.assertEqual(profile.email, "vincent@example.com")

    def test_legacy_journey_model_is_not_exported(self) -> None:
        self.assertFalse(hasattr(career_bridge, "CareerJourney"))
        self.assertTrue(hasattr(career_bridge, "JobApplication"))

    def test_job_application_is_the_aggregate_root(self) -> None:
        application = make_application(
            tailored_resume_version_ids=("tailored-1",),
            current_tailored_resume_version_id="tailored-1",
            interview_preparation_id="prep-1",
            mock_interview_session_ids=("mock-1",),
            improvement_action_ids=("action-1",),
        )

        self.assertEqual(application.candidate_profile_id, "candidate-1")
        self.assertEqual(application.career_background_id, "background-1")
        self.assertEqual(application.resume_id, "resume-1")
        self.assertEqual(application.target_job_description_id, "job-1")
        self.assertEqual(application.evidence_library_id, "library-1")
        self.assertEqual(application.current_tailored_resume_version_id, "tailored-1")
        self.assertEqual(application.interview_preparation_id, "prep-1")
        self.assertEqual(application.mock_interview_session_ids, ("mock-1",))
        self.assertEqual(application.improvement_action_ids, ("action-1",))

    def test_application_uses_controlled_status_transitions(self) -> None:
        application = make_application()
        preparing = application.transition_to(
            ApplicationStatus.PREPARING,
            changed_at=LATER,
            reason="Resume and interview preparation started",
        )
        self.assertEqual(preparing.status, ApplicationStatus.PREPARING)
        self.assertEqual(preparing.status_history[-1].from_status, ApplicationStatus.DRAFT)
        self.assertEqual(preparing.status_history[-1].to_status, ApplicationStatus.PREPARING)

        with self.assertRaises(ValueError):
            application.transition_to(ApplicationStatus.APPLIED, changed_at=LATER)

    def test_application_relationship_methods_deduplicate_ids(self) -> None:
        application = make_application()
        application = application.with_tailored_resume_version(
            "tailored-1",
            changed_at=LATER,
        )
        application = application.with_tailored_resume_version(
            "tailored-1",
            changed_at=LATER,
        )
        application = application.with_interview_preparation("prep-1", changed_at=LATER)
        application = application.with_mock_interview_session("mock-1", changed_at=LATER)
        application = application.with_improvement_action("action-1", changed_at=LATER)

        self.assertEqual(application.tailored_resume_version_ids, ("tailored-1",))
        self.assertEqual(application.current_tailored_resume_version_id, "tailored-1")
        self.assertEqual(application.interview_preparation_id, "prep-1")
        self.assertEqual(application.mock_interview_session_ids, ("mock-1",))
        self.assertEqual(application.improvement_action_ids, ("action-1",))

    def test_current_tailored_resume_must_be_referenced(self) -> None:
        with self.assertRaises(ValueError):
            make_application(current_tailored_resume_version_id="missing")

    def test_score_is_bounded_and_application_scoped(self) -> None:
        score = Score(
            id="score-1",
            application_id="application-1",
            kind=ScoreKind.JOB_FIT,
            value=83.333,
            confidence=0.8,
            created_at=NOW,
        )
        self.assertEqual(score.value, 83.33)
        with self.assertRaises(ValueError):
            Score(
                id="bad",
                application_id="application-1",
                kind=ScoreKind.OVERALL,
                value=101,
                created_at=NOW,
            )

    def test_hydrated_bundle_validates_every_relationship(self) -> None:
        candidate = CandidateProfile(
            id="candidate-1",
            user_id="user-1",
            full_name="Vincent Wenger",
            created_at=NOW,
            updated_at=NOW,
        )
        background = CareerBackground(
            id="background-1",
            candidate_profile_id=candidate.id,
            education=(
                EducationRecord(
                    id="education-1",
                    institution="UC Berkeley",
                    credential="Professional Certificate",
                ),
            ),
            skills=("Python", "SQL"),
            source_resume_ids=("resume-1",),
            created_at=NOW,
            updated_at=NOW,
        )
        resume = Resume(
            id="resume-1",
            candidate_profile_id=candidate.id,
            career_background_id=background.id,
            document_id="document-resume-1",
            created_at=NOW,
            updated_at=NOW,
        )
        target_job = TargetJobDescription(
            id="job-1",
            application_id="application-1",
            role_title="Senior Software Engineer",
            company_name="Example Bank",
            raw_text="Build regulatory reporting software.",
            captured_at=NOW,
        )
        evidence = EvidenceItem(
            id="evidence-1",
            candidate_profile_id=candidate.id,
            statement="Delivered more than 50 regulatory reports.",
            evidence_type=EvidenceType.ACHIEVEMENT,
            verification_status=EvidenceVerificationStatus.CANDIDATE_CONFIRMED,
            created_at=NOW,
            updated_at=NOW,
        )
        library = EvidenceLibrary(
            id="library-1",
            candidate_profile_id=candidate.id,
            evidence_item_ids=(evidence.id,),
            created_at=NOW,
            updated_at=NOW,
        )
        tailored = TailoredResumeVersion(
            id="tailored-1",
            application_id="application-1",
            version=1,
            source_resume_id=resume.id,
            generated_document_id="document-tailored-1",
            status=ProcessingStatus.READY,
            evidence_item_ids=(evidence.id,),
            created_at=NOW,
        )
        preparation = InterviewPreparation(
            id="prep-1",
            application_id="application-1",
            status=PreparationStatus.IN_PROGRESS,
            question_ids=("question-1",),
            selected_evidence_item_ids=(evidence.id,),
            created_at=NOW,
            updated_at=NOW,
        )
        question = InterviewQuestion(
            id="question-1",
            preparation_id=preparation.id,
            prompt="Tell me about a complex regulatory-reporting delivery.",
            suggested_evidence_item_ids=(evidence.id,),
        )
        mock = MockInterviewSession(
            id="mock-1",
            application_id="application-1",
            interview_preparation_id=preparation.id,
            created_at=NOW,
        )
        action = ImprovementAction(
            id="action-1",
            application_id="application-1",
            title="Strengthen the delivery example",
            owner_user_id="user-1",
            area=ImprovementArea.INTERVIEW_DELIVERY,
            priority=ActionPriority.HIGH,
            created_at=NOW,
            updated_at=NOW,
        )
        application = make_application(
            tailored_resume_version_ids=(tailored.id,),
            current_tailored_resume_version_id=tailored.id,
            interview_preparation_id=preparation.id,
            mock_interview_session_ids=(mock.id,),
            improvement_action_ids=(action.id,),
        )

        bundle = JobApplicationBundle(
            application=application,
            candidate_profile=candidate,
            career_background=background,
            resume=resume,
            target_job_description=target_job,
            evidence_library=library,
            evidence_items=(evidence,),
            tailored_resume_versions=(tailored,),
            interview_preparation=preparation,
            interview_questions=(question,),
            mock_interview_sessions=(mock,),
            improvement_actions=(action,),
        )
        self.assertEqual(bundle.application.id, "application-1")

        wrong_session = MockInterviewSession(
            id="mock-1",
            application_id="another-application",
            interview_preparation_id=preparation.id,
            created_at=NOW,
        )
        with self.assertRaises(ValueError):
            JobApplicationBundle(
                application=application,
                candidate_profile=candidate,
                career_background=background,
                resume=resume,
                target_job_description=target_job,
                evidence_library=library,
                evidence_items=(evidence,),
                tailored_resume_versions=(tailored,),
                interview_preparation=preparation,
                interview_questions=(question,),
                mock_interview_sessions=(wrong_session,),
                improvement_actions=(action,),
            )


if __name__ == "__main__":
    unittest.main()
