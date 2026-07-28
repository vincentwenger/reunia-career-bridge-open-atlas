from __future__ import annotations

from resume_tailor.optimization import (
    FINAL_OPTIMIZATION_BATCH_SIZE,
    final_optimization_focus_score,
    final_optimization_issue_batches,
    final_optimization_score_guard,
)
from resume_tailor.resume_report import (
    ReportCheck,
    ReportSection,
    ReportSubsection,
    ResumeReport,
)


def _section(name: str, score: float, *, warnings: int = 0) -> ReportSection:
    checks = [
        ReportCheck(
            label=f"{name} score",
            status="pass",
            detail="Synthetic score for optimization guard testing.",
            score_value=score,
        )
    ]
    checks.extend(
        ReportCheck(
            label=f"{name} warning {index}",
            status="warning",
            detail="Synthetic actionable recommendation.",
        )
        for index in range(warnings)
    )
    return ReportSection(
        name=name,
        intro="",
        subsections=[ReportSubsection(name="Checks", checks=checks)],
    )


def _report(
    *,
    content: float = 80,
    searchability: float = 80,
    recruiter: float = 80,
    formatting: float = 80,
    soft: float = 80,
    hard: float = 80,
    evidence: float = 80,
    content_warnings: int = 0,
    search_warnings: int = 0,
) -> ResumeReport:
    return ResumeReport(
        searchability=_section("Searchability", searchability, warnings=search_warnings),
        hard_skills=_section("Hard skills", hard),
        soft_skills=_section("Soft skills", soft),
        content_quality=_section("Content Quality", content, warnings=content_warnings),
        recruiter_tips=_section("Recruiter tips", recruiter),
        formatting=_section("Formatting", formatting),
        evidence_gaps=_section("Evidence & Gaps", evidence),
    )


def test_issue_batches_are_small_and_do_not_mix_categories():
    report = _report(content_warnings=7, search_warnings=2)

    batches = final_optimization_issue_batches(report)

    assert batches
    assert all(1 <= len(batch) <= FINAL_OPTIMIZATION_BATCH_SIZE for batch in batches)
    assert all(len({issue.section for issue in batch}) == 1 for batch in batches)
    assert [len(batch) for batch in batches] == [3, 3, 1, 2]


def test_score_guard_accepts_a_targeted_improvement_without_regressions():
    current = _report()
    candidate = _report(content=90)

    accepted, regressions = final_optimization_score_guard(
        current, candidate, "Content Quality"
    )

    assert accepted is True
    assert regressions == []
    assert final_optimization_focus_score(candidate) > final_optimization_focus_score(current)


def test_score_guard_rejects_overall_or_protected_metric_regression():
    current = _report()
    candidate = _report(content=100, hard=70)

    accepted, regressions = final_optimization_score_guard(
        current, candidate, "Content Quality"
    )

    assert accepted is False
    assert any("Hard Skills" in item for item in regressions)


def test_score_guard_rejects_target_category_regression_even_when_other_scores_rise():
    current = _report()
    candidate = _report(content=75, searchability=100, recruiter=100)

    accepted, regressions = final_optimization_score_guard(
        current, candidate, "Content Quality"
    )

    assert accepted is False
    assert any("Content Quality" in item for item in regressions)


def test_optimization_batch_state_is_reset_with_final_report(profile):
    from resume_tailor.web_state import WorkflowState

    state = WorkflowState(source_profile=profile)
    state.optimization_accepted_batch_count = 2
    state.optimization_rejected_batch_count = 3
    state.optimization_rejected_issue_count = 7
    state.optimization_unchanged_batch_count = 1
    state.optimization_baseline_rolled_back = True

    state.clear_final_report()

    assert state.optimization_accepted_batch_count == 0
    assert state.optimization_rejected_batch_count == 0
    assert state.optimization_rejected_issue_count == 0
    assert state.optimization_unchanged_batch_count == 0
    assert state.optimization_baseline_rolled_back is False
