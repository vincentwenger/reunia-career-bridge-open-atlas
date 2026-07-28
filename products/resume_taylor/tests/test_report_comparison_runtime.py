from __future__ import annotations


def test_report_comparison_builds_when_initial_and_working_reports_exist(
    project_root,
    profile,
    analysis,
    proposal,
):
    """Regression: opening Resume Reports must not depend on a removed local helper."""
    from resume_tailor.report_impacts import comparison_view
    from resume_tailor.resume_report import (
        build_initial_resume_proposal,
        build_resume_report,
        initial_resume_title,
    )

    initial_proposal = build_initial_resume_proposal(profile)
    initial_report = build_resume_report(
        profile,
        analysis,
        initial_proposal,
        generated_filename="Initial_Resume.docx",
        template_path=project_root / "data" / "resume_template_professional.docx",
        resume_title=initial_resume_title(profile),
    )
    working_report = build_resume_report(
        profile,
        analysis,
        proposal,
        generated_filename="Working_Resume.docx",
        template_path=project_root / "data" / "resume_template_professional.docx",
        resume_title=analysis.target_title,
    )

    comparison = comparison_view(
        initial_report,
        working_report,
        initial_label="Initial Resume",
        updated_label="Working Resume",
    )

    assert comparison["initial_label"] == "Initial Resume"
    assert comparison["updated_label"] == "Working Resume"
    assert comparison["rows"]
