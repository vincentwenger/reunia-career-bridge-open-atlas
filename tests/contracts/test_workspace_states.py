"""UI state contract tests for major Career Bridge workspaces.

These tests intentionally avoid a browser dependency. They verify that every major
workspace declares loading, empty, and error states, and that asynchronous recovery
controls are wired to page JavaScript. Run with:

    python -m unittest -v tests.contracts.test_workspace_states
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class WorkspaceStateContractTests(unittest.TestCase):
    STATE_PATTERNS = {
        "loading": (
            re.compile(r"workspace_state\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]loading['\"]", re.DOTALL),
            re.compile(r"data-ui-state=['\"]loading['\"]"),
        ),
        "empty": (
            re.compile(r"workspace_state\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]empty['\"]", re.DOTALL),
            re.compile(r"data-ui-state=['\"]empty['\"]"),
        ),
        "error": (
            re.compile(r"workspace_state\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]error['\"]", re.DOTALL),
            re.compile(r"data-ui-state=['\"]error['\"]"),
        ),
    }

    WORKSPACES = {
        "homepage": (
            "products/reunia/templates/index.html",
            "products/reunia/static/js/pages/index.js",
        ),
        "career action plan": (
            "products/reunia/templates/action-center.html",
            "products/reunia/static/js/pages/action-center.js",
        ),
        "social impact": (
            "products/reunia/templates/analytics.html",
            "products/reunia/static/js/pages/analytics.js",
        ),
        "admin analytics": (
            "products/reunia/templates/admin-analytics.html",
            "products/reunia/static/js/pages/admin-analytics.js",
        ),
        "career evidence search": (
            "products/reunia/templates/knowledge.html",
            "products/reunia/static/js/pages/knowledge.js",
        ),
        "adaptive mock interview": (
            "products/reunia/templates/meeting-recorder.html",
            "products/reunia/static/js/pages/meeting-recorder.js",
        ),
        "application builder": (
            "products/resume_taylor/templates/application_builder/base.html",
            "products/resume_taylor/templates/application_builder/applications.html",
            "products/resume_taylor/static/app.js",
        ),
        "interview preparation": (
            "products/resume_taylor/templates/application_builder/base.html",
            "products/resume_taylor/templates/application_builder/interview_preparation.html",
            "products/resume_taylor/static/app.js",
        ),
    }

    RETRY_CONTROLS = {
        "homepage": ("home-retry-button", "products/reunia/static/js/pages/index.js"),
        "career action plan": ("action-retry-button", "products/reunia/static/js/pages/action-center.js"),
        "social impact": ("impact-retry-button", "products/reunia/static/js/pages/analytics.js"),
        "admin analytics": ("admin-retry-state-button", "products/reunia/static/js/pages/admin-analytics.js"),
        "career evidence search": ("knowledge-answer-retry", "products/reunia/static/js/pages/knowledge.js"),
        "adaptive mock interview": ("retryAnswerButton", "products/reunia/static/js/pages/meeting-recorder.js"),
        "application builder": ("application-builder-error-retry", "products/resume_taylor/static/app.js"),
    }

    @staticmethod
    def read(*relative_paths: str) -> str:
        return "\n".join((ROOT / path).read_text(encoding="utf-8") for path in relative_paths)

    def test_shared_component_supports_all_three_states(self) -> None:
        macro = self.read("products/reunia/templates/macros/ui.html")
        css = self.read("products/reunia/static/css/components.css")
        javascript = self.read("products/reunia/static/js/common.js")

        self.assertIn("macro workspace_state", macro)
        self.assertIn('data-ui-state="{{ state }}"', macro)
        for state in self.STATE_PATTERNS:
            self.assertIn(f".app-state--{state}", css)
        self.assertIn("function setWorkspaceState", javascript)
        self.assertIn("showWorkspaceState", javascript)
        self.assertIn("hideWorkspaceState", javascript)

    def test_every_major_workspace_declares_loading_empty_and_error_states(self) -> None:
        for workspace, resources in self.WORKSPACES.items():
            source = self.read(*resources)
            with self.subTest(workspace=workspace):
                for state, patterns in self.STATE_PATTERNS.items():
                    self.assertTrue(
                        any(pattern.search(source) for pattern in patterns),
                        f"{workspace} does not declare a {state} state",
                    )

    def test_async_error_states_have_wired_retry_controls(self) -> None:
        templates = self.read(
            "products/reunia/templates/index.html",
            "products/reunia/templates/action-center.html",
            "products/reunia/templates/analytics.html",
            "products/reunia/templates/admin-analytics.html",
            "products/reunia/templates/knowledge.html",
            "products/reunia/templates/meeting-recorder.html",
            "products/resume_taylor/templates/application_builder/base.html",
        )
        for workspace, (control_id, script_path) in self.RETRY_CONTROLS.items():
            script = self.read(script_path)
            with self.subTest(workspace=workspace):
                self.assertIn(control_id, templates)
                self.assertIn(control_id, script)
                self.assertRegex(script, rf"{re.escape(control_id)}|getElementById\(['\"]{re.escape(control_id)}['\"]\)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
