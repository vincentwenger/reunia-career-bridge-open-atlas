"""Contracts for the public hackathon submission package."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SubmissionReadinessAssetContracts(unittest.TestCase):
    def test_ci_workflows_cover_static_runtime_and_browser_gates(self) -> None:
        static = (ROOT / ".github/workflows/asset-budget.yml").read_text(encoding="utf-8")
        runtime = (ROOT / ".github/workflows/runtime-tests.yml").read_text(encoding="utf-8")
        for command in (
            "python scripts/build_static_assets.py --check",
            "python scripts/check_asset_budgets.py",
            "python scripts/check_css_token_policy.py",
            "npm run lint:css",
        ):
            self.assertIn(command, static)
        for command in (
            "python -m pip install -r requirements-dev.txt",
            "python -m playwright install --with-deps chromium",
            "python -m pytest -q",
            "python tests/run_final_integration_checks.py --require-runtime",
        ):
            self.assertIn(command, runtime)

    def test_submission_documents_and_visual_assets_exist(self) -> None:
        expected = (
            "DEMO_PLAN.md",
            "DEVPOST_SUBMISSION_COPY.md",
            "PRIVACY_AND_DEMO_DATA.md",
            "SUBMISSION_CHECKLIST.md",
            "PREEXISTING_COMPONENTS.md",
            "HACKATHON_CHANGES.md",
            "pitch-deck.pdf",
        )
        submission = ROOT / "docs" / "submission"
        for name in expected:
            with self.subTest(name=name):
                path = submission / name
                self.assertTrue(path.is_file(), path)
                self.assertGreater(path.stat().st_size, 500)
        architecture = submission / "assets" / "architecture.svg"
        self.assertTrue(architecture.is_file())
        self.assertIn("Verified evidence", architecture.read_text(encoding="utf-8"))

        history = submission / "project-history"
        for name in (
            "README.md",
            "PRE_HACKATHON_RESUME_TAILOR.md",
            "REUNIA_SUBMISSION_PERIOD.md",
            "CAREER_BRIDGE_TIMELINE.md",
            "GIT_HISTORY_GUIDE.md",
        ):
            with self.subTest(history=name):
                path = history / name
                self.assertTrue(path.is_file(), path)
                self.assertGreater(path.stat().st_size, 400)

    def test_public_readme_avoids_unverifiable_paths_and_placeholders(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for forbidden in (
            "<your-public-repository-url>",
            "<repository-directory>",
            "legacy/reunia",
            "legacy/resume-tailor",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, readme)
        self.assertIn("scripts/submission/check_submission_readiness.py", readme)
        self.assertIn("docs/submission/DEMO_PLAN.md", readme)
        self.assertIn("Proprietary Source-Available License", readme)

    def test_submission_provenance_does_not_call_reunia_pre_hackathon(self) -> None:
        documents = (
            ROOT / "README.md",
            ROOT / "docs" / "submission" / "PREEXISTING_COMPONENTS.md",
            ROOT / "docs" / "submission" / "HACKATHON_CHANGES.md",
            ROOT / "docs" / "submission" / "DEVPOST_SUBMISSION_COPY.md",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in documents)
        self.assertNotIn("extends two pre-existing personal projects", combined)
        self.assertNotIn("sanitized predecessor branches", combined)
        self.assertIn("June 22, 2026", combined)
        self.assertIn("July 28, 2026", combined)
        self.assertIn("Resume Tailor", combined)

    def test_submission_readiness_script_imports_without_optional_runtime(self) -> None:
        path = ROOT / "scripts" / "submission" / "check_submission_readiness.py"
        spec = importlib.util.spec_from_file_location("submission_readiness", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        checks = module._repository_checks()
        self.assertTrue(checks)
        self.assertFalse(any(check.status == "failed" for check in checks))

    def test_license_is_proprietary_source_available(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("PROPRIETARY SOURCE-AVAILABLE LICENSE"))
        self.assertIn("Copyright © 2026", license_text)
        self.assertIn("you may download, clone, and execute", license_text)
        self.assertIn("you may not", license_text)
        self.assertIn("commercial purposes", license_text)


if __name__ == "__main__":
    unittest.main()
