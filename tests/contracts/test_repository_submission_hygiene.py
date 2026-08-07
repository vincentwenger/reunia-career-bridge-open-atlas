"""Repository-submission hygiene contracts."""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RepositorySubmissionHygieneTests(unittest.TestCase):
    def test_root_contains_only_the_primary_markdown_document(self) -> None:
        root_markdown = sorted(path.name for path in ROOT.glob("*.md"))
        self.assertEqual(root_markdown, ["README.md"])
        self.assertTrue((ROOT / "docs" / "submission" / "HACKATHON_CHANGES.md").is_file())
        self.assertTrue((ROOT / "docs" / "submission" / "PREEXISTING_COMPONENTS.md").is_file())
        history = ROOT / "docs" / "submission" / "project-history"
        for name in (
            "README.md",
            "PRE_HACKATHON_RESUME_TAILOR.md",
            "REUNIA_SUBMISSION_PERIOD.md",
            "CAREER_BRIDGE_TIMELINE.md",
            "GIT_HISTORY_GUIDE.md",
        ):
            self.assertTrue((history / name).is_file(), history / name)

    def test_cleanup_utility_removes_compiled_python_artifacts(self) -> None:
        script = ROOT / "scripts" / "clean_repository_artifacts.py"
        spec = importlib.util.spec_from_file_location("clean_repository_artifacts", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            cache = temporary_root / "package" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.cpython-311.pyc").write_bytes(b"compiled")
            (temporary_root / "orphan.pyo").write_bytes(b"compiled")
            module.clean(temporary_root)
            self.assertFalse(cache.exists())
            self.assertFalse((temporary_root / "orphan.pyo").exists())

    def test_gitignore_covers_generated_local_artifacts(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("__pycache__/", "*.py[cod]", ".pytest_cache/", "node_modules/", ".venv/"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, gitignore)

    def test_deployment_scripts_have_one_canonical_upload_entrypoint(self) -> None:
        deployment = ROOT / "scripts" / "deployment"
        upload_scripts = sorted(path.name for path in deployment.glob("upload_to_lightsail*.bat"))
        self.assertEqual(upload_scripts, ["upload_to_lightsail.bat"])
        self.assertTrue((deployment / "provision_career_bridge_storage.bat").is_file())
        self.assertFalse(any(".." in path.name for path in deployment.iterdir() if path.is_file()))

    def test_lightsail_async_jobs_link_resolves(self) -> None:
        document = ROOT / "docs" / "deployment" / "lightsail.md"
        content = document.read_text(encoding="utf-8")
        match = re.search(r"\[[^]]+\]\(([^)]*async-ai-jobs\.md)\)", content)
        self.assertIsNotNone(match)
        target = (document.parent / match.group(1)).resolve()
        self.assertTrue(target.is_file(), target)


if __name__ == "__main__":
    unittest.main()
