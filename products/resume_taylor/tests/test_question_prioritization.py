from __future__ import annotations

from resume_tailor.models import CandidateQuestion, EvidenceMatch, JobAnalysis, JobRequirement
from resume_tailor.question_prioritization import (
    MAX_INITIAL_CONFIRMATION_QUESTIONS,
    prioritize_candidate_questions,
)


def test_initial_questions_keep_only_material_unresolved_requirements(proposal):
    analysis = JobAnalysis(
        target_title="Engineer",
        requirements=[
            JobRequirement(id="R1", category="technical_skill", priority="critical", requirement="Python", keywords=["Python"]),
            JobRequirement(id="R2", category="leadership", priority="important", requirement="Mentoring", keywords=["mentoring"]),
            JobRequirement(id="R3", category="methodology", priority="secondary", requirement="Scrum ceremonies", keywords=["Scrum"]),
            JobRequirement(id="R4", category="domain_knowledge", priority="important", requirement="Banking", keywords=["banking"]),
        ],
    )
    proposal.evidence_matches = [
        EvidenceMatch(requirement_id="R1", status="partial", evidence_ids=[], rationale="partial"),
        EvidenceMatch(requirement_id="R2", status="unsupported", evidence_ids=[], rationale="missing"),
        EvidenceMatch(requirement_id="R3", status="unsupported", evidence_ids=[], rationale="missing"),
        EvidenceMatch(requirement_id="R4", status="supported", evidence_ids=["NAS-01"], rationale="supported"),
    ]
    proposal.candidate_questions = [
        CandidateQuestion(id="Q1", requirement_id="R1", question="Python detail?", answer_type="yes_no_with_details"),
        CandidateQuestion(id="Q2", requirement_id="R2", question="Mentoring detail?", answer_type="yes_no_with_details"),
        CandidateQuestion(id="Q3", requirement_id="R3", question="Scrum detail?", answer_type="yes_no_with_details"),
        CandidateQuestion(id="Q4", requirement_id="R4", question="Banking detail?", answer_type="yes_no_with_details"),
        CandidateQuestion(id="Q5", requirement_id="", question="Untied detail?", answer_type="long_text"),
    ]

    updated = prioritize_candidate_questions(proposal, analysis)

    assert [item.id for item in updated.candidate_questions] == ["Q1", "Q2"]
    assert len(updated.candidate_questions) <= MAX_INITIAL_CONFIRMATION_QUESTIONS


def test_initial_questions_are_deduplicated_and_capped(proposal):
    requirements = [
        JobRequirement(id=f"R{index}", category="technical_skill", priority="critical", requirement=f"Skill {index}", keywords=[])
        for index in range(1, 9)
    ]
    analysis = JobAnalysis(target_title="Engineer", requirements=requirements)
    proposal.evidence_matches = [
        EvidenceMatch(requirement_id=item.id, status="unsupported", evidence_ids=[], rationale="missing")
        for item in requirements
    ]
    proposal.candidate_questions = [
        CandidateQuestion(id=f"Q{index}", requirement_id=f"R{index}", question=f"Do you have Skill {index}?", answer_type="yes_no_with_details")
        for index in range(1, 9)
    ]
    proposal.candidate_questions.insert(
        1,
        CandidateQuestion(id="QD", requirement_id="R1", question="Do you have Skill 1?", answer_type="yes_no_with_details"),
    )

    updated = prioritize_candidate_questions(proposal, analysis)

    assert len(updated.candidate_questions) == MAX_INITIAL_CONFIRMATION_QUESTIONS == 6
    assert [item.requirement_id for item in updated.candidate_questions].count("R1") == 1
