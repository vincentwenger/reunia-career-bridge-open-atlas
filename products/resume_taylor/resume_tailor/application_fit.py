from __future__ import annotations

from typing import Iterable

from career_bridge.domain.fit_scoring import (
    ApplicationFitAssessment,
    ApplicationOutcome,
    RequirementStatus,
    build_requirement_fit_assessment,
)

from .models import CandidateAnswer, CandidateProfile, JobAnalysis, TailoringProposal


def build_requirement_statuses(
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    profile: CandidateProfile,
    candidate_answers: Iterable[CandidateAnswer] = (),
) -> dict[str, RequirementStatus]:
    """Translate Resume Workflow evidence into the shared scorer contract."""

    valid_ids = {item.id for item in analysis.requirements}
    statuses: dict[str, RequirementStatus] = {
        item.requirement_id: item.status
        for item in proposal.evidence_matches
        if item.requirement_id in valid_ids
    }

    # Candidate-confirmed supplemental evidence is part of source evidence,
    # not wording invented by the tailored resume.
    for evidence in profile.supplemental_evidence:
        if not evidence.statement.strip():
            continue
        for requirement_id in evidence.requirement_ids:
            if requirement_id in valid_ids:
                statuses[requirement_id] = "supported"

    for answer in candidate_answers:
        requirement_id = answer.requirement_id
        if not requirement_id or requirement_id not in valid_ids:
            continue
        if answer.yes_no is False:
            statuses[requirement_id] = "unsupported"
        elif answer.yes_no is True or answer.text.strip():
            statuses[requirement_id] = "supported"

    return statuses


def build_application_fit_assessment(
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    profile: CandidateProfile,
    *,
    candidate_answers: Iterable[CandidateAnswer] = (),
    application_records: Iterable[ApplicationOutcome] = (),
    confirmation_complete: bool = False,
) -> ApplicationFitAssessment:
    """Compatibility adapter for the Resume Workflow.

    The workflow still derives statuses from TailoringProposal evidence and
    candidate confirmations, while the scoring policy itself is shared with
    Job Discovery through ``build_requirement_fit_assessment``.
    """

    answers = tuple(candidate_answers)
    statuses = build_requirement_statuses(analysis, proposal, profile, answers)
    if confirmation_complete:
        stage_label = "Updated after experience confirmation"
    elif answers:
        stage_label = "Updated with confirmed answers · Final questions pending"
    else:
        stage_label = "Preliminary assessment"

    return build_requirement_fit_assessment(
        analysis.requirements,
        statuses,
        application_records=application_records,
        confirmation_complete=confirmation_complete,
        stage_label=stage_label,
    )


__all__ = [
    "ApplicationFitAssessment",
    "ApplicationOutcome",
    "RequirementStatus",
    "build_application_fit_assessment",
    "build_requirement_fit_assessment",
    "build_requirement_statuses",
]
