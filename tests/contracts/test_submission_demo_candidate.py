"""Contracts for the prepared Thomas MARTIN submission demo."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SubmissionDemoCandidateContracts(unittest.TestCase):
    def test_public_surfaces_use_thomas_martin(self) -> None:
        paths = (
            ROOT / "products/reunia/templates/_marketing_content.html",
            ROOT / "products/reunia/templates/login.html",
            ROOT / "docs/submission/DEMO_PLAN.md",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertIn("Thomas MARTIN", combined)
        self.assertIn("Senior Application Support Engineer", combined)
        self.assertNotIn("Lina Haddad", combined)
        self.assertNotIn("Digital Marketing Manager", combined)

    def test_public_safe_demo_resume_and_target_job_exist(self) -> None:
        demo = ROOT / "docs/submission/demo-data"
        resume = demo / "CV_Thomas_MARTIN_Fictif_Demo.docx"
        job = demo / "Thomas_MARTIN_Target_Job_Fictif.txt"
        guide = demo / "README.md"
        self.assertGreater(resume.stat().st_size, 10000)
        job_text = job.read_text(encoding="utf-8")
        self.assertIn("Northstar Community Bank", job_text)
        self.assertIn("Demonstrated leadership scope", job_text)
        self.assertIn("direct employee supervision", job_text)
        self.assertIn("hiring authority", job_text)
        self.assertNotIn("Direct people-management experience or formal ownership", job_text)
        guide_text = guide.read_text(encoding="utf-8")
        self.assertIn("does **not** provide", guide_text)
        self.assertIn("thomas.martin@example.com", guide_text)


if __name__ == "__main__":
    unittest.main()
