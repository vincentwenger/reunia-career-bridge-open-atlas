from __future__ import annotations

import unittest

from career_bridge.domain.enums import EvidenceVerificationStatus
from career_bridge.domain.fit_scoring import build_requirement_fit_assessment
from career_bridge.domain.models import (
    CandidateProfile as CareerCandidateProfile,
    CareerBackground,
    EvidenceItem,
)
from job_discovery.models import (
    DiscoveredJob,
    JobSourceType,
    WorkplaceType,
    discovered_job_id,
)
from job_discovery.ranking import (
    CandidateJobProfile,
    assess_analyzed_job,
    build_discovery_requirements,
    build_discovery_requirement_statuses,
    rank_jobs,
)
from products.resume_taylor.resume_tailor.application_fit import (
    build_application_fit_assessment,
    build_requirement_statuses,
)
from products.resume_taylor.resume_tailor.models import (
    CandidateProfile,
    ContactInfo,
    EvidenceMatch,
    JobAnalysis,
    JobRequirement,
    SkillSet,
    TailoringProposal,
    VerifiedSkills,
)


class SharedFitScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = JobAnalysis(
            target_title="Platform Engineer",
            requirements=[
                JobRequirement(
                    id="python",
                    category="technical_skill",
                    priority="critical",
                    requirement="Python",
                ),
                JobRequirement(
                    id="aws",
                    category="technical_skill",
                    priority="important",
                    requirement="AWS",
                ),
                JobRequirement(
                    id="leadership",
                    category="leadership",
                    priority="secondary",
                    requirement="Technical leadership",
                ),
            ],
        )
        self.profile = CandidateProfile(
            name="Candidate",
            contact=ContactInfo(
                location="Portland, OR",
                phone="",
                email="candidate@example.com",
            ),
            current_summary="Platform engineer",
            skills=VerifiedSkills(hard_skills=["Python", "AWS"]),
            education=[],
            experiences=[],
        )
        self.proposal = TailoringProposal(
            professional_summary="Platform engineer",
            skills=SkillSet(hard_skills=["Python", "AWS"]),
            bullet_proposals=[],
            evidence_matches=[
                EvidenceMatch(
                    requirement_id="python",
                    status="supported",
                    rationale="Verified skill",
                ),
                EvidenceMatch(
                    requirement_id="aws",
                    status="partial",
                    rationale="Limited evidence",
                ),
                EvidenceMatch(
                    requirement_id="leadership",
                    status="unsupported",
                    rationale="No evidence",
                ),
            ],
        )

    def test_resume_adapter_matches_direct_status_scorer(self) -> None:
        statuses = build_requirement_statuses(
            self.analysis,
            self.proposal,
            self.profile,
        )
        direct = build_requirement_fit_assessment(
            self.analysis.requirements,
            statuses,
            stage_label="Preliminary assessment",
        )
        workflow = build_application_fit_assessment(
            self.analysis,
            self.proposal,
            self.profile,
        )

        self.assertEqual(direct, workflow)
        self.assertEqual(
            {"python": "supported", "aws": "partial", "leadership": "unsupported"},
            statuses,
        )

    def test_discovery_uses_shared_score_recommendation_and_blockers(self) -> None:
        job = DiscoveredJob(
            id=discovered_job_id("owner-1", "source-1", "job-1"),
            owner_id="owner-1",
            source_id="source-1",
            external_job_id="job-1",
            company="Acme",
            title="Platform Engineer",
            canonical_url="https://jobs.example.com/job-1",
            description="Build a Python platform.",
            skills=("Python", "AWS"),
            source_type=JobSourceType.GREENHOUSE,
            workplace_type=WorkplaceType.HYBRID,
            metadata={
                "requirements": [
                    {
                        "id": "authorization",
                        "requirement": "Must be authorized to work in the United States",
                        "priority": "critical",
                        "category": "qualification",
                    }
                ]
            },
        )
        profile = CandidateJobProfile(
            target_titles=("Platform Engineer",),
            verified_skills=("Python", "AWS"),
        )

        requirements = build_discovery_requirements(job)
        statuses = build_discovery_requirement_statuses(requirements, profile)
        direct = build_requirement_fit_assessment(
            requirements,
            statuses,
            confirmation_complete=True,
            stage_label="Discovery assessment from Career Profile and Evidence Library",
        )
        ranked = rank_jobs([job], profile)[0]

        self.assertEqual(direct.score, ranked.score)
        self.assertEqual(direct.recommendation, ranked.fit_snapshot.recommendation)
        self.assertEqual(direct.hard_blockers, ranked.fit_snapshot.hard_blockers)
        self.assertEqual(45.0, ranked.score)
        self.assertEqual("low", ranked.assessment.recommendation_key)
        self.assertIn("Must be authorized", ranked.fit_snapshot.hard_blockers[0])

    def test_evidence_library_can_support_discovery_requirement_directly(self) -> None:
        job = DiscoveredJob(
            id=discovered_job_id("owner-1", "source-1", "job-2"),
            owner_id="owner-1",
            source_id="source-1",
            external_job_id="job-2",
            company="Acme",
            title="Regulatory Reporting Engineer",
            canonical_url="https://jobs.example.com/job-2",
            description="Regulatory reporting role.",
            source_type=JobSourceType.GREENHOUSE,
            metadata={
                "requirements": [
                    {
                        "id": "regulatory",
                        "requirement": "Regulatory reporting experience",
                        "priority": "critical",
                        "category": "domain_knowledge",
                    }
                ]
            },
        )
        profile = CandidateJobProfile(
            target_titles=("Regulatory Reporting Engineer",),
            evidence_statements=(
                "Delivered regulatory reporting solutions for banking clients",
            ),
        )

        ranked = rank_jobs([job], profile)[0]

        self.assertIn(
            "Regulatory reporting experience",
            ranked.fit_snapshot.supported_requirements,
        )
        self.assertEqual(0, ranked.assessment.hard_blocker_count)

    def test_location_salary_and_employment_preferences_do_not_change_job_fit(self) -> None:
        job = DiscoveredJob(
            id=discovered_job_id("owner-1", "source-1", "job-preferences"),
            owner_id="owner-1",
            source_id="source-1",
            external_job_id="job-preferences",
            company="Acme",
            title="Platform Engineer",
            canonical_url="https://jobs.example.com/job-preferences",
            description="Python role based in Miami with advertised compensation.",
            location="Miami, FL",
            workplace_type=WorkplaceType.ONSITE,
            employment_type="Contract",
            salary_max=90000,
            salary_interval="year",
            source_type=JobSourceType.GREENHOUSE,
        )
        analysis = JobAnalysis(
            target_title="Platform Engineer",
            requirements=[
                JobRequirement(
                    id="python",
                    category="technical_skill",
                    priority="critical",
                    requirement="Python",
                ),
                JobRequirement(
                    id="location",
                    category="qualification",
                    priority="critical",
                    requirement="Must be located in Miami, FL",
                ),
                JobRequirement(
                    id="salary",
                    category="qualification",
                    priority="important",
                    requirement="Salary range $80,000 to $90,000 per year",
                ),
                JobRequirement(
                    id="employment",
                    category="qualification",
                    priority="important",
                    requirement="Contract position",
                ),
            ],
        )
        profile = CandidateJobProfile(
            verified_skills=("Python",),
            preferred_locations=("Portland",),
            preferred_employment_types=("Full-time",),
            minimum_salary=120000,
        )

        ranked = assess_analyzed_job(job, profile, analysis)

        self.assertEqual(100.0, ranked.fit_score)
        self.assertEqual(("Python",), ranked.fit_snapshot.supported_requirements)
        self.assertEqual((), ranked.fit_snapshot.unsupported_requirements)
        self.assertEqual((), ranked.fit_snapshot.hard_blockers)


    def test_candidate_job_profile_uses_only_verified_career_records(self) -> None:
        career_profile = CareerCandidateProfile(
            id="profile-1",
            user_id="owner-1",
            full_name="Candidate",
            location="Portland, OR",
            preferred_roles=("Platform Engineer",),
        )
        background = CareerBackground(
            id="background-1",
            candidate_profile_id="profile-1",
            professional_summary="Built banking platforms",
            skills=("Python", "AWS"),
            certification_names=("AWS Certified Developer",),
        )
        evidence = (
            EvidenceItem(
                id="verified",
                candidate_profile_id="profile-1",
                statement="Delivered regulatory reporting systems",
                verification_status=EvidenceVerificationStatus.DOCUMENT_VERIFIED,
            ),
            EvidenceItem(
                id="unverified",
                candidate_profile_id="profile-1",
                statement="Invented Kubernetes leadership",
                verification_status=EvidenceVerificationStatus.UNVERIFIED,
            ),
        )

        profile = CandidateJobProfile.from_career_records(
            career_profile,
            background,
            evidence,
        )

        self.assertEqual(("Platform Engineer",), profile.target_titles)
        self.assertIn("Python", profile.verified_skills)
        self.assertIn("Delivered regulatory reporting systems", profile.evidence_statements)
        self.assertNotIn("Invented Kubernetes leadership", profile.evidence_statements)
        self.assertEqual(("Portland, OR",), profile.preferred_locations)

    def test_invalid_status_is_rejected_at_shared_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported requirement status"):
            build_requirement_fit_assessment(
                self.analysis.requirements,
                {"python": "maybe"},  # type: ignore[dict-item]
            )


if __name__ == "__main__":
    unittest.main()
