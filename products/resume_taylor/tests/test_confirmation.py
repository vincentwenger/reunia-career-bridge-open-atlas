from __future__ import annotations

import pytest
from pydantic import ValidationError

from resume_tailor.confirmation import (
    build_profile_with_candidate_answers,
    validate_candidate_answers,
)
from resume_tailor.models import CandidateAnswer, CandidateQuestion, SupplementalEvidence, TailoringProposal
from resume_tailor.validation import validate_proposal


def test_yes_no_with_details_requires_details():
    question = CandidateQuestion(
        id="Q1",
        requirement_id="R1",
        question="Have you used Axiom UDFs?",
        answer_type="yes_no_with_details",
        details_prompt="Describe how you used them.",
    )
    answer = CandidateAnswer(
        question_id="Q1",
        requirement_id="R1",
        answer_type="yes_no_with_details",
        yes_no=True,
    )

    errors = validate_candidate_answers([question], [answer])

    assert any("Add one brief detail" in error for error in errors)


def test_no_answer_does_not_become_candidate_evidence(profile, analysis):
    question = CandidateQuestion(
        id="Q1",
        requirement_id="R1",
        question="Have you used Axiom UDFs?",
        answer_type="yes_no",
    )
    answer = CandidateAnswer(
        question_id="Q1",
        requirement_id="R1",
        answer_type="yes_no",
        yes_no=False,
    )

    updated = build_profile_with_candidate_answers(profile, analysis, [question], [answer])

    assert updated.supplemental_evidence == []


def test_affirmative_detail_becomes_traceable_supplemental_evidence(profile, analysis):
    question = CandidateQuestion(
        id="Q1",
        requirement_id="R2",
        question="Describe your SQL optimization experience.",
        answer_type="long_text",
    )
    answer = CandidateAnswer(
        question_id="Q1",
        requirement_id="R2",
        answer_type="long_text",
        text="Optimized SQL transformations for a regulatory reporting data pipeline.",
        experience_id="nasdaq",
    )

    updated = build_profile_with_candidate_answers(profile, analysis, [question], [answer])

    evidence = updated.supplemental_evidence[0]
    assert evidence.id == "CONF-Q1"
    assert evidence.requirement_ids == ["R2"]
    assert evidence.statement.startswith("Optimized SQL")
    assert evidence.experience_id == "nasdaq"
    assert evidence.source_bullet_id == "NAS-CONF-01"
    assert updated.experience_lookup()["nasdaq"].bullets[-1].id == "NAS-CONF-01"
    assert updated.experience_lookup()["nasdaq"].bullets[-1].text.startswith("Optimized SQL")
    assert "SQL" in evidence.verified_skills
    assert "data transformation" in evidence.verified_skills


def test_candidate_questions_require_structured_objects(proposal):
    payload = proposal.model_dump()
    payload["candidate_questions"] = ["Have you administered Oracle RAC environments?"]

    with pytest.raises(ValidationError):
        TailoringProposal.model_validate(payload)


def test_confirmed_skill_and_number_are_allowed_by_local_validation(profile, analysis, proposal):
    updated_profile = profile.model_copy(deep=True)
    updated_profile.supplemental_evidence.append(
        SupplementalEvidence(
            id="CONF-Q1",
            statement="Used Oracle RAC for 3 production environments.",
            requirement_ids=["R2"],
            verified_skills=["Oracle RAC"],
        )
    )
    proposal.skills.tools_software.append("Oracle RAC")
    proposal.bullet_proposals[0].proposed_text += " Supported 3 production environments."

    issues = validate_proposal(updated_profile, analysis, proposal)

    assert not any("Oracle RAC" in issue.issue and issue.severity == "blocking" for issue in issues)
    assert not any("introduces new number" in issue.issue for issue in issues)


def test_text_question_explicit_no_experience_is_complete_and_not_evidence(profile, analysis):
    question = CandidateQuestion(
        id="Q-TEXT",
        requirement_id="R2",
        question="Describe your Kubernetes experience.",
        answer_type="long_text",
        required=True,
    )
    answer = CandidateAnswer(
        question_id="Q-TEXT",
        requirement_id="R2",
        answer_type="long_text",
        yes_no=False,
        text="",
    )

    assert validate_candidate_answers([question], [answer]) == []

    updated = build_profile_with_candidate_answers(profile, analysis, [question], [answer])
    assert updated.supplemental_evidence == []


def test_required_text_question_explains_no_experience_alternative():
    question = CandidateQuestion(
        id="Q-TEXT",
        question="Describe your Kubernetes experience.",
        answer_type="long_text",
        required=True,
    )
    answer = CandidateAnswer(
        question_id="Q-TEXT",
        answer_type="long_text",
        text="",
    )

    errors = validate_candidate_answers([question], [answer])

    assert errors == ["Q-TEXT: Enter an answer or mark it as no relevant experience."]
