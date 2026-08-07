from __future__ import annotations

from typing import Any, Iterable

from .web_state import WorkflowState


def _normalized_unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(str(raw or "").split()).strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _active_proposal(state: WorkflowState):
    return (
        state.final_proposal
        or state.draft_proposal
        or state.provisional_proposal
        or state.initial_evidence_proposal
        or state.initial_report_proposal
    )


def build_workflow_impact_snapshot(state: WorkflowState) -> dict[str, Any]:
    """Build evidence-backed social-impact measurements for one application.

    Counts are deliberately derived from saved workflow artifacts. Missing workflow
    stages remain unmeasured rather than being estimated or filled with demo values.
    """

    proposal = _active_proposal(state)
    assessment = (
        proposal.career_translation_assessment
        if proposal is not None
        else None
    )
    findings = list(getattr(assessment, "findings", []) or [])

    credentials = _normalized_unique(
        finding.source_text
        for finding in findings
        if getattr(finding, "category", "") == "credential_explanation"
    )
    terminology = _normalized_unique(
        finding.source_text
        for finding in findings
        if getattr(finding, "category", "")
        in {"job_title_translation", "regional_terminology"}
    )

    unsupported_labels: list[str] = []
    if proposal is not None:
        if proposal.unsupported_requirements:
            unsupported_labels.extend(proposal.unsupported_requirements)
        else:
            unsupported_labels.extend(
                match.requirement_id
                for match in proposal.evidence_matches
                if match.status == "unsupported"
            )
    unsupported_labels.extend(
        finding.source_text
        for finding in findings
        if getattr(finding, "disposition", "") == "unsupported_claim"
    )
    unsupported = _normalized_unique(unsupported_labels)

    source_evidence_ids = {
        item.id for item in state.source_profile.supplemental_evidence
    }
    confirmed_profile = state.confirmed_profile or state.source_profile
    recovered_evidence = _normalized_unique(
        item.statement
        for item in confirmed_profile.supplemental_evidence
        if item.id not in source_evidence_ids
    )
    recovered_findings = _normalized_unique(
        finding.source_text
        for finding in findings
        if getattr(finding, "category", "")
        in {"hidden_accomplishment", "transferable_skill"}
        and getattr(finding, "disposition", "")
        in {"confirmed_experience", "reasonable_rephrasing"}
    )
    recovered = _normalized_unique([*recovered_evidence, *recovered_findings])

    baseline_report = state.initial_report
    current_report = (
        state.final_report
        or state.optimization_report_after
        or state.updated_report
    )
    baseline_alignment = (
        baseline_report.job_match_score() if baseline_report is not None else None
    )
    current_alignment = (
        current_report.job_match_score() if current_report is not None else None
    )
    alignment_improvement = (
        round(current_alignment - baseline_alignment, 1)
        if baseline_alignment is not None and current_alignment is not None
        else None
    )

    return {
        "credentials_identified": len(credentials),
        "credentials": credentials,
        "terminology_clarified": len(terminology),
        "terminology": terminology,
        "unsupported_claims_prevented": len(unsupported),
        "unsupported_claims": unsupported,
        "relevant_experience_recovered": len(recovered),
        "recovered_experience": recovered,
        "baseline_alignment_score": baseline_alignment,
        "current_alignment_score": current_alignment,
        "alignment_improvement": alignment_improvement,
        "verified_resume_ready": bool(
            state.final_resume_bytes and state.final_proposal is not None
        ),
        "workflow_stage": state.workflow_stage,
        "measured": any(
            (
                credentials,
                terminology,
                unsupported,
                recovered,
                baseline_alignment is not None,
                current_alignment is not None,
            )
        ),
    }
