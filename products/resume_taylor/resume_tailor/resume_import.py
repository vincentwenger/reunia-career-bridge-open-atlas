from __future__ import annotations

from io import BytesIO
from pathlib import PurePath

from docx import Document
from pypdf import PdfReader


SUPPORTED_RESUME_EXTENSIONS = {".json", ".pdf", ".docx", ".txt", ".md"}
MAX_RESUME_TEXT_CHARACTERS = 60_000

RESUME_IMPORT_SYSTEM = """You convert a candidate's existing resume into a structured CandidateProfile.

Evidence rules:
- Use only facts explicitly present in the supplied resume text.
- Never infer a skill, employer, title, credential, date, location, metric, or responsibility.
- Preserve international job titles, credentials, institution names, locations, dates, and terminology as written.
- Do not translate or normalize international titles yet; the Baseline Resume workflow handles that later.
- When a contact field is absent, return an empty string.
- Keep accomplishments as separate resume bullets. Do not merge unrelated claims.
- Create stable unique IDs: experiences EXP-001, EXP-002... and bullets EXP-001-B01, EXP-001-B02...
- Put explicit technologies and technical capabilities in hard_skills or tools_software.
- Put explicit collaboration or leadership capabilities in soft_skills.
- Put explicit sectors, regulations, and domain expertise in industry_knowledge.
- Put spoken languages only in languages.
- If the resume has no explicit summary, write a short factual summary using only documented roles, years, sectors, and skills. Do not add adjectives or claims not supported by the text.
- Leave supplemental_evidence empty. Evidence questions will add confirmed facts later.

Return only the structured CandidateProfile."""


def build_resume_import_prompt(resume_text: str, filename: str) -> str:
    return f"""Source file: {filename}

Convert the following resume into the CandidateProfile schema while following every evidence rule.

<resume>
{resume_text}
</resume>"""


def resume_extension(filename: str) -> str:
    return PurePath(filename or "").suffix.casefold()


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
            document = Document(BytesIO(data))
            parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            text = "\n".join(parts)
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
