from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module, module_exports

"""Evidence-gap report construction."""

def build_evidence_gap_report(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    candidate_answers: list[CandidateAnswer] | None = None,
) -> tuple[EvidenceGapSummary, list[EvidenceGapRow]]:
    """Build the scored evidence matrix displayed in initial and updated reports."""
    evidence_lookup = {item.requirement_id: item for item in proposal.evidence_matches}
    location_lookup = _evidence_location_lookup(profile)
    negative_requirement_ids = _negative_answer_requirement_ids(candidate_answers)
    rows: list[EvidenceGapRow] = []
    supported = 0
    partial = 0
    unsupported = 0

    for requirement in analysis.requirements:
        match = evidence_lookup.get(requirement.id)
        status = match.status if match else "no decision"
        represented = _requirement_is_represented(requirement, proposal)
        acknowledged_no = requirement.id in negative_requirement_ids
        report_status, score, _ = _evidence_requirement_result(
            status,
            represented,
            acknowledged_no,
        )
        if status == "supported":
            supported += 1
        elif status == "partial":
            partial += 1
        else:
            unsupported += 1

        evidence_locations = [
            location_lookup.get(evidence_id, evidence_id)
            for evidence_id in (match.evidence_ids if match else [])
        ]
        rows.append(
            EvidenceGapRow(
                requirement_id=requirement.id,
                priority=requirement.priority,
                category=requirement.category,
                requirement=requirement.requirement,
                evidence_status=status,
                appears_in_resume=represented,
                evidence_locations=evidence_locations,
                rationale=match.rationale if match else "No evidence decision was supplied.",
                recommended_action=_recommended_evidence_action(
                    status,
                    represented,
                    acknowledged_no,
                ),
                score=score,
                report_status=report_status,
            )
        )

    summary = EvidenceGapSummary(
        supported=supported,
        partial=partial,
        unsupported=unsupported,
        candidate_confirmations=(
            len(candidate_answers)
            if candidate_answers is not None
            else len(proposal.candidate_questions)
        ),
    )
    return summary, rows


def _evidence_gaps_section(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    candidate_answers: list[CandidateAnswer] | None,
) -> ReportSection:
    _, rows = build_evidence_gap_report(
        profile,
        analysis,
        proposal,
        candidate_answers,
    )
    checks: list[ReportCheck] = []
    for row in rows:
        _, _, score_detail = _evidence_requirement_result(
            row.evidence_status,
            row.appears_in_resume,
            row.requirement_id in _negative_answer_requirement_ids(candidate_answers),
        )
        checks.append(
            ReportCheck(
                row.requirement,
                row.report_status,
                f"{row.priority.capitalize()} requirement. {score_detail}",
                weight=_EVIDENCE_PRIORITY_WEIGHTS.get(row.priority, 1.0),
                score_value=row.score,
            )
        )

    if not checks:
        checks.append(
            ReportCheck(
                "No analyzed requirements",
                "info",
                "The job analysis did not return requirements for evidence scoring.",
            )
        )

    return ReportSection(
        "Evidence & Gaps",
        "This section measures whether job requirements are supported by verified candidate evidence and represented truthfully in the resume. Critical requirements receive more weight than important or secondary requirements.",
        [ReportSubsection("Requirement evidence coverage", checks)],
    )

_EXPORT_NAMES = (
    'build_evidence_gap_report',
    '_evidence_gaps_section',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
