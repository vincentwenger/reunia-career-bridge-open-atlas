"""Contracts for complete development and browser-test dependencies."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DevelopmentTestDependencyContracts(unittest.TestCase):
    def test_development_requirements_include_runtime_and_playwright(self) -> None:
        requirements = ROOT / "requirements-dev.txt"
        self.assertTrue(requirements.is_file())
        content = requirements.read_text(encoding="utf-8")
        self.assertIn("-r requirements.txt", content)
        self.assertRegex(content, r"(?m)^playwright(?:[<>=!~].*)?$")
        self.assertRegex(content, r"(?m)^pytest(?:[<>=!~].*)?$")

    def test_runtime_ci_installs_browser_and_requires_runtime_phases(self) -> None:
        workflow = ROOT / ".github" / "workflows" / "runtime-tests.yml"
        self.assertTrue(workflow.is_file())
        content = workflow.read_text(encoding="utf-8")
        self.assertIn("python -m pip install -r requirements-dev.txt", content)
        self.assertIn("python -m playwright install --with-deps chromium", content)
        self.assertIn("python tests/run_final_integration_checks.py", content)
        self.assertIn("--require-runtime", content)
        runner = (ROOT / "tests" / "run_final_integration_checks.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("tests.integration.test_csp_runtime", runner)
        self.assertIn("tests.browser.test_csp_compatibility", runner)

    def test_browser_validation_uses_playwright_managed_chromium(self) -> None:
        dependency_helper = (ROOT / "tests" / "runtime_dependencies.py").read_text(
            encoding="utf-8"
        )
        browser_test = (
            ROOT / "tests" / "browser" / "test_international_profile_journey.py"
        ).read_text(encoding="utf-8")
        self.assertIn("playwright.chromium.executable_path", dependency_helper)
        self.assertNotIn('shutil.which("chromium")', browser_test)
        self.assertIn("playwright_chromium_executable()", browser_test)


if __name__ == "__main__":
    unittest.main()
