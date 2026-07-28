from __future__ import annotations

from .models import AuditIssue
from .resume_report import ResumeReport

FINAL_OPTIMIZATION_SECTIONS = (
    "Content Quality",
    "Searchability",
    "Recruiter tips",
    "Formatting",
    "Soft skills",
)

# Apply AI suggestions in small, category-specific groups. Each proposed group is
# rescored before it can replace the current best resume. This prevents a broad
# rewrite from improving one check while lowering the report overall.
FINAL_OPTIMIZATION_BATCH_SIZE = 3
FINAL_OPTIMIZATION_SECTION_WEIGHTS = {
    "Content Quality": 0.30,
    "Searchability": 0.25,
    "Recruiter tips": 0.20,
    "Formatting": 0.15,
    "Soft skills": 0.10,
}
SCORE_GUARD_TOLERANCE = 0.01

# Only findings that can be changed through TailoringProposal content are sent to
# the AI optimizer. Contact data, education records, template layout, file type,
# web links, and other profile/template-owned checks remain advisory. Filtering
# them avoids slow no-op model calls.
FINAL_OPTIMIZATION_ACTIONABLE_SUBSECTIONS = {
    "Content Quality": {
        "Semantic Match",
        "Grammar & Spelling",
        "Metric Integrity",
        "Readability",
        "Writing Style",
        "Content Focus",
    },
    "Searchability": {"Summary"},
    "Recruiter tips": {"Measurable Results", "Resume Tone", "Word Count"},
    "Soft skills": {"Skill comparison"},
}



def _report_fix_instruction(section_name: str) -> str:
    instructions = {
        "Content Quality": (
            "Improve clarity, grammar, readability, bullet structure, and impact using only "
            "claims and metrics already supported by the candidate profile."
        ),
        "Searchability": (
            "Improve ATS searchability with exact relevant job-language and verified skills, "
            "without keyword stuffing or adding unsupported qualifications."
        ),
        "Recruiter tips": (
            "Improve recruiter readability, concise action-oriented wording, and role relevance "
            "without inventing achievements or metrics."
        ),
        "Formatting": (
            "Correct text-level spacing, punctuation, capitalization, and consistency issues that "
            "can be fixed inside the proposal. Preserve dates and facts from the source profile."
        ),
        "Soft skills": (
            "Use only verified, job-relevant soft skills and demonstrate them conservatively in "
            "existing resume wording when supported by the candidate profile."
        ),
    }
    return instructions[section_name]


def final_optimization_issues(report: ResumeReport) -> list[AuditIssue]:
    """Convert actionable Final Resume Report checks into conservative fix requests."""
    issues: list[AuditIssue] = []
    for section in report.sections():
        if section.name not in FINAL_OPTIMIZATION_SECTIONS:
            continue
        for subsection in section.subsections:
            for check in subsection.checks:
                if check.status not in {"warning", "fail"}:
                    continue
                issues.append(
                    AuditIssue(
                        severity="warning",
                        section=section.name,
                        source_id=subsection.name,
                        issue=f"{check.label}: {check.detail}",
                        suggested_fix=_report_fix_instruction(section.name),
                    )
                )
    return issues


def final_optimization_actionable_issues(report: ResumeReport) -> list[AuditIssue]:
    """Return only report findings that proposal text can actually resolve."""
    issues: list[AuditIssue] = []
    for section in report.sections():
        allowed_subsections = FINAL_OPTIMIZATION_ACTIONABLE_SUBSECTIONS.get(section.name)
        if not allowed_subsections:
            continue
        for subsection in section.subsections:
            if subsection.name not in allowed_subsections:
                continue
            for check in subsection.checks:
                if check.status not in {"warning", "fail"}:
                    continue
                issues.append(
                    AuditIssue(
                        severity="warning",
                        section=section.name,
                        source_id=subsection.name,
                        issue=f"{check.label}: {check.detail}",
                        suggested_fix=_report_fix_instruction(section.name),
                    )
                )
    return issues


def final_optimization_actionable_issue_batches(
    report: ResumeReport,
) -> list[list[AuditIssue]]:
    """Batch only content-editable findings, avoiding profile/template no-op calls."""
    issues_by_section: dict[str, list[AuditIssue]] = {
        name: [] for name in FINAL_OPTIMIZATION_SECTIONS
    }
    for issue in final_optimization_actionable_issues(report):
        issues_by_section[issue.section].append(issue)

    batches: list[list[AuditIssue]] = []
    for section_name in FINAL_OPTIMIZATION_SECTIONS:
        section_issues = issues_by_section[section_name]
        for start in range(0, len(section_issues), FINAL_OPTIMIZATION_BATCH_SIZE):
            batches.append(section_issues[start : start + FINAL_OPTIMIZATION_BATCH_SIZE])
    return batches


def final_optimization_issue_batches(report: ResumeReport) -> list[list[AuditIssue]]:
    """Group report findings by category into small, predictable AI requests."""
    issues_by_section: dict[str, list[AuditIssue]] = {
        name: [] for name in FINAL_OPTIMIZATION_SECTIONS
    }
    for issue in final_optimization_issues(report):
        if issue.section in issues_by_section:
            issues_by_section[issue.section].append(issue)

    batches: list[list[AuditIssue]] = []
    for section_name in FINAL_OPTIMIZATION_SECTIONS:
        section_issues = issues_by_section[section_name]
        for start in range(0, len(section_issues), FINAL_OPTIMIZATION_BATCH_SIZE):
            batches.append(section_issues[start : start + FINAL_OPTIMIZATION_BATCH_SIZE])
    return batches


def final_optimization_focus_score(report: ResumeReport) -> float:
    """Score only the five report categories owned by Improve Resume Quality."""
    sections = {section.name: section for section in report.sections()}
    score = sum(
        sections[name].score() * weight
        for name, weight in FINAL_OPTIMIZATION_SECTION_WEIGHTS.items()
    )
    return round(score, 1)


def final_optimization_score_guard(
    current_report: ResumeReport,
    candidate_report: ResumeReport,
    target_section: str | None = None,
) -> tuple[bool, list[str]]:
    """Accept a batch only when it preserves every protected report metric."""
    current_sections = {section.name: section for section in current_report.sections()}
    candidate_sections = {section.name: section for section in candidate_report.sections()}
    comparisons = [
        ("overall score", current_report.overall_score(), candidate_report.overall_score()),
        ("job-match score", current_report.job_match_score(), candidate_report.job_match_score()),
        (
            "Hard Skills",
            current_sections["Hard skills"].score(),
            candidate_sections["Hard skills"].score(),
        ),
        (
            "Evidence & Gaps",
            current_sections["Evidence & Gaps"].score(),
            candidate_sections["Evidence & Gaps"].score(),
        ),
        (
            "optimization-category score",
            final_optimization_focus_score(current_report),
            final_optimization_focus_score(candidate_report),
        ),
    ]
    if target_section is not None:
        comparisons.append(
            (
                target_section,
                current_sections[target_section].score(),
                candidate_sections[target_section].score(),
            )
        )
    regressions = [
        f"{label} {before:.1f} → {after:.1f}"
        for label, before, after in comparisons
        if after + SCORE_GUARD_TOLERANCE < before
    ]
    return not regressions, regressions
