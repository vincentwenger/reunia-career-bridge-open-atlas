from __future__ import annotations

import os
import unicodedata
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .bullet_text import has_bullet_structure_artifacts, normalize_resume_bullet_text
from .docx_export import RESUME_FORMAT_SECTIONS, SKILL_CATEGORY_ORDER
from .docx_styles import (
    compose_resume_theme,
    normalize_career_stage,
    normalize_resume_format,
    normalize_visual_design,
    resume_preference_label,
)
from .models import ApprovedResume, CandidateProfile


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
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/Library/Fonts/Arial.ttf",
        ),
        "ResumeSans-Bold": (
            str(windows / "arialbd.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
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


def _build_styles(theme) -> dict[str, ParagraphStyle]:
    body_font, body_bold, heading_font, heading_bold = _font_names(theme)
    text_color = _rgb_color(theme.text_color)
    accent_color = _rgb_color(theme.accent_color)
    header_alignment = TA_CENTER if theme.header_alignment is not None and int(theme.header_alignment) == 1 else TA_LEFT

    return {
        "name": _paragraph_style(
            "PdfResumeName",
            font_name=heading_font if theme.is_mid_career_corporate else heading_bold,
            font_size=theme.name_size,
            text_color=accent_color,
            leading=theme.name_size * 1.08,
            alignment=header_alignment,
            space_after=1.5,
            keep_with_next=True,
        ),
        "target": _paragraph_style(
            "PdfResumeTarget",
            font_name=heading_font if theme.is_mid_career_corporate else heading_bold,
            font_size=theme.target_size,
            text_color=text_color if theme.is_mid_career_corporate else accent_color,
            alignment=header_alignment,
            space_after=2,
            keep_with_next=True,
        ),
        "contact": _paragraph_style(
            "PdfResumeContact",
            font_name=body_font,
            font_size=theme.contact_size,
            text_color=text_color,
            alignment=header_alignment,
            leading=theme.contact_size * 1.15,
            space_after=1.5,
        ),
        "section": _paragraph_style(
            "PdfResumeSection",
            font_name=heading_bold,
            font_size=theme.section_size,
            text_color=accent_color,
            leading=theme.section_size * 1.08,
            alignment=TA_CENTER if theme.is_mid_career_corporate else TA_LEFT,
            space_before=theme.section_space_before,
            space_after=theme.section_space_after,
            keep_with_next=True,
        ),
        "body": _paragraph_style(
            "PdfResumeBody",
            font_name=body_font,
            font_size=theme.body_size,
            text_color=text_color,
            leading=theme.body_size * 1.18,
            alignment=TA_JUSTIFY if theme.is_mid_career_corporate else TA_LEFT,
            space_after=2,
        ),
        "skill": _paragraph_style(
            "PdfResumeSkill",
            font_name=body_font,
            font_size=theme.skill_size,
            text_color=text_color,
            leading=theme.skill_size * 1.17,
            space_after=1.2,
        ),
        "employer": _paragraph_style(
            "PdfResumeEmployer",
            font_name=body_font,
            font_size=theme.employer_size,
            text_color=text_color,
            leading=theme.employer_size * 1.12,
            space_after=0,
            keep_with_next=True,
        ),
        "employer_right": _paragraph_style(
            "PdfResumeEmployerRight",
            font_name=body_font,
            font_size=theme.employer_size,
            text_color=text_color,
            leading=theme.employer_size * 1.12,
            alignment=TA_RIGHT,
            keep_with_next=True,
        ),
        "role": _paragraph_style(
            "PdfResumeRole",
            font_name=body_bold,
            font_size=theme.role_size,
            text_color=accent_color if (theme.is_executive or theme.is_early_career) else text_color,
            leading=theme.role_size * 1.12,
            space_after=1,
            keep_with_next=True,
        ),
        "bullet": _paragraph_style(
            "PdfResumeBullet",
            font_name=body_font,
            font_size=theme.bullet_size,
            text_color=text_color,
            leading=theme.bullet_size * 1.18,
            alignment=TA_JUSTIFY if theme.is_mid_career_corporate else TA_LEFT,
            left_indent=11,
            first_line_indent=-8,
            space_after=theme.bullet_space_after,
        ),
        "education": _paragraph_style(
            "PdfResumeEducation",
            font_name=body_font,
            font_size=theme.education_size,
            text_color=text_color,
            leading=theme.education_size * 1.15,
            keep_with_next=True,
        ),
        "education_right": _paragraph_style(
            "PdfResumeEducationRight",
            font_name=body_font,
            font_size=theme.education_size,
            text_color=text_color,
            leading=theme.education_size * 1.15,
            alignment=TA_RIGHT,
            keep_with_next=True,
        ),
        "education_meta": _paragraph_style(
            "PdfResumeEducationMeta",
            font_name=body_font,
            font_size=max(theme.education_size - 0.15, 8),
            text_color=text_color,
            leading=theme.education_size * 1.12,
            space_after=0.5,
        ),
        "education_detail": _paragraph_style(
            "PdfResumeEducationDetail",
            font_name=body_font,
            font_size=max(theme.education_size - 0.15, 8),
            text_color=text_color,
            leading=theme.education_size * 1.14,
            left_indent=11 if theme.is_mid_career_corporate else 0,
            first_line_indent=-8 if theme.is_mid_career_corporate else 0,
            space_after=1.5,
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


def _skill_groups(profile: CandidateProfile, approved: ApprovedResume, resume_format: str):
    category_map = {
        "hard": ("Hard Skills", approved.skills.hard_skills),
        "soft": ("Soft Skills", approved.skills.soft_skills),
        "tools": ("Tools & Software", approved.skills.tools_software),
        "industry": ("Industry Knowledge", approved.skills.industry_knowledge),
        "languages": ("Languages", profile.skills.languages),
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
) -> None:
    groups = _skill_groups(profile, approved, resume_format)
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
    balance_after_first = (
        len(included) >= 2
        and len(included[0][1]) >= 7
        and sum(len(item) for item in included[0][1]) >= 1000
    )

    for index, (experience, bullets) in enumerate(included):
        if index == 1 and balance_after_first:
            story.append(PageBreak())

        employer_name = _markup(experience.employer)
        if theme.is_mid_career_corporate:
            employer_name = f"<u>{employer_name}</u>"
        else:
            employer_name = f"<b>{employer_name}</b>"
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
        story.append(KeepTogether([header_table, role]))

        for bullet in bullets:
            rendered = (
                normalize_resume_bullet_text(bullet, max_words=35)
                if has_bullet_structure_artifacts(bullet)
                else bullet
            )
            rendered = _normalize_pdf_text(rendered)
            if rendered:
                marker = "&bull;"
                if theme.is_early_career:
                    accent = _rgb_color(theme.accent_color).hexval()[2:]
                    marker = f'<font color="#{accent}"><b>&bull;</b></font>'
                story.append(Paragraph(f"{marker} {_markup(rendered)}", styles["bullet"]))
        story.append(Spacer(1, 2))


def _add_education(
    story: list,
    profile: CandidateProfile,
    theme,
    styles,
    *,
    heading: str,
    usable_width: float,
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
        detail = _normalize_pdf_text(item.detail)

        if theme.is_mid_career_corporate:
            left_parts = []
            if credential:
                left_parts.append(f"<b>{_markup(credential)}</b>")
            if institution:
                left_parts.append(_markup(institution))
            left_text = ", ".join(left_parts)
        else:
            left_text = f"<b>{_markup(credential)}</b>" if credential else _markup(institution)

        left_width = usable_width * 0.78
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
            prefix = "- " if theme.is_mid_career_corporate else ""
            story.append(Paragraph(prefix + _markup(detail), styles["education_detail"]))
        story.append(Spacer(1, 1.5))


def export_resume_pdf(
    profile: CandidateProfile,
    approved: ApprovedResume,
    *,
    career_stage: str | None = None,
    resume_format: str | None = None,
    visual_design: str | None = None,
) -> bytes:
    """Generate a styled, ATS-readable PDF without Word or LibreOffice.

    PDF and Word are rendered from the same approved resume data and the same
    career-stage, structural-format, and visual-design selections. This removes
    any desktop application dependency from the web download path.
    """
    _register_runtime_fonts()
    stage = normalize_career_stage(career_stage)
    format_key = normalize_resume_format(resume_format)
    design_key = normalize_visual_design(visual_design)
    theme = compose_resume_theme(stage, design_key)
    styles = _build_styles(theme)
    headings = RESUME_FORMAT_SECTIONS[format_key]

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
        )

    def add_education() -> None:
        _add_education(
            story,
            profile,
            theme,
            styles,
            heading=headings["education"],
            usable_width=usable_width,
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
