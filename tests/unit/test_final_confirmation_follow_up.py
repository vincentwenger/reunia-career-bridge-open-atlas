from __future__ import annotations

from products.resume_taylor.resume_tailor.confirmation_followup import (
    apply_final_follow_up_answers_locally,
)
from products.resume_taylor.resume_tailor.models import (
    BulletProposal,
    CandidateAnswer,
    CandidateProfile,
    CandidateQuestion,
    ContactInfo,
    EvidenceMatch,
    Experience,
    ResumeBullet,
    SkillSet,
    TailoringProposal,
    VerifiedSkills,
)


def _profile() -> CandidateProfile:
    return CandidateProfile(
        name="Alex Morgan",
        contact=ContactInfo(
            location="Portland, OR",
            phone="",
            email="alex.morgan@example.com",
        ),
        current_summary="Software engineer with verified production support experience.",
        skills=VerifiedSkills(hard_skills=["SQL", "Python"]),
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
                        id="EXP-001-B09",
                        text=(
                            "Resolved 1000 client-reported technical issues for 20 clients "
                            "through structured JIRA workflows."
                        ),
                    )
                ],
            )
        ],
    )


def _proposal() -> TailoringProposal:
    return TailoringProposal(
        professional_summary="Owned all production incident response globally.",
        skills=SkillSet(hard_skills=["SQL", "Python", "Kubernetes"]),
        bullet_proposals=[
            BulletProposal(
                source_bullet_id="EXP-001-B09",
                include=True,
                proposed_text="Led all global production incident response for every client.",
                matched_requirement_ids=["R9", "R11"],
                evidence_note="Generated stronger wording.",
            )
        ],
        evidence_matches=[
            EvidenceMatch(
                requirement_id="R9",
                status="supported",
                evidence_ids=["EXP-001-B09"],
                rationale="Issue resolution evidence.",
            )
        ],
    )


def test_declined_final_bullet_claim_restores_verified_source_wording() -> None:
    question = CandidateQuestion(
        id="FQ1-1",
        requirement_id="R9",
        source_id="EXP-001-B09",
        question="Is the full transformed bullet accurate?",
        answer_type="yes_no_with_details",
    )
    answer = CandidateAnswer(
        question_id=question.id,
        answer_type=question.answer_type,
        yes_no=False,
    )

    updated = apply_final_follow_up_answers_locally(
        _profile(), _proposal(), [question], [answer]
    )

    bullet = updated.bullet_proposals[0]
    assert bullet.proposed_text.startswith("Resolved 1000 client-reported")
    assert "verified source wording was restored" in bullet.evidence_note


def test_confirmed_final_claim_is_left_for_confirmed_profile_evidence() -> None:
    question = CandidateQuestion(
        id="FQ1-1",
        requirement_id="R9",
        source_id="EXP-001-B09",
        question="Is the full transformed bullet accurate?",
        answer_type="yes_no_with_details",
    )
    answer = CandidateAnswer(
        question_id=question.id,
        answer_type=question.answer_type,
        yes_no=True,
        text="I personally led the production issue workflow for the cited clients.",
        experience_id="EXP-001",
    )

    updated = apply_final_follow_up_answers_locally(
        _profile(), _proposal(), [question], [answer]
    )

    assert updated.bullet_proposals[0].proposed_text.startswith(
        "Led all global production incident response"
    )


def test_declined_requirement_is_removed_from_selection_evidence() -> None:
    question = CandidateQuestion(
        id="FQ1-2",
        requirement_id="R9",
        source_id="R9",
        question="Does the resume fully support this requirement?",
        answer_type="yes_no_with_details",
    )
    answer = CandidateAnswer(
        question_id=question.id,
        answer_type=question.answer_type,
        yes_no=False,
    )

    updated = apply_final_follow_up_answers_locally(
        _profile(), _proposal(), [question], [answer]
    )

    match = updated.evidence_matches[0]
    assert match.status == "unsupported"
    assert match.evidence_ids == []
    assert "R9" not in updated.bullet_proposals[0].matched_requirement_ids
