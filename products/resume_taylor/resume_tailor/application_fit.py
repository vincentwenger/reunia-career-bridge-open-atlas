from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from .models import CandidateAnswer, CandidateProfile, JobAnalysis, JobRequirement, TailoringProposal


class ApplicationOutcome(Protocol):
    status: str
    alignment_score: float | None
    interview_received: bool


_PRIORITY_WEIGHT = {
    "critical": 2.0,
    "important": 1.2,
    "secondary": 0.55,
}
_CATEGORY_WEIGHT = {
    "technical_skill": 1.15,
    "domain_knowledge": 1.10,
    "methodology": 0.90,
    "responsibility": 1.00,
    "leadership": 0.90,
    "qualification": 1.10,
}
_STATUS_VALUE = {
    "supported": 1.0,
    "partial": 0.55,
    "unsupported": 0.0,
}
_PRIORITY_ORDER = {"critical": 0, "important": 1, "secondary": 2}
_STATUS_ORDER = {"unsupported": 0, "partial": 1, "supported": 2}
_HARD_CONSTRAINT_PATTERNS = (
    r"\bsecurity clearance\b",
    r"\bactive clearance\b",
    r"\bclearance required\b",
    r"\bmust be (?:a )?(?:u\.s\.? |united states )?citizen\b",
    r"\bcitizenship required\b",
    r"\bwork authorization required\b",
    r"\bmust be authorized to work\b",
    r"\bno (?:visa )?sponsorship\b",
    r"\blicen[cs]e required\b",
    r"\brequired licen[cs]e\b",
    r"\bcertification required\b",
    r"\brequired certification\b",
    r"\bmust reside\b",
    r"\blocal candidates only\b",
)


@dataclass(frozen=True)
class ApplicationFitAssessment:
    score: float
    recommendation: str
    recommendation_key: str
    recommendation_summary: str
    interview_label: str
    interview_low: int
    interview_high: int
    confidence: str
    confidence_summary: str
    stage_label: str
    strengths: tuple[str, ...]
    obstacles: tuple[str, ...]
    improvements: tuple[str, ...]
    supported_count: int
    partial_count: int
    unsupported_count: int
    critical_supported: int
    critical_total: int
    hard_blocker_count: int
    history_calibrated: bool
    history_note: str

    @property
    def interview_range(self) -> str:
        return f"{self.interview_low}–{self.interview_high}%"


@dataclass(frozen=True)
class _RequirementAssessment:
    requirement: JobRequirement
    status: str
    hard_constraint: bool

    @property
    def weight(self) -> float:
        return _PRIORITY_WEIGHT[self.requirement.priority] * _CATEGORY_WEIGHT[
            self.requirement.category
        ]


def _is_hard_constraint(requirement: JobRequirement) -> bool:
    if requirement.priority != "critical":
        return False
    text = requirement.requirement.casefold()
    return any(re.search(pattern, text) for pattern in _HARD_CONSTRAINT_PATTERNS)


def _status_lookup(
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    profile: CandidateProfile,
    candidate_answers: Iterable[CandidateAnswer],
) -> dict[str, str]:
    valid_ids = {item.id for item in analysis.requirements}
    statuses = {
        item.requirement_id: item.status
        for item in proposal.evidence_matches
        if item.requirement_id in valid_ids
    }

    # Candidate-confirmed supplemental evidence is part of the source evidence,
    # not wording invented by the tailored resume. It can therefore strengthen
    # the application decision without allowing resume rewriting to inflate it.
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


def _baseline_interview_range(score: float) -> tuple[int, int]:
    # These intentionally broad ranges are a directional baseline, not a claim
    # about the employer, applicant pool, referrals, or hiring conditions.
    if score >= 85:
        return 30, 50
    if score >= 75:
        return 20, 35
    if score >= 65:
        return 12, 25
    if score >= 52:
        return 6, 15
    return 2, 8


def _potential_label(low: int, high: int) -> str:
    midpoint = (low + high) / 2
    if midpoint >= 35:
        return "High"
    if midpoint >= 24:
        return "Moderate to high"
    if midpoint >= 14:
        return "Moderate"
    if midpoint >= 7:
        return "Low to moderate"
    return "Low"


def _calibrated_interview_range(
    score: float,
    records: Iterable[ApplicationOutcome],
) -> tuple[int, int, bool, str]:
    baseline_low, baseline_high = _baseline_interview_range(score)
    resolved = [
        item
        for item in records
        if item.alignment_score is not None
        and (
            item.status in {"interview", "offer", "rejected", "withdrawn"}
            or item.interview_received
        )
    ]
    comparable = [
        item for item in resolved if abs(float(item.alignment_score) - score) <= 12
    ]
    if len(comparable) < 5:
        return (
            baseline_low,
            baseline_high,
            False,
            "Fit-based estimate. Track at least five resolved applications with fit scores to personalize this range.",
        )

    observed_rate = 100 * sum(item.interview_received for item in comparable) / len(
        comparable
    )
    baseline_midpoint = (baseline_low + baseline_high) / 2
    history_weight = min(0.75, len(comparable) / (len(comparable) + 6))
    blended_midpoint = (
        baseline_midpoint * (1 - history_weight) + observed_rate * history_weight
    )
    spread = max(8, 16 - min(len(comparable), 8))
    low = max(1, round(blended_midpoint - spread))
    high = min(90, round(blended_midpoint + spread))
    if high <= low:
        high = min(90, low + 5)
    return (
        low,
        high,
        True,
        f"Personalized with {len(comparable)} resolved applications having similar saved fit scores.",
    )


def _short_requirement(requirement: str, *, limit: int = 125) -> str:
    value = " ".join(requirement.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _recommendation(score: float, hard_blockers: int) -> tuple[str, str, str]:
    if hard_blockers:
        return (
            "Low match — Probably not worth your time",
            "low",
            "A critical eligibility requirement appears unsupported. Verify it before investing time in the application.",
        )
    if score >= 80:
        return (
            "Strong match — Apply",
            "strong",
            "Your verified experience covers most of the role's highest-priority requirements.",
        )
    if score >= 68:
        return (
            "Good match — Worth applying",
            "good",
            "You have a credible foundation for the role, with a limited number of meaningful gaps.",
        )
    if score >= 52:
        return (
            "Stretch opportunity — Apply selectively",
            "stretch",
            "The role is possible, but the decision should depend on whether the remaining gaps are flexible or confirmable.",
        )
    return (
        "Low match — Probably not worth your time",
        "low",
        "Too many important requirements currently lack verified evidence for this application to be an efficient use of time.",
    )


def _confidence(
    *,
    requirements: int,
    matched_statuses: int,
    partial_count: int,
    confirmation_complete: bool,
) -> tuple[str, str]:
    if requirements == 0:
        return "Low", "No structured job requirements were available to score."
    coverage = matched_statuses / requirements
    partial_ratio = partial_count / requirements
    if confirmation_complete and requirements >= 5 and coverage >= 0.9 and partial_ratio <= 0.2:
        return (
            "High",
            "The job requirements were mapped to source evidence and the candidate confirmation step is complete.",
        )
    if requirements >= 4 and coverage >= 0.7:
        suffix = (
            " Candidate confirmation is complete."
            if confirmation_complete
            else " Confirmation answers may still change the result."
        )
        return "Medium", "Most requirements have an evidence decision." + suffix
    return (
        "Low",
        "Several requirements lack a complete evidence decision, so treat this as an early directional result.",
    )


def build_application_fit_assessment(
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    profile: CandidateProfile,
    *,
    candidate_answers: Iterable[CandidateAnswer] = (),
    application_records: Iterable[ApplicationOutcome] = (),
    confirmation_complete: bool = False,
) -> ApplicationFitAssessment:
    statuses = _status_lookup(analysis, proposal, profile, candidate_answers)
    assessed = [
        _RequirementAssessment(
            requirement=requirement,
            status=statuses.get(requirement.id, "unsupported"),
            hard_constraint=_is_hard_constraint(requirement),
        )
        for requirement in analysis.requirements
    ]

    total_weight = sum(item.weight for item in assessed)
    earned_weight = sum(
        item.weight * _STATUS_VALUE.get(item.status, 0.0) for item in assessed
    )
    requirement_score = 100 * earned_weight / total_weight if total_weight else 0.0

    critical = [item for item in assessed if item.requirement.priority == "critical"]
    critical_weight = sum(item.weight for item in critical)
    critical_earned = sum(
        item.weight * _STATUS_VALUE.get(item.status, 0.0) for item in critical
    )
    critical_score = (
        100 * critical_earned / critical_weight if critical_weight else requirement_score
    )

    score = requirement_score * 0.82 + critical_score * 0.18
    hard_blockers = [
        item
        for item in assessed
        if item.hard_constraint and item.status != "supported"
    ]
    if hard_blockers:
        score = min(score, 45.0)
    elif len(critical) >= 2 and critical_score < 50:
        score = min(score, 59.0)
    score = round(max(0.0, min(100.0, score)), 1)

    recommendation, recommendation_key, recommendation_summary = _recommendation(
        score, len(hard_blockers)
    )
    interview_low, interview_high, history_calibrated, history_note = (
        _calibrated_interview_range(score, application_records)
    )

    supported = [item for item in assessed if item.status == "supported"]
    partial = [item for item in assessed if item.status == "partial"]
    unsupported = [item for item in assessed if item.status == "unsupported"]

    strengths = tuple(
        _short_requirement(item.requirement.requirement)
        for item in sorted(
            supported,
            key=lambda item: (
                _PRIORITY_ORDER[item.requirement.priority],
                -item.weight,
                item.requirement.requirement.casefold(),
            ),
        )[:3]
    )
    obstacle_candidates = sorted(
        [item for item in assessed if item.status != "supported"],
        key=lambda item: (
            0 if item.hard_constraint else 1,
            _PRIORITY_ORDER[item.requirement.priority],
            _STATUS_ORDER[item.status],
            item.requirement.requirement.casefold(),
        ),
    )
    obstacles = tuple(
        (
            "Critical eligibility gap: "
            if item.hard_constraint
            else "Partial evidence: "
            if item.status == "partial"
            else "Missing evidence: "
        )
        + _short_requirement(item.requirement.requirement)
        for item in obstacle_candidates[:3]
    )

    improvements_list: list[str] = []
    for item in obstacle_candidates[:2]:
        requirement = _short_requirement(item.requirement.requirement, limit=105)
        if item.hard_constraint:
            improvements_list.append(
                f"Verify the mandatory requirement before applying: {requirement}"
            )
        elif item.status == "partial":
            improvements_list.append(
                f"Add a specific, truthful example or measurable result for: {requirement}"
            )
        else:
            improvements_list.append(
                f"Confirm whether you have relevant experience for: {requirement}"
            )
    if len(improvements_list) < 3:
        improvements_list.append(
            "Tailor the resume around the strongest supported requirements rather than adding unsupported keywords."
        )

    matched_statuses = sum(requirement.id in statuses for requirement in analysis.requirements)
    confidence, confidence_summary = _confidence(
        requirements=len(analysis.requirements),
        matched_statuses=matched_statuses,
        partial_count=len(partial),
        confirmation_complete=confirmation_complete,
    )

    if confirmation_complete:
        stage_label = "Updated after experience confirmation"
    elif candidate_answers:
        stage_label = "Updated with confirmed answers · Final questions pending"
    else:
        stage_label = "Preliminary assessment"

    return ApplicationFitAssessment(
        score=score,
        recommendation=recommendation,
        recommendation_key=recommendation_key,
        recommendation_summary=recommendation_summary,
        interview_label=_potential_label(interview_low, interview_high),
        interview_low=interview_low,
        interview_high=interview_high,
        confidence=confidence,
        confidence_summary=confidence_summary,
        stage_label=stage_label,
        strengths=strengths,
        obstacles=obstacles,
        improvements=tuple(improvements_list[:3]),
        supported_count=len(supported),
        partial_count=len(partial),
        unsupported_count=len(unsupported),
        critical_supported=sum(item.status == "supported" for item in critical),
        critical_total=len(critical),
        hard_blocker_count=len(hard_blockers),
        history_calibrated=history_calibrated,
        history_note=history_note,
    )
