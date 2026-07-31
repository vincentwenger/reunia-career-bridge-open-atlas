from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from career_bridge.domain.enums import EvidenceVerificationStatus
from career_bridge.domain.models import (
    CandidateProfile,
    CareerBackground,
    CareerExperience,
    EvidenceItem,
)
from job_discovery.models import DiscoveredJob, JobSourceType, WorkplaceType, discovered_job_id
from job_discovery.ranking import CandidateJobProfile, assess_analyzed_job
from job_discovery.storage import JsonFileDiscoveryStore
from products.resume_taylor.resume_tailor.models import JobAnalysis, JobRequirement


def discovered_job() -> DiscoveredJob:
    return DiscoveredJob(
        id=discovered_job_id("owner-1", "source-1", "job-1"),
        owner_id="owner-1",
        source_id="source-1",
        external_job_id="job-1",
        company="Example Bank",
        title="Senior Data Platform Engineer",
        location="Portland, OR",
        workplace_type=WorkplaceType.HYBRID,
        employment_type="Full-time",
        description="SQL, regulatory platforms, Snowflake, and Kubernetes are required.",
        canonical_url="https://example.com/jobs/job-1",
        source_type=JobSourceType.GREENHOUSE,
        first_seen_at="2026-07-30T18:00:00+00:00",
        last_seen_at="2026-07-30T18:00:00+00:00",
    )


def analysis() -> JobAnalysis:
    return JobAnalysis(
        target_title="Senior Data Platform Engineer",
        target_company="Example Bank",
        requirements=[
            JobRequirement(
                id="sql",
                category="technical_skill",
                priority="critical",
                requirement="SQL",
                keywords=["SQL"],
            ),
            JobRequirement(
                id="regulatory",
                category="domain_knowledge",
                priority="important",
                requirement="Financial-services regulatory data platforms",
                keywords=["regulatory data platforms", "financial services"],
            ),
            JobRequirement(
                id="snowflake",
                category="technical_skill",
                priority="important",
                requirement="Direct Snowflake experience",
                keywords=["Snowflake"],
            ),
            JobRequirement(
                id="kubernetes",
                category="technical_skill",
                priority="important",
                requirement="Kubernetes",
                keywords=["Kubernetes"],
            ),
        ],
    )


class JobDiscoveryEvidenceProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile_record = CandidateProfile(
            id="profile-1",
            user_id="owner-1",
            full_name="Candidate",
            headline="Data platform engineer",
            location="Portland, OR",
            preferred_roles=("Data Platform Engineer",),
        )
        self.background = CareerBackground(
            id="background-1",
            candidate_profile_id="profile-1",
            professional_summary="Built financial-services reporting platforms.",
            experiences=(
                CareerExperience(
                    id="experience-1",
                    employer="Verified Bank Vendor",
                    title="Lead Software Engineer",
                    summary="Led delivery of regulatory data platforms for banking clients.",
                ),
            ),
            skills=("SQL", "Python"),
        )
        self.verified_item = EvidenceItem(
            id="evidence-1",
            candidate_profile_id="profile-1",
            statement="Delivered regulatory reporting systems for financial-services clients.",
            verification_status=EvidenceVerificationStatus.DOCUMENT_VERIFIED,
        )
        self.unverified_item = EvidenceItem(
            id="evidence-unverified",
            candidate_profile_id="profile-1",
            statement="Led Kubernetes and Snowflake migrations.",
            verification_status=EvidenceVerificationStatus.UNVERIFIED,
        )

    def test_displayed_strengths_use_only_confirmed_evidence_records(self) -> None:
        profile = CandidateJobProfile.from_career_records(
            self.profile_record,
            self.background,
            (self.verified_item, self.unverified_item),
        )

        ranked = assess_analyzed_job(discovered_job(), profile, analysis())
        matches = {item.requirement_id: item for item in ranked.fit_snapshot.evidence_matches}

        self.assertNotIn("sql", matches)
        self.assertIn("regulatory", matches)
        self.assertNotIn("snowflake", matches)
        self.assertNotIn("kubernetes", matches)
        self.assertIn("Direct Snowflake experience", ranked.fit_snapshot.unsupported_requirements)
        self.assertIn("Kubernetes", ranked.fit_snapshot.unsupported_requirements)

        allowed_record_ids = {self.verified_item.id}
        for match in matches.values():
            self.assertTrue(match.evidence)
            for reference in match.evidence:
                self.assertIn(reference.record_id, allowed_record_ids)
                self.assertNotEqual("evidence-unverified", reference.record_id)
                self.assertEqual("Career Evidence Library", reference.surface)

    def test_job_description_keyword_alone_cannot_create_strength(self) -> None:
        empty_profile = CandidateJobProfile()

        ranked = assess_analyzed_job(discovered_job(), empty_profile, analysis())

        self.assertEqual((), ranked.fit_snapshot.evidence_matches)
        self.assertFalse(any(reason.startswith("Direct Snowflake") for reason in ranked.reasons if " — Career " in reason))
        self.assertIn("Direct Snowflake experience", ranked.fit_snapshot.unsupported_requirements)
        self.assertIn("Kubernetes", ranked.fit_snapshot.unsupported_requirements)

    def test_provenance_survives_json_storage_round_trip(self) -> None:
        profile = CandidateJobProfile.from_career_records(
            self.profile_record,
            self.background,
            (self.verified_item,),
        )
        snapshot = assess_analyzed_job(discovered_job(), profile, analysis()).fit_snapshot

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            first = JsonFileDiscoveryStore(path)
            first.put_fit_snapshot(snapshot)
            second = JsonFileDiscoveryStore(path)
            restored = second.get_fit_snapshot(
                snapshot.owner_id,
                snapshot.job_id,
                snapshot.profile_fingerprint,
                snapshot.description_fingerprint,
            )

        self.assertEqual(snapshot, restored)
        self.assertTrue(restored.evidence_matches)
        self.assertEqual(
            snapshot.evidence_matches[0].evidence[0].record_id,
            restored.evidence_matches[0].evidence[0].record_id,
        )


if __name__ == "__main__":
    unittest.main()


class ResumeWorkflowDiscoveryProfileTests(unittest.TestCase):
    def test_workflow_profile_creates_traceable_source_references(self) -> None:
        from products.resume_taylor.resume_tailor.models import (
            CandidateProfile as ResumeCandidateProfile,
            ContactInfo,
            EducationItem,
            Experience,
            NewcomerCareerProfile,
            ResumeBullet,
            SupplementalEvidence,
            VerifiedSkills,
        )

        profile = ResumeCandidateProfile(
            name="Candidate",
            contact=ContactInfo(
                location="Portland, OR",
                phone="",
                email="candidate@example.com",
            ),
            current_summary="Data platform engineer in financial services.",
            skills=VerifiedSkills(
                hard_skills=["SQL"],
                tools_software=["Oracle"],
            ),
            education=[
                EducationItem(
                    credential="Certificate",
                    institution="Example University",
                    date="2025",
                )
            ],
            experiences=[
                Experience(
                    id="experience-1",
                    employer="Example Bank",
                    location="Portland, OR",
                    dates="2020-2026",
                    title="Lead Engineer",
                    bullets=[
                        ResumeBullet(
                            id="bullet-1",
                            text="Delivered regulatory data platforms using SQL.",
                        )
                    ],
                )
            ],
            supplemental_evidence=[
                SupplementalEvidence(
                    id="evidence-1",
                    statement="Led client delivery for banking customers.",
                )
            ],
        )
        background = NewcomerCareerProfile(
            target_role="Senior Data Platform Engineer",
            professional_certifications=["AWS Certification"],
        )

        discovery_profile = CandidateJobProfile.from_resume_workflow(
            profile,
            background,
        )

        record_ids = {item.record_id for item in discovery_profile.evidence_references}
        self.assertIn("bullet-1", record_ids)
        self.assertIn("evidence-1", record_ids)
        self.assertIn("experience-1", record_ids)
        self.assertIn("Senior Data Platform Engineer", discovery_profile.target_titles)
        self.assertIn("SQL", discovery_profile.verified_skills)
        self.assertNotIn("AWS Certification", discovery_profile.licenses_certifications)
        self.assertFalse(any("AWS Certification" in item.statement for item in discovery_profile.evidence_references))
