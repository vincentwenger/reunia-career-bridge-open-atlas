from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from docx import Document

from resume_tailor.docx_styles import (
    DEFAULT_RESUME_STYLE,
    RESUME_STYLE_THEMES,
    clear_document_body,
    clear_headers_and_footers,
    configure_resume_document,
    normalize_resume_style,
)


def build_template(output_path: str | Path, style_key: str = DEFAULT_RESUME_STYLE) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_resume_style(style_key)
    theme = RESUME_STYLE_THEMES[normalized]

    document = Document()
    configure_resume_document(document, normalized)
    clear_document_body(document)
    clear_headers_and_footers(document)
    document.core_properties.title = f"{theme.label} ATS-Friendly Resume Template"
    document.core_properties.subject = (
        f"Style-only {theme.label.lower()} template used by the resume tailoring application"
    )
    document.core_properties.author = "Resume Tailoring Application"
    document.core_properties.keywords = f"resume, ATS, template, {normalized}"
    document.save(output)
    return output


def build_all_templates(output_directory: str | Path) -> list[Path]:
    directory = Path(output_directory)
    return [
        build_template(directory / f"resume_template_{style_key}.docx", style_key)
        for style_key in RESUME_STYLE_THEMES
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build clean dynamic resume Word templates.")
    parser.add_argument(
        "output",
        nargs="?",
        default="data",
        help="Output directory when --all is used, otherwise the output DOCX path.",
    )
    parser.add_argument(
        "--style",
        choices=tuple(RESUME_STYLE_THEMES),
        default=DEFAULT_RESUME_STYLE,
        help="Style to build when creating a single template.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Build all three career-stage resume templates.",
    )
    args = parser.parse_args()
    if args.all:
        for output in build_all_templates(args.output):
            print(output)
    else:
        print(build_template(args.output, args.style))


if __name__ == "__main__":
    main()
