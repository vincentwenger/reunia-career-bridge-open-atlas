from __future__ import annotations

import re


def test_project_contains_no_hardcoded_openai_secret(project_root):
    secret_pattern = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")
    excluded_suffixes = {".docx", ".zip", ".pyc"}
    for path in project_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in excluded_suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not secret_pattern.search(text), f"Potential API key found in {path}"
