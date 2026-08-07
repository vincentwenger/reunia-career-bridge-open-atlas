from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module, module_exports

"""DOCX parsing, pagination, typography, and layout primitives."""

def _approved_resume_for_report(
    profile: CandidateProfile,
    proposal: TailoringProposal,
    resume_title: str,
) -> ApprovedResume:
    proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
    bullets_by_experience: dict[str, list[str]] = {}
    for experience in profile.experiences:
        bullets_by_experience[experience.id] = [
            normalize_resume_bullet_terminal_punctuation(
                proposal_lookup[bullet.id].proposed_text
            )
            for bullet in experience.bullets
            if bullet.id in proposal_lookup
            and proposal_lookup[bullet.id].include
            and proposal_lookup[bullet.id].proposed_text.strip()
        ]
    return ApprovedResume(
        target_title=resume_title,
        professional_summary=proposal.professional_summary,
        skills=proposal.skills,
        bullets_by_experience=bullets_by_experience,
    )


def _document_for_report(
    template_path: str | Path | None,
    profile: CandidateProfile,
    proposal: TailoringProposal,
    resume_title: str,
    generated_document_bytes: bytes | None = None,
    *,
    career_stage: str | None = None,
    resume_format: str | None = None,
    visual_design: str | None = None,
    resume_language: str | None = None,
) -> tuple[Document | None, str | None]:
    if generated_document_bytes is not None:
        try:
            return Document(BytesIO(generated_document_bytes)), None
        except Exception as generated_error:
            return None, f"The generated resume document could not be inspected: {generated_error}"
    if not template_path:
        return None, "No resume template was supplied for formatting inspection."
    try:
        approved = _approved_resume_for_report(profile, proposal, resume_title)
        generated = export_resume_docx(
            template_path,
            profile,
            approved,
            enforce_language_gate=False,
            career_stage=career_stage,
            resume_format=resume_format,
            visual_design=visual_design,
            resume_language=resume_language,
        )
        return Document(BytesIO(generated)), None
    except Exception as generated_error:
        try:
            return Document(str(template_path)), (
                "The current proposal could not be rendered into the template, so template formatting "
                f"was inspected instead: {generated_error}"
            )
        except Exception as template_error:  # pragma: no cover - defensive UI fallback
            return None, f"The resume document could not be inspected: {template_error}"


def _body_paragraphs(document: Document) -> list:
    paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    return paragraphs


def _header_footer_parts(document: Document) -> list[HeaderPart | FooterPart]:
    return [
        part
        for part in document.part.package.parts
        if isinstance(part, (HeaderPart, FooterPart))
    ]


def _part_text(part: HeaderPart | FooterPart) -> str:
    return " ".join(
        node.text.strip()
        for node in part.element.xpath(".//w:t")
        if node.text and node.text.strip()
    )


def _column_count(section) -> int:
    columns = section._sectPr.find(qn("w:cols"))
    if columns is None:
        return 1
    declared = columns.get(qn("w:num"))
    declared_count = int(declared) if declared and declared.isdigit() else 1
    explicit_count = len(columns.findall(qn("w:col")))
    return max(1, declared_count, explicit_count)


def _paragraph_alignment(paragraph):
    if paragraph.alignment is not None:
        return paragraph.alignment
    style = paragraph.style
    while style is not None:
        if style.paragraph_format.alignment is not None:
            return style.paragraph_format.alignment
        style = style.base_style
    return WD_ALIGN_PARAGRAPH.LEFT


def _style_chain_font_value(style, attribute: str):
    while style is not None:
        value = getattr(style.font, attribute)
        if value is not None:
            return value
        style = style.base_style
    return None


def _resolved_font_name(document: Document, paragraph, run) -> str:
    name = run.font.name
    if not name:
        name = _style_chain_font_value(run.style, "name")
    if not name:
        name = _style_chain_font_value(paragraph.style, "name")
    if not name:
        name = _style_chain_font_value(document.styles["Normal"], "name")
    return (name or "Unknown").strip()


def _resolved_font_size(document: Document, paragraph, run) -> float | None:
    size = run.font.size
    if size is None:
        size = _style_chain_font_value(run.style, "size")
    if size is None:
        size = _style_chain_font_value(paragraph.style, "size")
    if size is None:
        size = _style_chain_font_value(document.styles["Normal"], "size")
    return size.pt if size is not None else None


def _resolved_bold(paragraph, run) -> bool:
    value = run.bold
    if value is None:
        value = _style_chain_font_value(run.style, "bold")
    if value is None:
        value = _style_chain_font_value(paragraph.style, "bold")
    return bool(value)


def _resolved_rgb(paragraph, run) -> str:
    color = run.font.color.rgb
    if color is None:
        style = run.style
        while style is not None and color is None:
            color = style.font.color.rgb
            style = style.base_style
    if color is None:
        style = paragraph.style
        while style is not None and color is None:
            color = style.font.color.rgb
            style = style.base_style
    return str(color).upper() if color is not None else "000000"


def _relative_luminance(rgb: str) -> float:
    channels = [int(rgb[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_with_white(rgb: str) -> float:
    return 1.05 / (_relative_luminance(rgb) + 0.05)


def _document_text(document: Document) -> str:
    parts = [paragraph.text for paragraph in _body_paragraphs(document)]
    parts.extend(_part_text(part) for part in _header_footer_parts(document))
    return "\n".join(part for part in parts if part)


def _paragraph_format_value(paragraph, attribute: str):
    value = getattr(paragraph.paragraph_format, attribute)
    if value is not None:
        return value
    style = paragraph.style
    while style is not None:
        value = getattr(style.paragraph_format, attribute)
        if value is not None:
            return value
        style = style.base_style
    return None


def _length_points(value) -> float:
    return round(value.pt, 2) if value is not None and hasattr(value, "pt") else 0.0


def _list_signature(paragraph) -> tuple[str, str, str]:
    p_pr = paragraph._p.pPr
    num_pr = p_pr.numPr if p_pr is not None else None
    num_id = ""
    level = ""
    if num_pr is not None:
        if num_pr.numId is not None:
            num_id = str(num_pr.numId.val)
        if num_pr.ilvl is not None:
            level = str(num_pr.ilvl.val)
    return paragraph.style.style_id or paragraph.style.name, num_id, level


def _is_list_paragraph(paragraph) -> bool:
    style_text = f"{paragraph.style.style_id} {paragraph.style.name}".casefold()
    p_pr = paragraph._p.pPr
    return "list" in style_text or "bullet" in style_text or bool(p_pr is not None and p_pr.numPr is not None)


def _estimated_page_count(document: Document) -> int:
    estimated_pages = 0
    remaining_lines = 0.0
    for section in document.sections:
        page_height = section.page_height.inches if section.page_height is not None else 11.0
        top = section.top_margin.inches if section.top_margin is not None else 0.75
        bottom = section.bottom_margin.inches if section.bottom_margin is not None else 0.75
        usable_height = max(4.0, page_height - top - bottom)
        remaining_lines += usable_height * 72.0 / 13.0

    page_width = document.sections[0].page_width.inches if document.sections and document.sections[0].page_width is not None else 8.5
    left = document.sections[0].left_margin.inches if document.sections and document.sections[0].left_margin is not None else 0.75
    right = document.sections[0].right_margin.inches if document.sections and document.sections[0].right_margin is not None else 0.75
    usable_width = max(4.0, page_width - left - right)
    chars_per_line = max(45, int(usable_width * 9.5))
    consumed_lines = 0.0
    for paragraph in _body_paragraphs(document):
        text = paragraph.text.strip()
        if not text:
            consumed_lines += 0.35
            continue
        line_count = max(1, ceil(len(text) / chars_per_line))
        before = _length_points(_paragraph_format_value(paragraph, "space_before")) / 13.0
        after = _length_points(_paragraph_format_value(paragraph, "space_after")) / 13.0
        consumed_lines += line_count + before + after
    if remaining_lines <= 0:
        return 1
    estimated_pages = max(1, ceil(consumed_lines / (remaining_lines / max(1, len(document.sections)))))
    return estimated_pages


def _rendered_page_count(
    document: Document,
    *,
    exact: bool = True,
) -> tuple[int, str]:
    if not exact:
        return _estimated_page_count(document), "estimated"
    renderer = shutil.which("libreoffice") or shutil.which("soffice")
    if renderer:
        try:
            with tempfile.TemporaryDirectory(prefix="resume-report-pages-") as temp_dir:
                docx_path = Path(temp_dir) / "resume.docx"
                pdf_path = Path(temp_dir) / "resume.pdf"
                document.save(docx_path)
                # Give every conversion its own LibreOffice user profile. Reusing the
                # default profile can leave a lock after repeated report generation,
                # causing later requests to wait until the subprocess timeout.
                profile_dir = Path(temp_dir) / "libreoffice-profile"
                profile_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        renderer,
                        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--nofirststartwizard",
                        "--nolockcheck",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        temp_dir,
                        str(docx_path),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=20,
                )
                if pdf_path.exists():
                    pdf_bytes = pdf_path.read_bytes()
                    page_count = len(re.findall(rb"/Type\s*/Page\b", pdf_bytes))
                    if page_count > 0:
                        return page_count, "rendered"
        except Exception as exc:  # Renderer availability must not block report generation.
            logger.debug("Exact page rendering failed; using the estimated page count: %s", exc)
    return _estimated_page_count(document), "estimated"


def _typography_checks(document: Document) -> list[ReportCheck]:
    paragraphs = [paragraph for paragraph in _body_paragraphs(document) if paragraph.text.strip()]
    list_paragraphs = [paragraph for paragraph in paragraphs if _is_list_paragraph(paragraph)]
    list_signatures = Counter(_list_signature(paragraph) for paragraph in list_paragraphs)
    indentation_signatures = Counter(
        (
            _length_points(_paragraph_format_value(paragraph, "left_indent")),
            _length_points(_paragraph_format_value(paragraph, "right_indent")),
            _length_points(_paragraph_format_value(paragraph, "first_line_indent")),
        )
        for paragraph in list_paragraphs
    )
    body_paragraphs = [paragraph for paragraph in paragraphs if len(_words(paragraph.text)) >= 4]
    line_spacing_signatures = Counter(
        str(_paragraph_format_value(paragraph, "line_spacing") or "default")
        for paragraph in body_paragraphs
    )
    paragraph_spacing_signatures = Counter(
        (
            _length_points(_paragraph_format_value(paragraph, "space_before")),
            _length_points(_paragraph_format_value(paragraph, "space_after")),
        )
        for paragraph in body_paragraphs
    )

    headings = []
    for paragraph in paragraphs:
        normalized = paragraph.text.strip().casefold()
        if any(normalized == alias for _, aliases in _SECTION_HEADING_ALIASES for alias in aliases):
            run = next((item for item in paragraph.runs if item.text.strip()), None)
            headings.append(
                (
                    paragraph.style.style_id,
                    _resolved_font_name(document, paragraph, run) if run is not None else "Unknown",
                    _resolved_font_size(document, paragraph, run) if run is not None else None,
                    _resolved_bold(paragraph, run) if run is not None else False,
                    _length_points(_paragraph_format_value(paragraph, "space_before")),
                    _length_points(_paragraph_format_value(paragraph, "space_after")),
                )
            )
    heading_consistent = len(set(headings)) <= 1 if headings else False

    return [
        ReportCheck(
            "Bullet and list styles are consistent",
            "pass" if len(list_signatures) <= 1 else "warning",
            "All detected list paragraphs use the same bullet/list style."
            if len(list_signatures) <= 1
            else f"{len(list_signatures)} different list-style signatures were detected. Standardize bullet symbols and nesting levels.",
        ),
        ReportCheck(
            "Bullet indentation is consistent",
            "pass" if len(indentation_signatures) <= 1 else "warning",
            "All detected bullets use consistent indentation."
            if len(indentation_signatures) <= 1
            else f"{len(indentation_signatures)} bullet indentation patterns were detected. Align bullet left indents and hanging indents.",
        ),
        ReportCheck(
            "Line spacing is consistent throughout the resume",
            "pass" if len(line_spacing_signatures) <= 2 else "warning",
            f"{len(line_spacing_signatures)} line-spacing pattern(s) were detected across body text."
            + ("" if len(line_spacing_signatures) <= 2 else " Reduce unnecessary variation."),
        ),
        ReportCheck(
            "Paragraph spacing is consistent throughout the resume",
            "pass" if len(paragraph_spacing_signatures) <= 3 else "warning",
            f"{len(paragraph_spacing_signatures)} paragraph-spacing pattern(s) were detected across body text."
            + ("" if len(paragraph_spacing_signatures) <= 3 else " Standardize spacing before and after equivalent content."),
        ),
        ReportCheck(
            "Section-heading typography is consistent",
            "pass" if heading_consistent else "warning",
            "Detected section headings use a consistent font, size, emphasis, style, and spacing."
            if heading_consistent
            else "Section headings use inconsistent typography or could not all be identified. Standardize heading font, size, bolding, and spacing.",
        ),
    ]

_EXPORT_NAMES = (
    '_approved_resume_for_report',
    '_document_for_report',
    '_body_paragraphs',
    '_header_footer_parts',
    '_part_text',
    '_column_count',
    '_paragraph_alignment',
    '_style_chain_font_value',
    '_resolved_font_name',
    '_resolved_font_size',
    '_resolved_bold',
    '_resolved_rgb',
    '_relative_luminance',
    '_contrast_with_white',
    '_document_text',
    '_paragraph_format_value',
    '_length_points',
    '_list_signature',
    '_is_list_paragraph',
    '_estimated_page_count',
    '_rendered_page_count',
    '_typography_checks',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
