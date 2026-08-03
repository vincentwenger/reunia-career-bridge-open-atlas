from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .bullet_text import (
    has_bullet_structure_artifacts,
    normalize_resume_bullet_terminal_punctuation,
    normalize_resume_bullet_text,
)
from .docx_export import RESUME_FORMAT_SECTIONS, SKILL_CATEGORY_ORDER
from .docx_styles import (
    compose_resume_theme,
    normalize_career_stage,
    normalize_resume_format,
    normalize_visual_design,
    resume_preference_label,
)
from .models import ApprovedResume, CandidateProfile
from .resume_language import resume_format_headings, resume_labels


class PdfConversionError(RuntimeError):
    """Raised when a resume PDF cannot be generated."""


# Runtime font registration avoids bundling or distributing font files. The app
# uses fonts already installed on the host OS and falls back to built-in PDF fonts.
_FONT_REGISTRATION_ATTEMPTED = False
_FONT_FAMILY = {
    "sans": "Helvetica",
    "sans_bold": "Helvetica-Bold",
    "serif": "Times-Roman",
    "serif_bold": "Times-Bold",
}

# A subtle 6-point gap separates employers in the PDF without inserting a
# literal blank paragraph. This mirrors the Word export while preserving space.
EXPERIENCE_ENTRY_GAP_POINTS = 6


def _first_existing(paths: Iterable[str]) -> str | None:
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            return str(path)
    return None


def _register_runtime_fonts() -> None:
    global _FONT_REGISTRATION_ATTEMPTED
    if _FONT_REGISTRATION_ATTEMPTED:
        return
    _FONT_REGISTRATION_ATTEMPTED = True

    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    font_candidates = {
        "ResumeSans": (
            str(windows / "arial.ttf"),
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf",
        ),
        "ResumeSans-Bold": (
            str(windows / "arialbd.ttf"),
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ),
        "ResumeSerif": (
            str(windows / "times.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
            "/Library/Fonts/Times New Roman.ttf",
        ),
        "ResumeSerif-Bold": (
            str(windows / "timesbd.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
            "/Library/Fonts/Times New Roman Bold.ttf",
        ),
    }

    registered: set[str] = set()
    for name, candidates in font_candidates.items():
        font_path = _first_existing(candidates)
        if not font_path:
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, font_path))
            registered.add(name)
        except Exception:
            # PDF export must still work with ReportLab's built-in fonts.
            continue

    if {"ResumeSans", "ResumeSans-Bold"}.issubset(registered):
        _FONT_FAMILY["sans"] = "ResumeSans"
        _FONT_FAMILY["sans_bold"] = "ResumeSans-Bold"
        # Paragraph inline markup such as <b>...</b> resolves bold through a
        # registered font family. Without this mapping ReportLab keeps using
        # the regular custom font even though the bold face was registered.
        pdfmetrics.registerFontFamily(
            "ResumeSans",
            normal="ResumeSans",
            bold="ResumeSans-Bold",
            italic="ResumeSans",
            boldItalic="ResumeSans-Bold",
        )

    if {"ResumeSerif", "ResumeSerif-Bold"}.issubset(registered):
        _FONT_FAMILY["serif"] = "ResumeSerif"
        _FONT_FAMILY["serif_bold"] = "ResumeSerif-Bold"
        pdfmetrics.registerFontFamily(
            "ResumeSerif",
            normal="ResumeSerif",
            bold="ResumeSerif-Bold",
            italic="ResumeSerif",
            boldItalic="ResumeSerif-Bold",
        )


def _rgb_color(value) -> colors.Color:
    channels = list(value)
    return colors.Color(*(channel / 255 for channel in channels[:3]))


def _normalize_pdf_text(value: str) -> str:
    """Normalize text without changing verified resume wording materially."""
    text = unicodedata.normalize("NFC", str(value or ""))
    replacements = {
        "\u00a0": " ",
        "\u200b": "",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return " ".join(text.split())


def _markup(value: str) -> str:
    return escape(_normalize_pdf_text(value), quote=False)


def _link_markup(label: str, target: str, color_hex: str) -> str:
    safe_label = _markup(label)
    safe_target = escape(target.strip(), quote=True)
    if not safe_target:
        return safe_label
    return f'<link href="{safe_target}" color="#{color_hex}"><u>{safe_label}</u></link>'


def _paragraph_style(
    name: str,
    *,
    font_name: str,
    font_size: float,
    text_color,
    leading: float | None = None,
    alignment: int = TA_LEFT,
    space_before: float = 0,
    space_after: float = 0,
    left_indent: float = 0,
    first_line_indent: float = 0,
    keep_with_next: bool = False,
) -> ParagraphStyle:
    return ParagraphStyle(
        name=name,
        fontName=font_name,
        fontSize=font_size,
        leading=leading or max(font_size * 1.18, font_size + 1.2),
        textColor=text_color,
        alignment=alignment,
        spaceBefore=space_before,
        spaceAfter=space_after,
        leftIndent=left_indent,
        firstLineIndent=first_line_indent,
        keepWithNext=keep_with_next,
        splitLongWords=True,
        allowWidows=0,
        allowOrphans=0,
    )


def _font_names(theme) -> tuple[str, str, str, str]:
    heading_uses_serif = "cambria" in theme.heading_font.casefold() or "times" in theme.heading_font.casefold()
    heading_regular = _FONT_FAMILY["serif"] if heading_uses_serif else _FONT_FAMILY["sans"]
    heading_bold = _FONT_FAMILY["serif_bold"] if heading_uses_serif else _FONT_FAMILY["sans_bold"]
    return _FONT_FAMILY["sans"], _FONT_FAMILY["sans_bold"], heading_regular, heading_bold


def _build_styles(theme, *, compact: bool = False) -> dict[str, ParagraphStyle]:
    body_font, body_bold, heading_font, heading_bold = _font_names(theme)
    text_color = _rgb_color(theme.text_color)
    accent_color = _rgb_color(theme.accent_color)
    header_alignment = TA_CENTER if theme.header_alignment is not None and int(theme.header_alignment) == 1 else TA_LEFT
    section_space_before = (
        max(3.5, theme.section_space_before - 2.5) if compact else theme.section_space_before
    )
    section_space_after = (
        max(1.0, theme.section_space_after - 1.5) if compact else theme.section_space_after
    )
    bullet_space_after = (
        max(0.0, theme.bullet_space_after - 0.5) if compact else theme.bullet_space_after
    )
    header_space_reduction = 0.5 if compact else 0.0

    return {
        "name": _paragraph_style(
            "PdfResumeName",
            font_name=heading_font if theme.is_mid_career_corporate else heading_bold,
            font_size=theme.name_size,
            text_color=accent_color,
            leading=theme.name_size * 1.08,
            alignment=header_alignment,
            space_after=max(1, (3 if theme.is_mid_career_corporate else 1) - header_space_reduction),
            keep_with_next=True,
        ),
        "target": _paragraph_style(
            "PdfResumeTarget",
            font_name=heading_font if theme.is_mid_career_corporate else heading_bold,
            font_size=theme.target_size,
            text_color=text_color if theme.is_mid_career_corporate else accent_color,
            alignment=header_alignment,
            space_after=max(1, (3 if theme.is_mid_career_corporate else 1) - header_space_reduction),
            keep_with_next=True,
        ),
        "contact": _paragraph_style(
            "PdfResumeContact",
            font_name=body_font,
            font_size=theme.contact_size,
            text_color=text_color,
            alignment=header_alignment,
            leading=theme.contact_size * (1.10 if compact else 1.15),
            space_after=max(1, (2 if theme.is_mid_career_corporate else 5) - header_space_reduction),
        ),
        "section": _paragraph_style(
            "PdfResumeSection",
            font_name=heading_bold,
            font_size=theme.section_size,
            text_color=accent_color,
            leading=theme.section_size * (1.04 if compact else 1.08),
            alignment=TA_CENTER if theme.is_mid_career_corporate else TA_LEFT,
            space_before=section_space_before,
            space_after=section_space_after,
            keep_with_next=True,
        ),
        "body": _paragraph_style(
            "PdfResumeBody",
            font_name=body_font,
            font_size=theme.body_size,
            text_color=text_color,
            leading=theme.body_size * (1.10 if compact else 1.18),
            alignment=TA_LEFT,
            space_after=1.0 if compact else 2,
        ),
        "skill": _paragraph_style(
            "PdfResumeSkill",
            font_name=body_font,
            font_size=theme.skill_size,
            text_color=text_color,
            leading=theme.skill_size * (1.10 if compact else 1.17),
            space_after=0 if theme.is_mid_career_corporate else 1,
        ),
        "employer": _paragraph_style(
            "PdfResumeEmployer",
            font_name=body_font,
            font_size=theme.employer_size,
            text_color=text_color,
            leading=theme.employer_size * (1.06 if compact else 1.12),
            space_after=0,
            keep_with_next=True,
        ),
        "employer_right": _paragraph_style(
            "PdfResumeEmployerRight",
            font_name=body_font,
            font_size=theme.employer_size,
            text_color=text_color,
            leading=theme.employer_size * (1.06 if compact else 1.12),
            alignment=TA_RIGHT,
            keep_with_next=True,
        ),
        "role": _paragraph_style(
            "PdfResumeRole",
            font_name=body_bold,
            font_size=theme.role_size,
            text_color=accent_color if (theme.is_executive or theme.is_early_career) else text_color,
            leading=theme.role_size * (1.06 if compact else 1.12),
            space_after=0 if theme.is_mid_career_corporate else 1,
            keep_with_next=True,
        ),
        "bullet": _paragraph_style(
            "PdfResumeBullet",
            font_name=body_font,
            font_size=theme.bullet_size,
            text_color=text_color,
            leading=theme.bullet_size * (1.10 if compact else 1.18),
            alignment=TA_LEFT,
            left_indent=(0.22 if theme.is_mid_career_corporate else 0.18) * inch,
            first_line_indent=(-0.17 if theme.is_mid_career_corporate else -0.13) * inch,
            space_after=bullet_space_after,
        ),
        "education": _paragraph_style(
            "PdfResumeEducation",
            font_name=body_font,
            font_size=theme.education_size,
            text_color=text_color,
            leading=theme.education_size * (1.08 if compact else 1.15),
            keep_with_next=False,
        ),
        "education_right": _paragraph_style(
            "PdfResumeEducationRight",
            font_name=body_font,
            font_size=theme.education_size,
            text_color=text_color,
            leading=theme.education_size * (1.08 if compact else 1.15),
            alignment=TA_RIGHT,
            keep_with_next=False,
        ),
        "education_meta": _paragraph_style(
            "PdfResumeEducationMeta",
            font_name=body_font,
            font_size=max(theme.education_size - 0.15, 8),
            text_color=text_color,
            leading=theme.education_size * (1.07 if compact else 1.12),
            space_after=0.5,
        ),
        "education_detail": _paragraph_style(
            "PdfResumeEducationDetail",
            font_name=body_font,
            font_size=max(theme.education_size - 0.15, 8),
            text_color=text_color,
            leading=theme.education_size * (1.08 if compact else 1.14),
            left_indent=(0.22 * inch) if theme.is_mid_career_corporate else 0,
            first_line_indent=(-0.17 * inch) if theme.is_mid_career_corporate else 0,
            space_after=0.5 if compact else 1.5,
        ),
    }


def _section_flowables(label: str, theme, styles) -> list:
    text = label.upper() if theme.section_uppercase else label
    items: list = [Paragraph(_markup(text), styles["section"])]
    if theme.section_border:
        items.append(
            HRFlowable(
                width="100%",
                thickness=0.7,
                color=_rgb_color(theme.accent_color),
                spaceBefore=0,
                spaceAfter=1.8,
            )
        )
    return items


def _contact_markup(profile: CandidateProfile, theme) -> tuple[str, str]:
    contact = profile.contact
    linked_items: list[str] = []
    if contact.phone.strip():
        linked_items.append(_markup(contact.phone))
    if contact.email.strip():
        linked_items.append(
            _link_markup(contact.email, f"mailto:{contact.email.strip()}", theme.link_color)
        )
    if contact.linkedin_url.strip():
        linked_items.append(
            _link_markup(
                contact.linkedin_label.strip() or "LinkedIn",
                contact.linkedin_url,
                theme.link_color,
            )
        )
    if contact.github_url.strip():
        linked_items.append(
            _link_markup(
                contact.github_label.strip() or "GitHub",
                contact.github_url,
                theme.link_color,
            )
        )
    location = _markup(contact.location) if contact.location.strip() else ""
    return location, " &nbsp;|&nbsp; ".join(linked_items)


def _add_header(story: list, profile: CandidateProfile, approved: ApprovedResume, theme, styles) -> None:
    story.append(Paragraph(_markup(profile.name), styles["name"]))
    if approved.target_title.strip():
        story.append(Paragraph(_markup(approved.target_title), styles["target"]))
        if theme.is_mid_career_corporate:
            # Match the Word style's compact double rule beneath the target title.
            rule_color = _rgb_color(theme.text_color)
            story.append(HRFlowable(width="100%", thickness=0.45, color=rule_color, spaceBefore=0, spaceAfter=0.6))
            story.append(HRFlowable(width="100%", thickness=0.45, color=rule_color, spaceBefore=0, spaceAfter=1.4))

    location, linked_items = _contact_markup(profile, theme)
    if theme.is_mid_career_corporate:
        if location:
            story.append(Paragraph(location, styles["contact"]))
        if linked_items:
            story.append(Paragraph(linked_items, styles["contact"]))
    else:
        all_items = [item for item in (location, linked_items) if item]
        if all_items:
            story.append(Paragraph(" &nbsp;|&nbsp; ".join(all_items), styles["contact"]))


def _add_summary(story: list, approved: ApprovedResume, theme, styles, heading: str) -> None:
    summary = _normalize_pdf_text(approved.professional_summary)
    if not summary:
        return
    story.extend(_section_flowables(heading, theme, styles))
    story.append(Paragraph(_markup(summary), styles["body"]))


def _skill_groups(
    profile: CandidateProfile,
    approved: ApprovedResume,
    resume_format: str,
    resume_language: str,
):
    labels = resume_labels(resume_language)
    category_map = {
        "hard": (labels["hard_skills"], approved.skills.hard_skills),
        "soft": (labels["soft_skills"], approved.skills.soft_skills),
        "tools": (labels["tools_software"], approved.skills.tools_software),
        "industry": (labels["industry_knowledge"], approved.skills.industry_knowledge),
        "languages": (labels["languages"], profile.skills.languages),
    }
    groups = []
    for key in SKILL_CATEGORY_ORDER[resume_format]:
        label, raw_items = category_map[key]
        items = [_normalize_pdf_text(item) for item in raw_items if str(item).strip()]
        if items:
            groups.append((label, items))
    return groups


def _add_skills(
    story: list,
    profile: CandidateProfile,
    approved: ApprovedResume,
    theme,
    styles,
    *,
    heading: str,
    resume_format: str,
    resume_language: str,
) -> None:
    groups = _skill_groups(profile, approved, resume_format, resume_language)
    if not groups:
        return
    story.extend(_section_flowables(heading, theme, styles))

    if theme.is_mid_career_corporate:
        lines = [f"<b>{_markup(label)}:</b> {_markup(', '.join(items))}" for label, items in groups]
        story.append(Paragraph("<br/>".join(lines), styles["skill"]))
        return

    accent = _rgb_color(theme.accent_color).hexval()[2:]
    for label, items in groups:
        label_markup = f"<b>{_markup(label)}:</b>"
        if theme.is_executive or theme.is_early_career:
            label_markup = f'<font color="#{accent}">{label_markup}</font>'
        story.append(
            Paragraph(f"{label_markup} {_markup(', '.join(items))}", styles["skill"])
        )


def _add_experience(
    story: list,
    profile: CandidateProfile,
    approved: ApprovedResume,
    theme,
    styles,
    *,
    heading: str,
    usable_width: float,
    entry_gap_points: float = EXPERIENCE_ENTRY_GAP_POINTS,
) -> None:
    included = [
        (
            experience,
            [
                _normalize_pdf_text(item)
                for item in approved.bullets_by_experience.get(experience.id, [])
                if str(item).strip()
            ],
        )
        for experience in profile.experiences
    ]
    included = [(experience, bullets) for experience, bullets in included if bullets]
    if not included:
        return

    story.extend(_section_flowables(heading, theme, styles))

    for index, (experience, bullets) in enumerate(included):
        # Let ReportLab paginate naturally. The employer heading and role are
        # kept together below, which prevents an orphaned heading without
        # forcing the second employer onto a new page.
        employer_name = f"<b>{_markup(experience.employer)}</b>"
        location = _normalize_pdf_text(experience.location)
        employer_left = employer_name + (f", {_markup(location)}" if location else "")
        date_text = _markup(experience.dates)

        left_width = usable_width * 0.72
        right_width = usable_width - left_width
        header_table = Table(
            [[Paragraph(employer_left, styles["employer"]), Paragraph(date_text, styles["employer_right"])]],
            colWidths=[left_width, right_width],
            hAlign="LEFT",
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        role = Paragraph(_markup(experience.title), styles["role"])
        heading_flowables = []
        if index > 0:
            heading_flowables.append(Spacer(1, entry_gap_points))
        heading_flowables.extend([header_table, role])
        story.append(KeepTogether(heading_flowables))

        for bullet in bullets:
            rendered = (
                normalize_resume_bullet_text(bullet, max_words=35)
                if has_bullet_structure_artifacts(bullet)
                else bullet
            )
            rendered = normalize_resume_bullet_terminal_punctuation(rendered)
            rendered = _normalize_pdf_text(rendered)
            if rendered:
                marker = "&bull;"
                if theme.is_early_career:
                    accent = _rgb_color(theme.accent_color).hexval()[2:]
                    marker = f'<font color="#{accent}"><b>&bull;</b></font>'
                story.append(Paragraph(f"{marker} {_markup(rendered)}", styles["bullet"]))


def _add_education(
    story: list,
    profile: CandidateProfile,
    theme,
    styles,
    *,
    heading: str,
    usable_width: float,
    item_gap_points: float = 1.5,
    left_column_ratio: float = 0.88,
) -> None:
    if not profile.education:
        return
    story.extend(_section_flowables(heading, theme, styles))

    for item in profile.education:
        credential = _normalize_pdf_text(item.credential)
        institution_parts = [
            _normalize_pdf_text(part)
            for part in (item.institution, item.location)
            if str(part).strip()
        ]
        institution = ", ".join(institution_parts)
        date_text = _normalize_pdf_text(item.date)
        detail = _normalize_pdf_text(
            normalize_resume_bullet_terminal_punctuation(item.detail)
        )

        if theme.is_mid_career_corporate:
            left_parts = []
            if credential:
                left_parts.append(f"<b>{_markup(credential)}</b>")
            if institution:
                left_parts.append(_markup(institution))
            left_text = ", ".join(left_parts)
        else:
            left_text = f"<b>{_markup(credential)}</b>" if credential else _markup(institution)

        # Mirror Word's right-aligned tab stop: reserve only the compact
        # date column so credentials and institutions do not wrap prematurely.
        left_width = usable_width * left_column_ratio
        table = Table(
            [[Paragraph(left_text, styles["education"]), Paragraph(_markup(date_text), styles["education_right"])]],
            colWidths=[left_width, usable_width - left_width],
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        block: list = [table]
        if not theme.is_mid_career_corporate and institution and credential:
            block.append(Paragraph(_markup(institution), styles["education_meta"]))
        story.append(KeepTogether(block))
        if detail:
            marker = "&bull; " if theme.is_mid_career_corporate else ""
            story.append(Paragraph(marker + _markup(detail), styles["education_detail"]))
        story.append(Spacer(1, item_gap_points))



@dataclass(frozen=True)
class PdfPaginationQuality:
    page_count: int
    last_page_substantive_lines: int
    last_page_fill_ratio: float

    @property
    def has_orphan_final_page(self) -> bool:
        if self.page_count <= 1:
            return False
        return (
            self.last_page_substantive_lines < 3
            or self.last_page_fill_ratio < 0.20
        )


def _pdf_pagination_quality(payload: bytes, theme) -> PdfPaginationQuality:
    try:
        reader = PdfReader(BytesIO(payload))
    except Exception:
        return PdfPaginationQuality(1, 0, 1.0)
    page_count = len(reader.pages)
    if page_count <= 1:
        return PdfPaginationQuality(max(1, page_count), 0, 1.0)

    page = reader.pages[-1]
    positions: list[tuple[float, float]] = []

    def visitor_text(text, current_matrix, text_matrix, _font_dictionary, font_size) -> None:
        if not str(text or "").strip():
            return
        try:
            # Apply the text matrix through the current transformation matrix.
            y_position = (
                (float(text_matrix[4]) * float(current_matrix[1]))
                + (float(text_matrix[5]) * float(current_matrix[3]))
                + float(current_matrix[5])
            )
            positions.append((y_position, float(font_size or 0.0)))
        except (IndexError, TypeError, ValueError):
            return

    try:
        extracted = page.extract_text(visitor_text=visitor_text) or ""
    except TypeError:
        extracted = page.extract_text() or ""
    extracted_lines = [line.strip() for line in extracted.splitlines() if line.strip()]

    unique_y: list[float] = []
    for y_position, _font_size in sorted(positions, key=lambda item: item[0]):
        if not unique_y or abs(y_position - unique_y[-1]) >= 2.0:
            unique_y.append(y_position)
    substantive_lines = len(unique_y) if unique_y else len(extracted_lines)

    if unique_y:
        max_font_size = max((font_size for _y, font_size in positions), default=10.0)
        content_span = max_font_size if len(unique_y) == 1 else (unique_y[-1] - unique_y[0]) + max_font_size
        page_height = float(page.mediabox.height)
        usable_height = max(
            1.0,
            page_height - ((theme.top_margin + theme.bottom_margin) * inch),
        )
        fill_ratio = max(0.0, min(1.0, content_span / usable_height))
    else:
        # A conservative text-only fallback when PDF coordinates are unavailable.
        fill_ratio = min(1.0, substantive_lines / 45.0)

    return PdfPaginationQuality(
        page_count=page_count,
        last_page_substantive_lines=substantive_lines,
        last_page_fill_ratio=fill_ratio,
    )


def _build_pdf_payload(
    profile: CandidateProfile,
    approved: ApprovedResume,
    *,
    theme,
    stage: str,
    format_key: str,
    design_key: str,
    resume_language: str,
    compact: bool,
) -> bytes:
    styles = _build_styles(theme, compact=compact)
    headings = resume_format_headings(resume_language, format_key)

    output = BytesIO()
    usable_width = LETTER[0] - (theme.left_margin + theme.right_margin) * inch
    document = SimpleDocTemplate(
        output,
        pagesize=LETTER,
        rightMargin=theme.right_margin * inch,
        leftMargin=theme.left_margin * inch,
        topMargin=theme.top_margin * inch,
        bottomMargin=theme.bottom_margin * inch,
        title=f"{_normalize_pdf_text(profile.name)} - {_normalize_pdf_text(approved.target_title or 'Resume')}",
        author=_normalize_pdf_text(profile.name),
        subject=f"Tailored resume - {resume_preference_label(stage, format_key, design_key)}",
        creator="Resume Tailoring Application",
        allowSplitting=1,
    )

    story: list = []
    _add_header(story, profile, approved, theme, styles)

    def add_summary() -> None:
        _add_summary(story, approved, theme, styles, headings["summary"])

    def add_skills() -> None:
        _add_skills(
            story,
            profile,
            approved,
            theme,
            styles,
            heading=headings["skills"],
            resume_format=format_key,
            resume_language=resume_language,
        )

    def add_experience() -> None:
        _add_experience(
            story,
            profile,
            approved,
            theme,
            styles,
            heading=headings["experience"],
            usable_width=usable_width,
            entry_gap_points=4.0 if compact else EXPERIENCE_ENTRY_GAP_POINTS,
        )

    def add_education() -> None:
        _add_education(
            story,
            profile,
            theme,
            styles,
            heading=headings["education"],
            usable_width=usable_width,
            item_gap_points=0.5 if compact else 1.5,
            left_column_ratio=0.91 if compact else 0.88,
        )

    if format_key == "technical":
        add_skills()
        add_summary()
        add_experience()
        add_education()
    elif format_key == "career_changer":
        add_summary()
        add_skills()
        add_education()
        add_experience()
    elif format_key == "freelance":
        add_summary()
        add_skills()
        add_experience()
        add_education()
    else:
        add_summary()
        add_skills()
        if theme.is_early_career:
            add_education()
            add_experience()
        else:
            add_experience()
            add_education()

    if not story:
        raise PdfConversionError("The final resume is empty and cannot be exported as PDF.")

    try:
        document.build(story)
    except Exception as exc:
        raise PdfConversionError(f"The PDF could not be generated: {exc}") from exc

    payload = output.getvalue()
    if not payload.startswith(b"%PDF-"):
        raise PdfConversionError("The generated file was not a valid PDF.")
    return payload


def export_resume_pdf(
    profile: CandidateProfile,
    approved: ApprovedResume,
    *,
    career_stage: str | None = None,
    resume_format: str | None = None,
    visual_design: str | None = None,
    resume_language: str | None = None,
) -> bytes:
    """Generate a styled, ATS-readable PDF without Word or LibreOffice.

    PDF and Word are rendered from the same approved resume data and the same
    career-stage, structural-format, and visual-design selections. If the first
    PDF pass leaves a nearly empty final page, the exporter rebuilds it with the
    same font sizes and margins but tighter discretionary spacing.
    """
    _register_runtime_fonts()
    stage = normalize_career_stage(career_stage)
    format_key = normalize_resume_format(resume_format)
    design_key = normalize_visual_design(visual_design)
    theme = compose_resume_theme(stage, design_key)
    language = resume_language or "English"

    payload = _build_pdf_payload(
        profile,
        approved,
        theme=theme,
        stage=stage,
        format_key=format_key,
        design_key=design_key,
        resume_language=language,
        compact=False,
    )
    quality = _pdf_pagination_quality(payload, theme)
    if not quality.has_orphan_final_page:
        return payload

    compact_payload = _build_pdf_payload(
        profile,
        approved,
        theme=theme,
        stage=stage,
        format_key=format_key,
        design_key=design_key,
        resume_language=language,
        compact=True,
    )
    compact_quality = _pdf_pagination_quality(compact_payload, theme)
    if (
        compact_quality.page_count < quality.page_count
        or not compact_quality.has_orphan_final_page
        or compact_quality.last_page_fill_ratio >= quality.last_page_fill_ratio
    ):
        return compact_payload
    return payload
