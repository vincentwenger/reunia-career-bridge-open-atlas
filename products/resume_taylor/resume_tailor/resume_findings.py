from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from .models import CareerTranslationAssessment, StrictModel, TailoringProposal
from .resume_report import ResumeReport, build_evidence_gap_report
from .web_state import WorkflowState, normalize_job_description


FindingStatus = Literal["partial", "unsupported", "no_decision"]
ReportFindingStatus = Literal["warning", "fail"]
ClaimDisposition = Literal["excluded", "questioned"]


class ResumeRequirementFinding(StrictModel):
    requirement_id: str
    requirement: str
    category: str
    priority: str
    evidence_status: FindingStatus
    evidence_ids: list[str] = Field(default_factory=list)
    appears_in_resume: bool
    rationale: str
    recommended_action: str


class ResumeReportFinding(StrictModel):
    report_stage: str
    section: str
    subsection: str
    label: str
    status: ReportFindingStatus
    detail: str


class ResumeClaimFinding(StrictModel):
    disposition: ClaimDisposition
    source_id: str = ""
    requirement_id: str = ""
    original_text: str = ""
    proposed_text: str = ""
    question: str = ""
    answer: str = ""
    rationale: str = ""
    matched_requirement_ids: list[str] = Field(default_factory=list)


class ResumeAlignmentChange(StrictModel):
    baseline_job_match_score: float | None = None
    current_job_match_score: float | None = None
    job_match_improvement: float | None = None
    baseline_overall_score: float | None = None
    current_overall_score: float | None = None
    overall_score_improvement: float | None = None


class ResumeFindingsSnapshot(StrictModel):
    schema_version: str = "1.0"
    captured_at: str
    source_stage: str
    target_company: str = ""
    target_role: str = ""
    application_context_fingerprint: str = ""
    unsupported_or_partial_requirements: list[ResumeRequirementFinding] = Field(
        default_factory=list
    )
    evidence_review_warnings: list[ResumeReportFinding] = Field(default_factory=list)
    career_translation_assessment: CareerTranslationAssessment = Field(
        default_factory=CareerTranslationAssessment
    )
    resume_report_weaknesses: list[ResumeReportFinding] = Field(default_factory=list)
    alignment_changes: ResumeAlignmentChange = Field(default_factory=ResumeAlignmentChange)
    excluded_or_questioned_claims: list[ResumeClaimFinding] = Field(default_factory=list)

    def has_findings(self) -> bool:
        assessment = self.career_translation_assessment
        alignment = self.alignment_changes
        return any(
            (
                self.unsupported_or_partial_requirements,
                self.evidence_review_warnings,
                assessment.summary.strip(),
                assessment.findings,
                self.resume_report_weaknesses,
                self.excluded_or_questioned_claims,
                alignment.baseline_job_match_score is not None,
                alignment.current_job_match_score is not None,
                alignment.baseline_overall_score is not None,
                alignment.current_overall_score is not None,
            )
        )

    def prompt_text(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )


def resume_findings_fingerprint(snapshot: ResumeFindingsSnapshot) -> str:
    payload = snapshot.model_dump(mode="json", exclude={"captured_at"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _current_proposal(state: WorkflowState) -> TailoringProposal | None:
    return (
        state.final_report_proposal
        or state.final_proposal
        or state.draft_proposal
        or state.provisional_proposal
        or state.initial_evidence_proposal
        or state.initial_report_proposal
    )


def _proposal_stage(state: WorkflowState, proposal: TailoringProposal | None) -> str:
    if proposal is None:
        return state.workflow_stage or "setup"
    if proposal is state.final_report_proposal:
        return "evidence_reviewed_final_resume"
    if proposal is state.final_proposal:
        return "final_resume"
    if proposal is state.draft_proposal:
        return "tailored_resume"
    if proposal is state.provisional_proposal:
        return "confirmed_evidence"
    if proposal is state.initial_evidence_proposal:
        return "initial_evidence_review"
    return "initial_resume_report"


def _current_report(state: WorkflowState) -> tuple[str, ResumeReport | None]:
    if state.final_report is not None:
        return "final_report", state.final_report
    if state.optimization_report_after is not None:
        return "optimized_report", state.optimization_report_after
    if state.updated_report is not None:
        return "tailored_report", state.updated_report
    if state.initial_report is not None:
        return "initial_report", state.initial_report
    return "", None


def _report_findings(
    report: ResumeReport | None,
    *,
    report_stage: str,
    evidence_only: bool = False,
    limit: int = 40,
) -> list[ResumeReportFinding]:
    if report is None:
        return []
    findings: list[ResumeReportFinding] = []
    for section in report.sections():
        if evidence_only and section.name != "Evidence & Gaps":
            continue
        if not evidence_only and section.name == "Evidence & Gaps":
            continue
        for subsection in section.subsections:
            for check in subsection.checks:
                if check.status not in {"warning", "fail"}:
                    continue
                findings.append(
                    ResumeReportFinding(
                        report_stage=report_stage,
                        section=section.name,
                        subsection=subsection.name,
                        label=check.label,
                        status=check.status,
                        detail=check.detail,
                    )
                )
    findings.sort(key=lambda item: (item.status != "fail", item.section, item.label))
    return findings[:limit]


def _answer_text(answer) -> str:
    if answer.yes_no is True:
        prefix = "Yes"
    elif answer.yes_no is False:
        prefix = "No"
    else:
        prefix = ""
    detail = " ".join((answer.text or "").split())
    return " — ".join(part for part in (prefix, detail) if part)


def build_resume_findings_snapshot(
    state: WorkflowState,
    *,
    company: str = "",
    role: str = "",
    job_description: str = "",
) -> ResumeFindingsSnapshot:
    proposal = _current_proposal(state)
    analysis = state.analysis or state.initial_report_analysis
    profile = state.final_report_profile or state.confirmed_profile or state.source_profile
    candidate_answers = (
        state.final_report_candidate_answers
        if proposal is state.final_report_proposal and state.final_report_candidate_answers
        else state.candidate_answers
    )

    requirement_findings: list[ResumeRequirementFinding] = []
    if proposal is not None and analysis is not None:
        _, rows = build_evidence_gap_report(
            profile,
            analysis,
            proposal,
            candidate_answers,
        )
        match_lookup = {item.requirement_id: item for item in proposal.evidence_matches}
        for row in rows:
            if row.evidence_status not in {"partial", "unsupported", "no decision"}:
                continue
            normalized_status = (
                row.evidence_status
                if row.evidence_status in {"partial", "unsupported"}
                else "no_decision"
            )
            match = match_lookup.get(row.requirement_id)
            requirement_findings.append(
                ResumeRequirementFinding(
                    requirement_id=row.requirement_id,
                    requirement=row.requirement,
                    category=row.category,
                    priority=row.priority,
                    evidence_status=normalized_status,
                    evidence_ids=list(match.evidence_ids) if match is not None else [],
                    appears_in_resume=row.appears_in_resume,
                    rationale=row.rationale,
                    recommended_action=row.recommended_action,
                )
            )

    report_stage, current_report = _current_report(state)
    evidence_warnings = _report_findings(
        current_report,
        report_stage=report_stage,
        evidence_only=True,
    )
    report_weaknesses = _report_findings(
        current_report,
        report_stage=report_stage,
        evidence_only=False,
    )

    initial_report = state.initial_report
    current_job_match = current_report.job_match_score() if current_report else None
    current_overall = current_report.overall_score() if current_report else None
    baseline_job_match = initial_report.job_match_score() if initial_report else None
    baseline_overall = initial_report.overall_score() if initial_report else None
    alignment_changes = ResumeAlignmentChange(
        baseline_job_match_score=baseline_job_match,
        current_job_match_score=current_job_match,
        job_match_improvement=(
            round(current_job_match - baseline_job_match, 1)
            if current_job_match is not None and baseline_job_match is not None
            else None
        ),
        baseline_overall_score=baseline_overall,
        current_overall_score=current_overall,
        overall_score_improvement=(
            round(current_overall - baseline_overall, 1)
            if current_overall is not None and baseline_overall is not None
            else None
        ),
    )

    claims: list[ResumeClaimFinding] = []
    if proposal is not None:
        source_lookup = profile.bullet_lookup()
        for bullet in proposal.bullet_proposals:
            if bullet.include:
                continue
            claims.append(
                ResumeClaimFinding(
                    disposition="excluded",
                    source_id=bullet.source_bullet_id,
                    original_text=source_lookup.get(bullet.source_bullet_id, ""),
                    proposed_text=bullet.proposed_text,
                    rationale=bullet.evidence_note,
                    matched_requirement_ids=list(bullet.matched_requirement_ids),
                )
            )

    question_sources: list[TailoringProposal] = []
    for candidate in (
        proposal,
        state.provisional_proposal,
        state.initial_evidence_proposal,
        state.initial_report_proposal,
    ):
        if candidate is not None and all(candidate is not existing for existing in question_sources):
            question_sources.append(candidate)
    answer_lookup = {answer.question_id: answer for answer in state.candidate_answers}
    seen_questions: set[str] = set()
    for question_source in question_sources:
        for question in question_source.candidate_questions:
            if question.id in seen_questions:
                continue
            seen_questions.add(question.id)
            answer = answer_lookup.get(question.id)
            claims.append(
                ResumeClaimFinding(
                    disposition="questioned",
                    source_id=question.source_id,
                    requirement_id=question.requirement_id,
                    question=question.question,
                    answer=_answer_text(answer) if answer is not None else "Not answered",
                    rationale=question.help_text or question.details_prompt,
                )
            )

    assessment = (
        proposal.career_translation_assessment.model_copy(deep=True)
        if proposal is not None
        else CareerTranslationAssessment()
    )

    effective_company = " ".join(
        (company or (analysis.target_company if analysis is not None else "")).split()
    )
    effective_role = " ".join(
        (role or state.target_title or (analysis.target_title if analysis is not None else "")).split()
    )
    effective_job_description = normalize_job_description(
        job_description or state.job_description
    )
    context_payload = {
        "company": effective_company,
        "role": effective_role,
        "job_description": effective_job_description,
    }
    application_context_fingerprint = hashlib.sha256(
        json.dumps(context_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return ResumeFindingsSnapshot(
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_stage=_proposal_stage(state, proposal),
        target_company=effective_company,
        target_role=effective_role,
        application_context_fingerprint=application_context_fingerprint,
        unsupported_or_partial_requirements=requirement_findings,
        evidence_review_warnings=evidence_warnings,
        career_translation_assessment=assessment,
        resume_report_weaknesses=report_weaknesses,
        alignment_changes=alignment_changes,
        excluded_or_questioned_claims=claims[:60],
    )
