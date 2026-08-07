"""Direct regression tests for the two cross-product integration paths.

These tests close the final validation gaps for:

* structured resume findings reaching the Interview Preparation prompt; and
* interview scorecard findings creating application-linked Career Action Plan items.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REUNIA_ROOT = ROOT / "products" / "reunia"
RESUME_ROOT = ROOT / "products" / "resume_taylor"
for path in (str(REUNIA_ROOT), str(RESUME_ROOT), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from products.resume_taylor.resume_tailor.interview_preparation import (
    VerifiedEvidenceBundle,
    VerifiedEvidenceItem,
    build_interview_preparation_prompt,
)
from products.resume_taylor.resume_tailor.models import (
    CareerTranslationAssessment,
    CareerTranslationFinding,
)
from products.resume_taylor.resume_tailor.resume_findings import (
    ResumeAlignmentChange,
    ResumeClaimFinding,
    ResumeFindingsSnapshot,
    ResumeReportFinding,
    ResumeRequirementFinding,
)


class ResumeFindingsPromptIntegrationTests(unittest.TestCase):
    """Verify every saved findings category is transferred to the final prompt."""

    def test_all_structured_resume_findings_reach_interview_prompt(self) -> None:
        snapshot = ResumeFindingsSnapshot(
            captured_at="2026-07-29T17:00:00+00:00",
            source_stage="evidence_reviewed_final_resume",
            target_company="Sentinel Manufacturing",
            target_role="Senior International Accountant",
            application_context_fingerprint="context-sentinel-001",
            unsupported_or_partial_requirements=[
                ResumeRequirementFinding(
                    requirement_id="REQ-UNSUPPORTED-SENTINEL",
                    requirement="Hold an active U.S. CPA license",
                    category="qualification",
                    priority="critical",
                    evidence_status="unsupported",
                    evidence_ids=[],
                    appears_in_resume=False,
                    rationale="No verified CPA evidence was supplied.",
                    recommended_action="Prepare an honest credential bridge.",
                )
            ],
            evidence_review_warnings=[
                ResumeReportFinding(
                    report_stage="final_report",
                    section="Evidence & Gaps",
                    subsection="Evidence traceability",
                    label="EVIDENCE-WARNING-SENTINEL",
                    status="warning",
                    detail="A translated title needs a plain-language explanation.",
                )
            ],
            career_translation_assessment=CareerTranslationAssessment(
                summary="TRANSLATION-SUMMARY-SENTINEL",
                target_country="United States",
                target_role="Senior International Accountant",
                findings=[
                    CareerTranslationFinding(
                        category="job_title_translation",
                        source_text="Responsable Comptable",
                        translated_meaning="Accounting Manager",
                        disposition="reasonable_rephrasing",
                        evidence_ids=["E1"],
                        rationale="The verified responsibilities support the translation.",
                        recommended_action="Explain scope without inflating seniority.",
                    )
                ],
            ),
            resume_report_weaknesses=[
                ResumeReportFinding(
                    report_stage="final_report",
                    section="Targeting",
                    subsection="Role alignment",
                    label="REPORT-WEAKNESS-SENTINEL",
                    status="fail",
                    detail="The resume does not yet explain U.S. GAAP exposure.",
                )
            ],
            alignment_changes=ResumeAlignmentChange(
                baseline_job_match_score=42.0,
                current_job_match_score=68.0,
                job_match_improvement=26.0,
                baseline_overall_score=55.0,
                current_overall_score=74.0,
                overall_score_improvement=19.0,
            ),
            excluded_or_questioned_claims=[
                ResumeClaimFinding(
                    disposition="excluded",
                    source_id="ACC-CPA-SENTINEL",
                    requirement_id="REQ-UNSUPPORTED-SENTINEL",
                    original_text="",
                    proposed_text="Active U.S. CPA",
                    question="Do you hold an active U.S. CPA license?",
                    answer="No",
                    rationale="The candidate explicitly denied the qualification.",
                    matched_requirement_ids=["REQ-UNSUPPORTED-SENTINEL"],
                )
            ],
        )
        evidence = VerifiedEvidenceBundle(
            items=(
                VerifiedEvidenceItem(
                    id="E1",
                    text="Prepared monthly financial statements and coordinated month-end close.",
                    source="verified experience",
                ),
            ),
            source_label="Confirmed Candidate Profile",
            fingerprint="evidence-sentinel-001",
        )

        prompt = build_interview_preparation_prompt(
            company=snapshot.target_company,
            role=snapshot.target_role,
            job_description=(
                "Prepare financial statements, manage month-end close, and hold an active "
                "U.S. CPA license."
            ),
            evidence=evidence,
            resume_findings=snapshot,
        )

        start_marker = "STRUCTURED RESUME FINDINGS\n"
        end_marker = "\n---\n\nHOW TO USE THE FINDINGS"
        self.assertIn(start_marker, prompt)
        findings_block = prompt.split(start_marker, 1)[1]
        findings_json = findings_block.split("---\n", 1)[1].split(end_marker, 1)[0]
        transferred = json.loads(findings_json)

        self.assertEqual(
            transferred["unsupported_or_partial_requirements"][0]["requirement_id"],
            "REQ-UNSUPPORTED-SENTINEL",
        )
        self.assertEqual(
            transferred["evidence_review_warnings"][0]["label"],
            "EVIDENCE-WARNING-SENTINEL",
        )
        self.assertEqual(
            transferred["career_translation_assessment"]["summary"],
            "TRANSLATION-SUMMARY-SENTINEL",
        )
        self.assertEqual(
            transferred["resume_report_weaknesses"][0]["label"],
            "REPORT-WEAKNESS-SENTINEL",
        )
        self.assertEqual(transferred["alignment_changes"]["job_match_improvement"], 26.0)
        self.assertEqual(
            transferred["excluded_or_questioned_claims"][0]["source_id"],
            "ACC-CPA-SENTINEL",
        )
        self.assertIn("[E1] (verified experience)", prompt)
        self.assertLess(
            prompt.index("VERIFIED CANDIDATE EVIDENCE"),
            prompt.index("STRUCTURED RESUME FINDINGS"),
        )


class InterviewScorecardActionIntegrationTests(unittest.TestCase):
    """Verify weak scorecard findings create grounded application actions."""

    def test_scorecard_findings_generate_expected_application_actions(self) -> None:
        import subprocess

        helper = ROOT / "tests" / "helpers" / "run_scorecard_action_integration.py"
        result = subprocess.run(
            [sys.executable, str(helper)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Scorecard/action integration failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["generated_action_count"], 2)
        self.assertEqual(
            payload["weak_answer_action"],
            "Review weak interview answer 1 using one confirmed example and a clear result",
        )
        self.assertIn("Complete another mock interview", payload["repeat_action"])
        self.assertEqual(
            payload["invented_claims_removed"],
            ["SAP S/4HANA", "Google", "12 years"],
        )


class PostInterviewActionDateIntegrationTests(unittest.TestCase):
    """Verify interview follow-ups use post-interview deadlines."""

    def test_thank_you_actions_are_due_the_day_after_the_interview(self) -> None:
        import subprocess

        helper = ROOT / "tests" / "helpers" / "run_post_interview_action_dates.py"
        result = subprocess.run(
            [sys.executable, str(helper)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Post-interview action-date integration failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "passed")
        self.assertTrue(payload["completed_action_preserved"])
        self.assertEqual(payload["upcoming_action_count"], 2)



if __name__ == "__main__":
    unittest.main(verbosity=2)
