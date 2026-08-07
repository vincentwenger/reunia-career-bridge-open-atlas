"""Shared runtime dependency checks for integration and browser validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_RUNTIME_MODULES = (
    "flask",
    "dotenv",
    "redis",
    "openai",
    "docx",
    "reportlab",
    "openpyxl",
    "pypdf",
    "xlrd",
)


def playwright_chromium_executable() -> str | None:
    """Return Playwright's managed Chromium executable when it is installed."""

    if importlib.util.find_spec("playwright.sync_api") is None:
        return None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
        return str(executable) if executable.is_file() else None
    except Exception:
        return None


def missing_runtime_dependencies() -> list[str]:
    """Return packages or browser binaries required by runtime validation."""

    missing = [
        module_name
        for module_name in _RUNTIME_MODULES
        if importlib.util.find_spec(module_name) is None
    ]
    if importlib.util.find_spec("playwright.sync_api") is None:
        missing.append("playwright")
    elif playwright_chromium_executable() is None:
        missing.append("playwright-chromium")
    return missing
