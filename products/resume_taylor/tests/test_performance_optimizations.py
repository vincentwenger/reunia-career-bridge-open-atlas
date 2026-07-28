from __future__ import annotations

from io import BytesIO

from docx import Document

from resume_tailor.docx_export import export_resume_docx
from resume_tailor.models import ApprovedResume
from resume_tailor.optimization import (
    final_optimization_actionable_issue_batches,
    final_optimization_issue_batches,
)
from resume_tailor.resume_report import build_resume_report


def test_step_four_filters_report_findings_that_cannot_change_proposal(
    profile, analysis, proposal, project_root
):
    report = build_resume_report(
        profile,
        analysis,
        proposal,
        generated_filename="Candidate_Axiom_Resume.docx",
        template_path=project_root / "data" / "resume_template_professional.docx",
        job_description="Axiom SQL testing collaboration",
        resume_title=analysis.target_title,
        exact_page_count=False,
    )

    all_batches = final_optimization_issue_batches(report)
    actionable_batches = final_optimization_actionable_issue_batches(report)

    assert len(actionable_batches) <= len(all_batches)
    assert all(batch[0].section != "Formatting" for batch in actionable_batches)
    assert all(
        issue.source_id not in {
            "Contact Information",
            "Section Headings",
            "Date Formatting",
            "Education Match",
            "File Type",
            "Web Presence",
        }
        for batch in actionable_batches
        for issue in batch
    )


def test_fast_report_mode_skips_exact_renderer(
    monkeypatch, profile, analysis, proposal, project_root
):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("The exact document renderer should not run in fast scoring mode.")

    monkeypatch.setattr("resume_tailor.resume_report.subprocess.run", fail_if_called)
    report = build_resume_report(
        profile,
        analysis,
        proposal,
        generated_filename="Candidate_Axiom_Resume.docx",
        template_path=project_root / "data" / "resume_template_professional.docx",
        job_description="Axiom SQL testing collaboration",
        resume_title=analysis.target_title,
        exact_page_count=False,
    )

    assert report.formatting.subsections


def test_report_can_reuse_already_generated_word_bytes(
    monkeypatch, profile, analysis, proposal, project_root
):
    approved = ApprovedResume(
        target_title=analysis.target_title,
        professional_summary=proposal.professional_summary,
        skills=proposal.skills,
        bullets_by_experience={
            experience.id: [
                item.proposed_text
                for bullet in experience.bullets
                for item in proposal.bullet_proposals
                if item.source_bullet_id == bullet.id and item.include
            ]
            for experience in profile.experiences
        },
    )
    template_path = project_root / "data" / "resume_template_professional.docx"
    document_bytes = export_resume_docx(template_path, profile, approved)
    assert Document(BytesIO(document_bytes)).paragraphs

    def fail_if_exported(*args, **kwargs):
        raise AssertionError("The report should inspect the existing Word bytes.")

    monkeypatch.setattr(
        "resume_tailor.resume_report.export_resume_docx", fail_if_exported
    )
    report = build_resume_report(
        profile,
        analysis,
        proposal,
        generated_filename="Candidate_Axiom_Resume.docx",
        template_path=template_path,
        job_description="Axiom SQL testing collaboration",
        resume_title=analysis.target_title,
        generated_document_bytes=document_bytes,
        exact_page_count=False,
    )

    assert report.overall_score() >= 0


def test_evidence_review_uses_one_audit_without_sequential_repair_rounds(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    review = source.split(
        "def _run_post_confirmation_evidence_review(", 1
    )[1].split("def create_app(", 1)[0]

    assert review.count("_run_reconciled_evidence_audit(") == 1
    assert "fix_ai.apply_suggested_fixes(" not in review
    assert "_conservatively_resolve_candidate_findings(" not in review
    assert "exact_page_count=False" in source.split(
        "def start_final_stage():", 1
    )[1].split('@app.post("/resume/save/<version>")', 1)[0]


def test_final_optimization_uses_one_ai_call_and_defers_exact_rendering(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    route = source.split("def start_final_stage():", 1)[1].split(
        '@app.post("/resume/save/<version>")', 1
    )[0]

    assert route.count("optimizer.apply_suggested_fixes(") == 1
    assert "final_optimization_actionable_issues(" in route
    assert "build_exact_report=False" in route
    assert "_store_fast_final_report_snapshot(" in route


def test_all_declined_confirmation_answers_skip_refinement_call(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    route = source.split("def apply_confirmation():", 1)[1].split(
        '@app.post("/confirmation/reopen")', 1
    )[0]

    assert "all_questions_declined" in route
    assert "if questions and not all_questions_declined:" in route
    assert "new evidence for the model to incorporate" in route


def test_regular_reports_default_to_fast_page_estimation(project_root):
    source = (project_root / "resume_tailor" / "resume_report.py").read_text(
        encoding="utf-8"
    )
    signature = source.split("def build_resume_report(", 1)[1].split(
        ") -> ResumeReport:", 1
    )[0]

    assert "exact_page_count: bool = False" in signature


def test_final_report_snapshot_requires_exact_page_rendering(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    final_snapshot = source.split("def _build_final_report_snapshot(", 1)[1].split(
        "def _store_refreshed_audit(", 1
    )[0]

    assert "generated_document_bytes=resume_bytes" in final_snapshot
    assert "exact_page_count=True" in final_snapshot
