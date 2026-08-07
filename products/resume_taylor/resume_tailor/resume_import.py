from __future__ import annotations

from io import BytesIO
from pathlib import PurePath
import re
from urllib.parse import urlparse

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from pypdf import PdfReader

from .models import CandidateProfile


SUPPORTED_RESUME_EXTENSIONS = {".json", ".pdf", ".docx", ".txt", ".md"}
MAX_RESUME_TEXT_CHARACTERS = 60_000

RESUME_IMPORT_SYSTEM = """You convert a candidate's existing resume into a structured CandidateProfile.

Evidence rules:
- Use only facts explicitly present in the supplied resume text.
- Never infer a skill, employer, title, credential, date, location, metric, or responsibility.
- Preserve international job titles, credentials, institution names, locations, dates, and terminology as written.
- Do not translate or normalize international titles yet; the Baseline Resume workflow handles that later.
- Preserve explicit professional profile links. Put a LinkedIn profile URL in contact.linkedin_url and a GitHub profile URL in contact.github_url.
- When a contact field is absent, return an empty string.
- Keep accomplishments as separate resume bullets. Do not merge unrelated claims.
- Create stable unique IDs: experiences EXP-001, EXP-002... and bullets EXP-001-B01, EXP-001-B02...
- Put explicit technologies and technical capabilities in hard_skills or tools_software.
- Put explicit collaboration or leadership capabilities in soft_skills.
- Put explicit sectors, regulations, and domain expertise in industry_knowledge.
- Put spoken languages only in languages.
- If the resume contains an explicit professional summary/profile section, copy its wording verbatim into current_summary. Do not rewrite, shorten, improve, or combine it with other resume content.
- If the resume has no explicit summary, write a short factual summary using only documented roles, years, sectors, and skills. Do not add adjectives or claims not supported by the text.
- Leave supplemental_evidence empty. Evidence questions will add confirmed facts later.

Return only the structured CandidateProfile."""


_SUMMARY_SECTION_HEADINGS = {
    "ABOUT ME",
    "CAREER PROFILE",
    "CAREER SUMMARY",
    "EXECUTIVE PROFILE",
    "EXECUTIVE SUMMARY",
    "PROFILE",
    "PROFILE SUMMARY",
    "PROFESSIONAL PROFILE",
    "PROFESSIONAL SUMMARY",
    "QUALIFICATIONS SUMMARY",
    "SUMMARY",
    "SUMMARY OF QUALIFICATIONS",
    # Common French resume headings.
    "A PROPOS",
    "PRESENTATION",
    "PROFIL",
    "PROFIL PROFESSIONNEL",
    "RESUME PROFESSIONNEL",
    "SOMMAIRE",
    "SOMMAIRE PROFESSIONNEL",
}

_RESUME_SECTION_HEADINGS = _SUMMARY_SECTION_HEADINGS | {
    "ACADEMIC BACKGROUND",
    "ACHIEVEMENTS",
    "ADDITIONAL INFORMATION",
    "CERTIFICATIONS",
    "COMMUNITY INVOLVEMENT",
    "CORE COMPETENCIES",
    "EDUCATION",
    "EMPLOYMENT",
    "EMPLOYMENT HISTORY",
    "EXPERIENCE",
    "LANGUAGES",
    "LICENSES",
    "PROFESSIONAL EXPERIENCE",
    "PROJECT EXPERIENCE",
    "PROJECTS",
    "PUBLICATIONS",
    "SKILLS",
    "TECHNICAL SKILLS",
    "TOOLS",
    "VOLUNTEER EXPERIENCE",
    "WORK EXPERIENCE",
    # Common French resume headings.
    "CERTIFICATIONS ET FORMATIONS",
    "COMPETENCES",
    "COMPETENCES TECHNIQUES",
    "EXPERIENCE PROFESSIONNELLE",
    "EXPERIENCES PROFESSIONNELLES",
    "FORMATION",
    "FORMATIONS",
    "LANGUES",
    "PROJETS",
}


def _normalized_section_heading(value: str) -> str:
    """Normalize a possible resume heading without changing source content."""

    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", ascii_text).upper().split())


def _looks_like_resume_section_heading(value: str) -> bool:
    stripped = str(value or "").strip()
    normalized = _normalized_section_heading(stripped)
    if not normalized:
        return False
    if normalized in _RESUME_SECTION_HEADINGS:
        return True

    # Unknown all-caps headings are a useful boundary for custom resume
    # sections, but normal summary sentences must never be treated as headings.
    words = normalized.split()
    letters = [character for character in stripped if character.isalpha()]
    return bool(
        1 <= len(words) <= 6
        and len(stripped) <= 70
        and letters
        and stripped == stripped.upper()
        and not stripped.endswith((".", "!", "?"))
    )


def extract_explicit_resume_summary(resume_text: str) -> str:
    """Return an explicitly labelled resume summary without rewriting it.

    Structured import still uses AI for employers, roles, skills, and education,
    but an existing summary is authored resume content. When a recognizable
    summary/profile heading is present, preserve the following text exactly
    instead of allowing the model to paraphrase it. If no explicit section is
    identifiable, return an empty string so the importer may build a factual
    fallback summary.
    """

    lines = [line.strip() for line in str(resume_text or "").splitlines()]
    start_index: int | None = None
    first_content = ""

    for index, line in enumerate(lines):
        if not line:
            continue
        normalized = _normalized_section_heading(line)
        if normalized in _SUMMARY_SECTION_HEADINGS:
            start_index = index + 1
            break

        # Preserve inline forms such as "Professional Summary: ...". Require a
        # visible separator so ordinary prose beginning with "Summary" is not
        # mistaken for a section label.
        inline = re.match(r"^(.{2,45}?)[\s]*[:\-–—][\s]*(.+)$", line)
        if inline and _normalized_section_heading(inline.group(1)) in _SUMMARY_SECTION_HEADINGS:
            start_index = index + 1
            first_content = inline.group(2).strip()
            break

    if start_index is None:
        return ""

    summary_lines: list[str] = [first_content] if first_content else []
    for line in lines[start_index:]:
        if not line:
            # Empty lines inside a summary are formatting, not evidence. Keep
            # reading until a real section boundary is found.
            continue
        if _looks_like_resume_section_heading(line):
            break
        summary_lines.append(line)
        if len(summary_lines) >= 12 or sum(len(item) for item in summary_lines) >= 2_500:
            break

    return "\n".join(item for item in summary_lines if item).strip()

def sanitize_imported_candidate_profile(
    profile: CandidateProfile,
    resume_text: str,
) -> tuple[CandidateProfile, list[str]]:
    """Remove untraceable extractor output without rejecting the whole upload.

    Resume replacement must be transactional: the newly uploaded document is the
    only evidence source.  A model-generated summary sentence, skill, bullet, or
    field that fails grounding is omitted rather than allowing stale evidence or
    blocking the entire re-import.
    """

    from .grounding import validate_candidate_claim

    source = str(resume_text or "")
    cleaned = profile.model_copy(deep=True)
    removed: list[str] = []

    def grounded(value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        return not validate_candidate_claim(
            text,
            [source],
            require_overlap=True,
            block_single_unsupported=True,
        )

    summary_sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+|\n+", cleaned.current_summary)
        if item.strip()
    ]
    kept_summary = [item for item in summary_sentences if grounded(item)]
    if len(kept_summary) != len(summary_sentences):
        removed.append("professional summary wording")
    cleaned.current_summary = " ".join(kept_summary)

    for field_name in (
        "hard_skills",
        "soft_skills",
        "tools_software",
        "industry_knowledge",
        "languages",
    ):
        values = list(getattr(cleaned.skills, field_name))
        kept = [value for value in values if grounded(value)]
        if len(kept) != len(values):
            removed.append(field_name.replace("_", " "))
        setattr(cleaned.skills, field_name, kept)

    retained_education = []
    for item in cleaned.education:
        for field_name in ("credential", "institution", "location", "date", "detail"):
            value = str(getattr(item, field_name) or "").strip()
            if value and not grounded(value):
                setattr(item, field_name, "")
                removed.append(f"education {field_name}")
        if any(
            str(getattr(item, field_name) or "").strip()
            for field_name in ("credential", "institution", "date", "detail")
        ):
            retained_education.append(item)
        else:
            removed.append("education record")
    cleaned.education = retained_education

    retained_experiences = []
    for experience in cleaned.experiences:
        for field_name in ("employer", "location", "dates", "title"):
            value = str(getattr(experience, field_name) or "").strip()
            if value and not grounded(value):
                setattr(experience, field_name, "")
                removed.append(f"experience {field_name}")
        bullets = []
        for bullet in experience.bullets:
            if grounded(bullet.text):
                bullets.append(bullet)
            else:
                removed.append("experience bullet")
        experience.bullets = bullets
        if any(
            (
                experience.employer.strip(),
                experience.title.strip(),
                experience.dates.strip(),
                bool(experience.bullets),
            )
        ):
            retained_experiences.append(experience)
        else:
            removed.append("experience record")
    cleaned.experiences = retained_experiences
    cleaned.supplemental_evidence = []

    # Keep the warning compact and deterministic for the route and logs.
    return cleaned, sorted(set(removed))


def build_resume_import_prompt(resume_text: str, filename: str) -> str:
    return f"""Source file: {filename}

Convert the following resume into the CandidateProfile schema while following every evidence rule.

<resume>
{resume_text}
</resume>"""


def resume_extension(filename: str) -> str:
    return PurePath(filename or "").suffix.casefold()


def _docx_story_parts(document: Document):
    """Yield document, header, and footer parts that may contain resume text.

    Contact details are frequently placed in a Word header or text box. Reading
    only ``document.paragraphs`` misses those locations and also loses the target
    URL behind hyperlink labels such as "LinkedIn" or "GitHub".
    """

    supported_part = re.compile(
        r"^/word/(?:document|header\d+|footer\d+|footnotes|endnotes)\.xml$",
        re.IGNORECASE,
    )
    for part in document.part.package.parts:
        if supported_part.match(str(part.partname)) and getattr(part, "element", None) is not None:
            yield part


def _element_text(element) -> str:
    parts: list[str] = []
    for node in element.iter():
        if node.tag == qn("w:t"):
            parts.append(node.text or "")
        elif node.tag == qn("w:tab"):
            parts.append("\t")
        elif node.tag in {qn("w:br"), qn("w:cr")}:
            parts.append("\n")
    return "".join(parts).strip()


def _relationship_target(part, relationship_id: str) -> str:
    if not relationship_id:
        return ""
    relationship = part.rels.get(relationship_id)
    if relationship is None or relationship.reltype != RT.HYPERLINK:
        return ""
    return str(relationship.target_ref or "").strip()


def _docx_text_with_hyperlinks(data: bytes) -> str:
    document = Document(BytesIO(data))
    lines: list[str] = []
    discovered_links: list[str] = []

    for part in _docx_story_parts(document):
        for paragraph in part.element.iter(qn("w:p")):
            paragraph_text = _element_text(paragraph)
            paragraph_links: list[str] = []
            for hyperlink in paragraph.iter(qn("w:hyperlink")):
                target = _relationship_target(part, hyperlink.get(qn("r:id"), ""))
                if target and target not in paragraph_links:
                    paragraph_links.append(target)
            for target in paragraph_links:
                if target not in discovered_links:
                    discovered_links.append(target)
            if paragraph_text:
                missing_targets = [
                    target for target in paragraph_links if target.casefold() not in paragraph_text.casefold()
                ]
                if missing_targets:
                    paragraph_text += " [" + "] [".join(missing_targets) + "]"
                lines.append(paragraph_text)

        # Shape and icon hyperlinks can be relationship-backed without a
        # conventional w:hyperlink element. Preserve every external hyperlink
        # relationship from the story part as a final deterministic fallback.
        for relationship in part.rels.values():
            if relationship.reltype != RT.HYPERLINK:
                continue
            target = str(relationship.target_ref or "").strip()
            if target and target not in discovered_links:
                discovered_links.append(target)

    if discovered_links:
        lines.append("Embedded professional links:")
        lines.extend(discovered_links)
    return "\n".join(lines)


_URL_PATTERN = re.compile(
    r"(?:(?:https?://|www\.)[^\s<>\"']+|(?:linkedin\.com|github\.com)/[^\s<>\"']+)",
    re.IGNORECASE,
)
_TRAILING_URL_PUNCTUATION = ".,;:!?)]}>"


def _normalized_url(candidate: str) -> str:
    value = str(candidate or "").strip().strip("<>{}[]()\"'")
    value = value.rstrip(_TRAILING_URL_PUNCTUATION)
    if not value:
        return ""
    if value.casefold().startswith("www.") or value.casefold().startswith(
        ("linkedin.com/", "github.com/")
    ):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return ""
    return value


def professional_contact_urls(resume_text: str) -> dict[str, str]:
    """Return explicit LinkedIn and GitHub profile URLs found in resume text."""

    result = {"linkedin_url": "", "github_url": ""}
    for match in _URL_PATTERN.finditer(str(resume_text or "")):
        candidate = _normalized_url(match.group(0))
        if not candidate:
            continue
        hostname = (urlparse(candidate).hostname or "").casefold().removeprefix("www.")
        if hostname == "linkedin.com" or hostname.endswith(".linkedin.com"):
            result["linkedin_url"] = result["linkedin_url"] or candidate
        elif hostname == "github.com" or hostname.endswith(".github.com"):
            result["github_url"] = result["github_url"] or candidate
    return result


def inherit_professional_contact_urls(
    profile: CandidateProfile,
    source_profile: CandidateProfile,
) -> CandidateProfile:
    """Fill missing LinkedIn/GitHub URLs from another verified profile copy.

    Application workflows keep several immutable or stage-specific profile
    snapshots. Older records may have been created before professional links
    were preserved during Word import. This helper repairs only missing contact
    URLs and never replaces an application-specific value.
    """

    restored = profile.model_copy(deep=True)
    source_contact = source_profile.contact
    if (
        not restored.contact.linkedin_url.strip()
        and source_contact.linkedin_url.strip()
    ):
        restored.contact.linkedin_url = source_contact.linkedin_url.strip()
        restored.contact.linkedin_label = (
            restored.contact.linkedin_label.strip()
            or source_contact.linkedin_label.strip()
            or "LinkedIn"
        )
    if (
        not restored.contact.github_url.strip()
        and source_contact.github_url.strip()
    ):
        restored.contact.github_url = source_contact.github_url.strip()
        restored.contact.github_label = (
            restored.contact.github_label.strip()
            or source_contact.github_label.strip()
            or "GitHub"
        )
    return restored


def restore_professional_contact_urls(
    profile: CandidateProfile,
    resume_text: str,
) -> CandidateProfile:
    """Fill professional profile URLs deterministically when AI omits them.

    The AI remains responsible for the full structured import, but hyperlink
    preservation must not depend on whether it notices a URL. Existing structured
    values are never replaced.
    """

    urls = professional_contact_urls(resume_text)
    restored = profile.model_copy(deep=True)
    if not restored.contact.linkedin_url.strip() and urls["linkedin_url"]:
        restored.contact.linkedin_url = urls["linkedin_url"]
        restored.contact.linkedin_label = restored.contact.linkedin_label.strip() or "LinkedIn"
    if not restored.contact.github_url.strip() and urls["github_url"]:
        restored.contact.github_url = urls["github_url"]
        restored.contact.github_label = restored.contact.github_label.strip() or "GitHub"
    return restored


def extract_resume_text(data: bytes, filename: str) -> str:
    extension = resume_extension(filename)
    if extension not in SUPPORTED_RESUME_EXTENSIONS - {".json"}:
        raise ValueError("Supported resume formats are PDF, Word (.docx), text, Markdown, or Verified Resume Evidence JSON.")
    if not data:
        raise ValueError("The uploaded resume is empty.")

    if extension == ".pdf":
        try:
            reader = PdfReader(BytesIO(data))
            text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
        except Exception as exc:
            raise ValueError(f"The PDF resume could not be read: {exc}") from exc
    elif extension == ".docx":
        try:
            text = _docx_text_with_hyperlinks(data)
        except Exception as exc:
            raise ValueError(f"The Word resume could not be read: {exc}") from exc
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")

    normalized = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
    if len(normalized) < 40:
        raise ValueError(
            "Very little text could be extracted from this resume. For a scanned PDF, upload a text-based PDF, Word document, or text file."
        )
    return normalized[:MAX_RESUME_TEXT_CHARACTERS]
