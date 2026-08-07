from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module, module_exports

"""Formatting and ATS presentation checks."""

def _formatting_sections(
    document: Document | None,
    inspection_note: str | None,
    page_limit: int,
    *,
    exact_page_count: bool = True,
) -> list[ReportSubsection]:
    if document is None:
        unavailable = ReportCheck(
            "The resume document can be inspected",
            "warning",
            inspection_note or "The document was unavailable for inspection.",
        )
        return [
            ReportSubsection("Layout", [unavailable]),
            ReportSubsection("Font Check", [unavailable]),
            ReportSubsection("Typography Consistency", [unavailable]),
            ReportSubsection("Page Setup", [unavailable]),
        ]

    paragraphs = [paragraph for paragraph in _body_paragraphs(document) if paragraph.text.strip()]
    full_text = _document_text(document)

    column_counts = [_column_count(section) for section in document.sections]
    multi_column_sections = [index + 1 for index, count in enumerate(column_counts) if count > 1]

    long_paragraphs = [
        (index + 1, len(_words(paragraph.text)), paragraph.text.strip())
        for index, paragraph in enumerate(paragraphs)
        if len(_words(paragraph.text)) > 40
    ]
    long_preview = "; ".join(
        f'paragraph {index} ({count} words: "{text[:55]}…")'
        for index, count, text in long_paragraphs[:4]
    )

    body_drawing_count = len(document.element.body.xpath(".//w:drawing | .//w:pict"))
    header_footer_drawing_count = sum(
        len(part.element.xpath(".//w:drawing | .//w:pict"))
        for part in _header_footer_parts(document)
    )
    image_count = body_drawing_count + header_footer_drawing_count

    body_table_count = len(document.element.body.xpath(".//w:tbl"))
    header_footer_table_count = sum(
        len(part.element.xpath(".//w:tbl"))
        for part in _header_footer_parts(document)
    )
    table_count = body_table_count + header_footer_table_count

    text_paragraphs = [paragraph for paragraph in paragraphs if len(_words(paragraph.text)) >= 5]
    left_aligned = [
        paragraph
        for paragraph in text_paragraphs
        if _paragraph_alignment(paragraph) == WD_ALIGN_PARAGRAPH.LEFT
    ]
    left_ratio = len(left_aligned) / len(text_paragraphs) if text_paragraphs else 1.0
    if left_ratio >= 0.8:
        alignment_status: ReportStatus = "pass"
    elif left_ratio >= 0.6:
        alignment_status = "warning"
    else:
        alignment_status = "fail"

    layout_checks = [
        ReportCheck(
            "Your resume does not contain columns",
            "pass" if not multi_column_sections else "fail",
            "The resume uses a single-column layout, which is easier for ATS to parse."
            if not multi_column_sections
            else "Multiple columns were detected in section(s) "
            + ", ".join(map(str, multi_column_sections))
            + ". Some ATS have trouble accurately parsing columns.",
        ),
        ReportCheck(
            "Your paragraphs are not longer than 40 words",
            "pass" if not long_paragraphs else "warning",
            "Every paragraph contains 40 words or fewer."
            if not long_paragraphs
            else f"{len(long_paragraphs)} paragraph(s) exceed 40 words. Consider shortening them for readability: {long_preview}",
        ),
        ReportCheck(
            "Your resume does not contain images",
            "pass" if image_count == 0 else "fail",
            "No images or drawing objects were detected."
            if image_count == 0
            else f"The resume contains {image_count} image or drawing object(s), which can be ignored or misread by an ATS.",
        ),
        ReportCheck(
            "Your resume does not contain any tables",
            "pass" if table_count == 0 else "fail",
            "No tables were detected in the resume."
            if table_count == 0
            else f"The resume contains {table_count} table(s). Some ATS parse table content out of order or omit it.",
        ),
        ReportCheck(
            "Your resume primarily uses standardized left alignment for text sections",
            alignment_status,
            f"{len(left_aligned)} of {len(text_paragraphs)} text paragraph(s) ({left_ratio:.0%}) are left aligned. "
            "Left alignment is the most predictable option for ATS parsing and recruiter scanning.",
        ),
    ]

    unusual_characters = [
        character
        for character in full_text
        if not character.isalnum()
        and not character.isspace()
        and character not in _COMMON_PUNCTUATION
        and not unicodedata.category(character).startswith("M")
    ]
    unusual_counts = Counter(unusual_characters)
    unusual_limit = max(5, round(max(1, len(_words(full_text))) * 0.01))
    unusual_overused = len(unusual_characters) > unusual_limit

    total_characters = 0
    bold_characters = 0
    font_character_counts: Counter[str] = Counter()
    size_weighted_total = 0.0
    size_character_total = 0
    color_character_counts: Counter[str] = Counter()
    for paragraph in paragraphs:
        for run in paragraph.runs:
            text = run.text.strip()
            if not text:
                continue
            character_count = len(text)
            total_characters += character_count
            if _resolved_bold(paragraph, run):
                bold_characters += character_count
            font_name = _resolved_font_name(document, paragraph, run)
            font_character_counts[font_name] += character_count
            font_size = _resolved_font_size(document, paragraph, run)
            if font_size is not None:
                size_weighted_total += font_size * character_count
                size_character_total += character_count
            color_character_counts[_resolved_rgb(paragraph, run)] += character_count

    bold_ratio = bold_characters / total_characters if total_characters else 0.0
    if bold_ratio <= 0.2:
        bold_status: ReportStatus = "pass"
    elif bold_ratio <= 0.35:
        bold_status = "warning"
    else:
        bold_status = "fail"

    fonts = sorted(font_character_counts)
    font_count = len(fonts)
    if font_count <= 2:
        font_variety_status: ReportStatus = "pass"
    elif font_count == 3:
        font_variety_status = "warning"
    else:
        font_variety_status = "fail"
    nonstandard_fonts = sorted(font for font in fonts if font.casefold() not in _STANDARD_FONTS and font != "Unknown")

    low_contrast_colors = sorted(
        color for color in color_character_counts if re.fullmatch(r"[0-9A-F]{6}", color) and _contrast_with_white(color) < 4.5
    )
    unknown_colors = sorted(color for color in color_character_counts if not re.fullmatch(r"[0-9A-F]{6}", color))
    if low_contrast_colors:
        color_status: ReportStatus = "fail"
    elif unknown_colors:
        color_status = "warning"
    else:
        color_status = "pass"

    average_font_size = size_weighted_total / size_character_total if size_character_total else 0.0
    if 10.0 <= average_font_size <= 12.0:
        size_status: ReportStatus = "pass"
    elif 9.0 <= average_font_size <= 14.0:
        size_status = "warning"
    else:
        size_status = "fail"

    font_checks = [
        ReportCheck(
            "Special characters were not overused in your resume",
            "pass" if not unusual_overused else "warning",
            "No excessive decorative or unusual characters were detected."
            if not unusual_overused
            else f"{len(unusual_characters)} unusual character(s) were detected, above the suggested limit of {unusual_limit}: "
            + ", ".join(f"{character!r} × {count}" for character, count in unusual_counts.most_common(8)),
        ),
        ReportCheck(
            "Your resume does not contain too much bold styling",
            bold_status,
            f"Approximately {bold_ratio:.0%} of visible text is bold. Reserve bold styling mainly for your name, job titles, company names, and section headings.",
        ),
        ReportCheck(
            "All parts of the resume use an easy-to-read font color",
            color_status,
            "All detected text colors have sufficient contrast against a white page; black remains the safest ATS-compatible choice."
            if color_status == "pass"
            else "Low-contrast colors were detected: " + ", ".join(low_contrast_colors or unknown_colors) + ". Use black or another very dark color.",
        ),
        ReportCheck(
            "Your resume does not overuse different fonts",
            font_variety_status,
            f"The resume uses {font_count} detected font(s): {', '.join(fonts) if fonts else 'none detected'}. Keep the document to one or two fonts.",
        ),
        ReportCheck(
            "Your resume does not contain non-standard fonts",
            "pass" if not nonstandard_fonts else "fail",
            "All detected fonts are common, readable, and ATS-compatible."
            if not nonstandard_fonts
            else "Replace these non-standard fonts with Arial, Calibri, Times New Roman, Helvetica, or another common resume font: "
            + ", ".join(nonstandard_fonts),
        ),
        ReportCheck(
            "The average font size meets readability and ATS standards",
            size_status,
            f"The character-weighted average font size is approximately {average_font_size:.1f} pt. A 10-12 pt body-text range is generally readable and ATS-friendly.",
        ),
    ]

    header_parts = [part for part in _header_footer_parts(document) if isinstance(part, HeaderPart)]
    footer_parts = [part for part in _header_footer_parts(document) if isinstance(part, FooterPart)]
    headers_with_information = [part for part in header_parts if _part_text(part)]
    footers_with_information = [part for part in footer_parts if _part_text(part)]

    margin_rows: list[tuple[float, float, float, float]] = []
    for section in document.sections:
        values = (section.top_margin, section.bottom_margin, section.left_margin, section.right_margin)
        if all(value is not None for value in values):
            margin_rows.append(tuple(value.inches for value in values))
    margins_standard = bool(margin_rows) and all(0.5 <= value <= 1.25 for row in margin_rows for value in row)
    margins_consistent = bool(margin_rows) and all(
        max(row[index] for row in margin_rows) - min(row[index] for row in margin_rows) <= 0.05
        for index in range(4)
    )
    if margins_standard and margins_consistent:
        margin_status: ReportStatus = "pass"
    elif margins_standard or margins_consistent:
        margin_status = "warning"
    else:
        margin_status = "fail"
    margin_detail = "; ".join(
        f"section {index + 1}: top {row[0]:.2f}, bottom {row[1]:.2f}, left {row[2]:.2f}, right {row[3]:.2f} inches"
        for index, row in enumerate(margin_rows)
    )

    page_sizes: list[tuple[float, float]] = []
    for section in document.sections:
        if section.page_width is not None and section.page_height is not None:
            page_sizes.append((section.page_width.inches, section.page_height.inches))

    def standard_page_size(width: float, height: float) -> bool:
        ordered = sorted((width, height))
        letter = sorted((8.5, 11.0))
        a4 = sorted((8.27, 11.69))
        return all(abs(actual - expected) <= 0.12 for actual, expected in zip(ordered, letter)) or all(
            abs(actual - expected) <= 0.12 for actual, expected in zip(ordered, a4)
        )

    page_sizes_standard = bool(page_sizes) and all(standard_page_size(width, height) for width, height in page_sizes)
    page_size_detail = "; ".join(
        f"section {index + 1}: {width:.2f} × {height:.2f} inches"
        for index, (width, height) in enumerate(page_sizes)
    )
    page_count, page_count_method = _rendered_page_count(document, exact=exact_page_count)
    page_count_status: ReportStatus = "pass" if page_count <= page_limit else "fail"
    pagination_balance = estimate_resume_pagination(document)
    orphan_page_status: ReportStatus = (
        "fail" if pagination_balance.has_orphan_final_page else "pass"
    )

    page_setup_checks = [
        ReportCheck(
            "Your resume does not contain information in footers",
            "pass" if not footers_with_information else "fail",
            "No footer text was detected."
            if not footers_with_information
            else f"Information was detected in {len(footers_with_information)} footer part(s). Move essential resume content into the document body.",
        ),
        ReportCheck(
            "Your resume does not contain information in headers",
            "pass" if not headers_with_information else "fail",
            "No header text was detected."
            if not headers_with_information
            else f"Information was detected in {len(headers_with_information)} header part(s). Some ATS omit header content.",
        ),
        ReportCheck(
            "Your margin sizes are consistent and use standard dimensions",
            margin_status,
            (margin_detail or "Margin values could not be resolved.")
            + " Standard resume margins are generally between 0.5 and 1.25 inches and should remain consistent across sections.",
        ),
        ReportCheck(
            "Your document page size is standard",
            "pass" if page_sizes_standard else "fail",
            (page_size_detail or "Page dimensions could not be resolved.")
            + (" Letter or A4 page sizing was detected." if page_sizes_standard else " Use US Letter or A4 page sizing."),
        ),
        ReportCheck(
            f"The resume fits within the {page_limit}-page limit",
            page_count_status,
            f"The resume is {page_count} page(s) using a {page_count_method} page count. "
            + (
                f"It stays within the configured {page_limit}-page maximum."
                if page_count <= page_limit
                else f"It exceeds the configured {page_limit}-page maximum by {page_count - page_limit} page(s). Prune lower-priority content or tighten spacing without reducing readability."
            )
            + (" Install or enable a compatible document renderer for an exact count." if page_count_method == "estimated" else ""),
        ),
        ReportCheck(
            "The final page is not nearly empty",
            orphan_page_status,
            (
                "No orphan final page was detected."
                if not pagination_balance.has_orphan_final_page
                else (
                    f"The estimated final page contains {pagination_balance.last_page_substantive_lines} "
                    f"substantive line(s) and uses approximately {pagination_balance.last_page_fill_ratio:.0%} "
                    "of the available page height. Rebalance spacing or move a logical section so the "
                    "resume is either one page or a meaningful multi-page document."
                )
            ),
        ),
    ]

    if inspection_note:
        layout_checks.insert(
            0,
            ReportCheck("Formatting inspection note", "info", inspection_note),
        )

    return [
        ReportSubsection("Layout", layout_checks),
        ReportSubsection("Font Check", font_checks),
        ReportSubsection("Typography Consistency", _typography_checks(document)),
        ReportSubsection("Page Setup", page_setup_checks),
    ]

_EXPORT_NAMES = (
    '_formatting_sections',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
