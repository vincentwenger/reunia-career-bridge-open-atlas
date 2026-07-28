from __future__ import annotations

import json
from pathlib import Path

import pytest

from resume_tailor.models import (
    BulletProposal,
    EvidenceMatch,
    JobAnalysis,
    JobRequirement,
    SkillSet,
    TailoringProposal,
)
from resume_tailor.profile_io import load_candidate_profile


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def profile(project_root):
    return load_candidate_profile(project_root / "data" / "candidate_profile.json")


@pytest.fixture
def analysis():
    return JobAnalysis(
        target_title="Axiom Developer",
        target_company="Example Bank",
        requirements=[
            JobRequirement(
                id="R1",
                category="technical_skill",
                priority="critical",
                requirement="Develop Axiom regulatory reporting solutions",
                keywords=["Axiom", "regulatory reporting"],
            ),
            JobRequirement(
                id="R2",
                category="technical_skill",
                priority="important",
                requirement="Use SQL for data transformation and performance optimization",
                keywords=["SQL", "data transformation"],
            ),
            JobRequirement(
                id="R3",
                category="methodology",
                priority="secondary",
                requirement="Apply software testing and cross-functional collaboration",
                keywords=["testing", "collaboration"],
            ),
        ],
        ignored_boilerplate=["Compensation and benefits"],
    )


@pytest.fixture
def proposal(profile):
    selected_ids = {
        "NAS-01",
        "NAS-02",
        "NAS-03",
        "NAS-04",
        "NAS-05",
        "NAS-07",
        "NAS-09",
        "AVI-01",
        "AVI-02",
        "AVI-03",
        "AVI-04",
        "CAP-01",
        "CAP-02",
        "CAP-04",
    }
    bullet_proposals = []
    for experience in profile.experiences:
        for bullet in experience.bullets:
            bullet_proposals.append(
                BulletProposal(
                    source_bullet_id=bullet.id,
                    include=bullet.id in selected_ids,
                    proposed_text=bullet.text,
                    matched_requirement_ids=["R1"] if bullet.id.startswith("NAS") else ["R3"],
                    evidence_note=f"Directly supported by source bullet {bullet.id}.",
                )
            )

    return TailoringProposal(
        professional_summary=(
            "Software engineer with more than 15 years of experience, including 12 years delivering "
            "financial-services and regulatory-reporting solutions. Experienced with the Axiom platform, "
            "SQL, Python, ETL workflows, data transformation, testing, and AWS cloud migration. Combines "
            "hands-on engineering with IT audit, stakeholder communication, cross-functional delivery, "
            "and client training across complex regulated environments."
        ),
        skills=SkillSet(
            hard_skills=["ETL Workflows", "Data Transformation", "Software Testing"],
            soft_skills=["Cross-functional Collaboration", "Stakeholder Communication"],
            tools_software=["Axiom regulatory reporting platform", "SQL", "Python", "AWS Cloud"],
            industry_knowledge=["Regulatory Reporting", "Financial Services", "IT Auditing"],
        ),
        bullet_proposals=bullet_proposals,
        evidence_matches=[
            EvidenceMatch(
                requirement_id="R1",
                status="supported",
                evidence_ids=["NAS-01", "NAS-03"],
                rationale="The candidate delivered regulatory reporting on the Axiom platform.",
            ),
            EvidenceMatch(
                requirement_id="R2",
                status="supported",
                evidence_ids=["NAS-04", "NAS-05"],
                rationale="The source resume documents SQL transformations and optimization.",
            ),
            EvidenceMatch(
                requirement_id="R3",
                status="supported",
                evidence_ids=["NAS-07", "CAP-02"],
                rationale="The source resume documents regression testing and collaboration.",
            ),
        ],
        unsupported_requirements=[],
        candidate_questions=[],
    )
