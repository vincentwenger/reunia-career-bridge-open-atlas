from __future__ import annotations

from types import SimpleNamespace

from resume_tailor.application_fit import build_application_fit_assessment
from resume_tailor.models import CandidateAnswer, EvidenceMatch, JobAnalysis, JobRequirement


def test_strong_supported_fit_recommends_applying(profile, analysis, proposal):
    assessment = build_application_fit_assessment(
        analysis,
        proposal,
        profile,
        confirmation_complete=True,
    )

    assert assessment.score >= 80
    assert assessment.recommendation == "Strong match — Apply"
    assert assessment.supported_count == 3
    assert assessment.unsupported_count == 0
    assert assessment.stage_label == "Updated after experience confirmation"
    assert assessment.interview_low < assessment.interview_high


def test_candidate_confirmation_updates_source_evidence_without_reading_rewritten_text(
    profile, analysis, proposal
):
    changed = proposal.model_copy(deep=True)
    changed.evidence_matches = [
        EvidenceMatch(
            requirement_id="R1",
            status="supported",
            evidence_ids=["NAS-01"],
            rationale="Supported.",
        ),
        EvidenceMatch(
            requirement_id="R2",
            status="unsupported",
            evidence_ids=[],
            rationale="Not shown yet.",
        ),
        EvidenceMatch(
            requirement_id="R3",
            status="supported",
            evidence_ids=["NAS-07"],
            rationale="Supported.",
        ),
    ]
    before = build_application_fit_assessment(analysis, changed, profile)
    confirmed = build_application_fit_assessment(
        analysis,
        changed,
        profile,
        candidate_answers=[
            CandidateAnswer(
                question_id="Q-R2",
                requirement_id="R2",
                answer_type="yes_no_with_details",
                yes_no=True,
                text="Used SQL for transformation and performance tuning.",
            )
        ],
        confirmation_complete=True,
    )

    assert confirmed.score > before.score
    assert confirmed.supported_count == 3
    assert confirmed.stage_label == "Updated after experience confirmation"


def test_unsupported_mandatory_eligibility_requirement_caps_recommendation(
    profile, proposal
):
    analysis = JobAnalysis(
        target_title="Cleared Engineer",
        requirements=[
            JobRequirement(
                id="R1",
                category="technical_skill",
                priority="critical",
                requirement="Build Python services",
                keywords=["Python"],
            ),
            JobRequirement(
                id="R2",
                category="qualification",
                priority="critical",
                requirement="Active security clearance required",
                keywords=["security clearance"],
            ),
        ],
    )
    changed = proposal.model_copy(deep=True)
    changed.evidence_matches = [
        EvidenceMatch(
            requirement_id="R1",
            status="supported",
            evidence_ids=["NAS-01"],
            rationale="Supported.",
        ),
        EvidenceMatch(
            requirement_id="R2",
            status="unsupported",
            evidence_ids=[],
            rationale="No clearance evidence.",
        ),
    ]

    assessment = build_application_fit_assessment(analysis, changed, profile)

    assert assessment.score <= 45
    assert assessment.recommendation_key == "low"
    assert assessment.hard_blocker_count == 1
    assert assessment.obstacles[0].startswith("Critical eligibility gap:")


def test_interview_range_uses_similar_resolved_application_history(
    profile, analysis, proposal
):
    records = [
        SimpleNamespace(
            status="interview" if index < 4 else "rejected",
            alignment_score=99 - index,
            interview_received=index < 4,
        )
        for index in range(6)
    ]

    assessment = build_application_fit_assessment(
        analysis,
        proposal,
        profile,
        application_records=records,
        confirmation_complete=True,
    )

    assert assessment.history_calibrated is True
    assert "6 resolved applications" in assessment.history_note
