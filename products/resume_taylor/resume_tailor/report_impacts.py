from __future__ import annotations

import re
from typing import Any

from .models import TailoringProposal
from .resume_report import ResumeReport


def report_check_map(report: ResumeReport) -> dict[tuple[str, str, str], tuple[str, float]]:
    return {
        (section.name, subsection.name, check.label): (check.status, check.score())
        for section in report.sections()
        for subsection in section.subsections
        for check in subsection.checks
        if check.status != "info"
    }



def comparison_view(
    initial: ResumeReport,
    updated: ResumeReport,
    *,
    initial_label: str = "Initial",
    updated_label: str = "Draft",
) -> dict[str, Any]:
    initial_scores = {section.name: section.score() for section in initial.sections()}
    updated_scores = {section.name: section.score() for section in updated.sections()}
    initial_checks = report_check_map(initial)
    updated_checks = report_check_map(updated)
    shared_keys = set(initial_checks) & set(updated_checks)
    evidence_keys = {key for key in shared_keys if key[0] == "Evidence & Gaps"}

    overall_delta = updated.overall_score() - initial.overall_score()
    job_match_delta = updated.job_match_score() - initial.job_match_score()
    quality_delta = updated.resume_quality_score() - initial.resume_quality_score()
    evidence_delta = updated.evidence_gaps.score() - initial.evidence_gaps.score()

    rows = [
        {
            "score": "Overall",
            "initial": initial.overall_score(),
            "updated": updated.overall_score(),
            "change": overall_delta,
        },
        {
            "score": "Job Match",
            "initial": initial.job_match_score(),
            "updated": updated.job_match_score(),
            "change": job_match_delta,
        },
        {
            "score": "Resume Quality",
            "initial": initial.resume_quality_score(),
            "updated": updated.resume_quality_score(),
            "change": quality_delta,
        },
    ]
    rows.extend(
        {
            "score": section_name,
            "initial": initial_scores[section_name],
            "updated": updated_scores[section_name],
            "change": updated_scores[section_name] - initial_scores[section_name],
        }
        for section_name in initial_scores
    )

    return {
        "initial_label": initial_label,
        "updated_label": updated_label,
        "overall": updated.overall_score(),
        "overall_delta": overall_delta,
        "job_match": updated.job_match_score(),
        "job_match_delta": job_match_delta,
        "resume_quality": updated.resume_quality_score(),
        "resume_quality_delta": quality_delta,
        "improved": sum(
            updated_checks[key][1] > initial_checks[key][1] + 0.05 for key in shared_keys
        ),
        "red_x_fixed": sum(
            initial_checks[key][0] == "fail" and updated_checks[key][0] != "fail"
            for key in shared_keys
        ),
        "regressed": sum(
            updated_checks[key][1] < initial_checks[key][1] - 0.05 for key in shared_keys
        ),
        "evidence_score": updated.evidence_gaps.score(),
        "evidence_delta": evidence_delta,
        "evidence_improved": sum(
            updated_checks[key][1] > initial_checks[key][1] + 0.05 for key in evidence_keys
        ),
        "newly_fully_supported": sum(
            initial_checks[key][1] < 99.95 and updated_checks[key][1] >= 99.95
            for key in evidence_keys
        ),
        "remaining_evidence_gaps": sum(updated_checks[key][1] <= 20.05 for key in evidence_keys),
        "evidence_regressed": sum(
            updated_checks[key][1] < initial_checks[key][1] - 0.05 for key in evidence_keys
        ),
        "rows": rows,
    }


def tailoring_report_impacts(
    initial: ResumeReport,
    updated: ResumeReport,
    analysis,
) -> dict[str, Any]:
    """Return concise, honest report improvements that may explain tailoring changes.

    Exact checks such as Job Title Match can be shown as observed impact. Requirement-
    level and section-level deltas are labelled as likely impact because several resume
    changes can contribute to the same report score.

    Only genuine score improvements are returned. Unchanged and negative deltas are not
    useful explanations for an improvement made to the resume and must never be shown as
    if the changed content caused them.
    """
    initial_checks = report_check_map(initial)
    updated_checks = report_check_map(updated)

    def check_impact(
        *,
        section: str | None = None,
        subsection: str | None = None,
        label: str | None = None,
        display_label: str | None = None,
        certainty: str = "likely",
    ) -> dict[str, Any] | None:
        candidates: list[tuple[tuple[str, str, str], float, float]] = []
        for key, (_, before_score) in initial_checks.items():
            if key not in updated_checks:
                continue
            if section is not None and key[0] != section:
                continue
            if subsection is not None and key[1] != subsection:
                continue
            if label is not None and key[2] != label:
                continue
            after_score = updated_checks[key][1]
            if after_score - before_score <= 0.05:
                continue
            candidates.append((key, before_score, after_score))
        if not candidates:
            return None
        key, before_score, after_score = max(
            candidates,
            key=lambda item: (item[2] - item[1], abs(item[2] - item[1])),
        )
        concise_label = display_label or key[2]
        return {
            "label": f"{key[0]} — {concise_label}",
            "section": key[0],
            "subsection": key[1],
            "check_label": key[2],
            "before": before_score,
            "after": after_score,
            "delta": after_score - before_score,
            "certainty": certainty,
        }

    def section_impact(section_name: str, display_label: str) -> dict[str, Any] | None:
        before = next(
            (section.score() for section in initial.sections() if section.name == section_name),
            None,
        )
        after = next(
            (section.score() for section in updated.sections() if section.name == section_name),
            None,
        )
        if before is None or after is None or after - before <= 0.05:
            return None
        return {
            "label": f"{section_name} — {display_label}",
            "section": section_name,
            "subsection": "",
            "check_label": display_label,
            "before": before,
            "after": after,
            "delta": after - before,
            "certainty": "likely",
        }

    requirement_impacts: dict[str, dict[str, Any]] = {}
    for requirement in getattr(analysis, "requirements", []) or []:
        impact = check_impact(
            section="Evidence & Gaps",
            label=requirement.requirement,
            display_label=requirement.requirement,
            certainty="likely",
        )
        if impact:
            requirement_impacts[requirement.id] = impact

    hard_skill_impact = section_impact("Hard skills", "overall score")
    soft_skill_impact = section_impact("Soft skills", "overall score")
    return {
        "available": True,
        "title": check_impact(
            subsection="Job Title Match",
            label="The job title matches the resume profile title",
            display_label="Job Title Match",
            certainty="observed",
        ),
        "summary": check_impact(
            subsection="Summary",
            display_label="Summary check",
            certainty="likely",
        ),
        "skills": {
            "Hard Skills": hard_skill_impact,
            "Tools & Software": hard_skill_impact,
            "Industry Knowledge": hard_skill_impact,
            "Soft Skills": soft_skill_impact,
        },
        "requirements": requirement_impacts,
    }


def _normalized_impact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _bullet_contributes_to_requirement(bullet, requirement) -> bool:
    """Return whether one included bullet can represent a specific requirement."""
    if bullet is None or not bullet.include:
        return False
    if requirement.id in bullet.matched_requirement_ids:
        return True

    text = _normalized_impact_text(bullet.proposed_text)
    if not text:
        return False
    terms = [*getattr(requirement, "keywords", []), requirement.requirement]
    return any(
        normalized_term and normalized_term in text
        for normalized_term in (_normalized_impact_text(term) for term in terms)
    )


def attributable_bullet_report_impacts(
    before: TailoringProposal,
    after: TailoringProposal,
    analysis,
    requirement_impacts: dict[str, dict[str, Any]],
    changed_source_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Assign only uniquely attributable positive requirement improvements to bullets.

    A report delta is attached to a bullet only when that bullet newly represents the
    requirement and it is the sole changed bullet that can explain that transition. This
    prevents the UI from attaching the first loosely matching metric to an unrelated item.
    """
    if not requirement_impacts or not changed_source_ids:
        return {}

    before_lookup = {item.source_bullet_id: item for item in before.bullet_proposals}
    after_lookup = {item.source_bullet_id: item for item in after.bullet_proposals}
    requirement_lookup = {
        requirement.id: requirement
        for requirement in (getattr(analysis, "requirements", []) or [])
    }
    candidates_by_requirement: dict[str, list[str]] = {}

    for requirement_id in requirement_impacts:
        requirement = requirement_lookup.get(requirement_id)
        if requirement is None:
            continue
        candidates: list[str] = []
        for source_id in changed_source_ids:
            contributed_before = _bullet_contributes_to_requirement(
                before_lookup.get(source_id), requirement
            )
            contributes_after = _bullet_contributes_to_requirement(
                after_lookup.get(source_id), requirement
            )
            if not contributed_before and contributes_after:
                candidates.append(source_id)
        if candidates:
            candidates_by_requirement[requirement_id] = candidates

    uniquely_explained_requirements: dict[str, str] = {
        requirement_id: candidates[0]
        for requirement_id, candidates in candidates_by_requirement.items()
        if len(candidates) == 1
    }
    requirements_by_source: dict[str, list[str]] = {}
    for requirement_id, source_id in uniquely_explained_requirements.items():
        requirements_by_source.setdefault(source_id, []).append(requirement_id)

    assigned: dict[str, dict[str, Any]] = {}
    for source_id, requirement_ids in requirements_by_source.items():
        # A bullet can newly represent several requirements. Showing an arbitrary first
        # metric would recreate the ambiguity this attribution layer is designed to avoid.
        if len(requirement_ids) != 1:
            continue
        requirement_id = requirement_ids[0]
        impact = dict(requirement_impacts[requirement_id])
        impact["attribution"] = "unique_direct_contribution"
        assigned[source_id] = impact
    return assigned
