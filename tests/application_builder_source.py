"""Source helpers for Application Builder structural contract tests.

The runtime is intentionally split across the composition root and feature route
modules. Structural tests should inspect this logical source bundle instead of
assuming every route lives in ``products/resume_taylor/app.py``.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "products" / "resume_taylor" / "app.py"
ROUTE_ROOT = ROOT / "products" / "resume_taylor" / "application_builder_routes"
ROUTE_MODULE_ORDER = (
    "lifecycle",
    "application_context",
    "resume_workflow",
    "career_translation",
    "interview_preparation",
    "job_discovery",
    "applications",
)


def application_builder_source(
    *route_modules: str,
    include_app: bool = True,
) -> str:
    """Return the composition-root and selected route-module sources."""

    selected = route_modules or ROUTE_MODULE_ORDER
    parts: list[str] = []
    if include_app:
        parts.append(APP_PATH.read_text(encoding="utf-8"))
    for module_name in selected:
        if module_name not in ROUTE_MODULE_ORDER:
            raise ValueError(f"Unknown Application Builder route module: {module_name}")
        parts.append((ROUTE_ROOT / f"{module_name}.py").read_text(encoding="utf-8"))
        package_root = ROUTE_ROOT / f"{module_name}_routes"
        if package_root.is_dir():
            for path in sorted(package_root.glob("*.py")):
                if path.name != "__init__.py":
                    parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)
