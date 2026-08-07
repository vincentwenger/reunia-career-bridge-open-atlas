"""Logical source bundles for code that is intentionally split across modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SourceBundle:
    paths: tuple[Path, ...]

    def read_text(self, encoding: str = "utf-8") -> str:
        parts: list[str] = []
        for index, path in enumerate(self.paths):
            text = path.read_text(encoding=encoding)
            if index:
                text = text.replace("from __future__ import annotations\n", "", 1)
            parts.append(f"# source: {path.relative_to(ROOT)}\n{text}")
        return "\n\n".join(parts)


def family(directory: Path, facade_name: str, module_prefix: str) -> SourceBundle:
    facade = directory / facade_name
    modules = sorted(
        path
        for path in directory.glob(f"{module_prefix}_*.py")
        if path.name != facade.name
    )
    return SourceBundle((facade, *modules))


SERVICE_ROOT = ROOT / "products" / "reunia" / "meeting_assistant" / "services"
ADMIN_ANALYTICS_SOURCE = family(
    SERVICE_ROOT, "admin_analytics_service.py", "admin_analytics"
)
MOCK_INTERVIEW_SOURCE = family(
    SERVICE_ROOT, "mock_interview_service.py", "mock_interview"
)
