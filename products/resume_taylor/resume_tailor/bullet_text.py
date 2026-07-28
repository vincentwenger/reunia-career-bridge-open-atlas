from __future__ import annotations

import re

# Resume bullets are plain text. Markdown often appears when a candidate pastes an
# AI-generated explanation into a confirmation field; it must never flow into the
# visible resume as nested headings or sublists.
_MARKDOWN_LINE_PREFIX_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|[-+*•▪◦‣]\s+|\d+[.)]\s+)", re.UNICODE
)
_MARKDOWN_DECORATION_RE = re.compile(r"(?:\*\*|__|`)")
_ITALIC_MARKER_RE = re.compile(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)")
_HEADING_ONLY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 &/()+#.-]{0,70}:$")
_LABEL_WITH_TEXT_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9 &/()+#.-]{1,70}):\s*(?P<body>.+)$"
)
_PRIORITY_LABELS = (
    "overview",
    "summary",
    "accomplishment",
    "achievement",
    "impact",
    "result",
    "responsibility",
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


_TRAILING_WEAK_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "using",
    "via",
    "with",
}


def has_bullet_structure_artifacts(value: str) -> bool:
    """Return whether text contains formatting that cannot belong in one bullet."""
    text = value or ""
    if "\n" in text or "\r" in text:
        return True
    stripped = text.lstrip()
    if _MARKDOWN_LINE_PREFIX_RE.match(stripped):
        return True
    if _MARKDOWN_DECORATION_RE.search(text) or _ITALIC_MARKER_RE.search(text):
        return True
    return False


def _clean_line(value: str) -> tuple[str, str]:
    """Return ``(label, body)`` for one pasted line after removing list syntax."""
    line = value.strip()
    # Pasted AI text can stack markers (for example ``• * **Heading:**``).
    # Remove all leading list markers, then remove emphasis and check once more.
    for _ in range(4):
        cleaned = _MARKDOWN_LINE_PREFIX_RE.sub("", line).strip()
        if cleaned == line:
            break
        line = cleaned
    line = _MARKDOWN_DECORATION_RE.sub("", line)
    line = _ITALIC_MARKER_RE.sub("", line)
    line = _MARKDOWN_LINE_PREFIX_RE.sub("", line).strip()
    line = re.sub(r"\s+", " ", line).strip()
    if not line or _HEADING_ONLY_RE.fullmatch(line):
        return "", ""

    match = _LABEL_WITH_TEXT_RE.match(line)
    if match:
        label = re.sub(r"\s+", " ", match.group("label")).strip()
        body = match.group("body").strip()
        # Remove explanatory labels such as "Automation Overview:" from the
        # visible bullet, while retaining sentence colons such as "Result: 40%".
        if len(label.split()) <= 7:
            return label, body
    return "", line


def _first_complete_thought(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    return sentence or text


def _remove_explanatory_asides(value: str) -> str:
    text = value
    # Long em-dash/parenthetical expansions are common in pasted AI explanations.
    # Removing them preserves the user's main claim while improving scanability.
    text = re.sub(r"\s*[—–-]\s*from\b.*?\s*[—–-]\s*to\b", " to", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(([^)]{35,})\)", "", text)
    text = re.sub(r"\bthe entire\b", "the", text, flags=re.IGNORECASE)
    text = re.sub(r"\bin order to\b", "to", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _trim_to_word_limit(value: str, max_words: int) -> str:
    text = value.strip()
    if _word_count(text) <= max_words:
        return text

    text = _remove_explanatory_asides(text)
    if _word_count(text) <= max_words:
        return text

    # Prefer a complete clause near the limit instead of cutting mid-thought.
    clauses = re.split(r"(?<=[;:])\s+|\s+[—–]\s+", text)
    accumulated: list[str] = []
    for clause in clauses:
        candidate = " ".join([*accumulated, clause]).strip()
        if _word_count(candidate) > max_words:
            break
        accumulated.append(clause)
    if accumulated and _word_count(" ".join(accumulated)) >= 12:
        return " ".join(accumulated).rstrip(";:,. ")

    tokens = text.split()
    kept = tokens[:max_words]
    while kept and re.sub(r"[^A-Za-z]", "", kept[-1]).casefold() in _TRAILING_WEAK_WORDS:
        kept.pop()
    return " ".join(kept).rstrip(";:,. ")


def normalize_resume_bullet_text(value: str, *, max_words: int = 35) -> str:
    """Convert pasted prose/markdown into one plain, recruiter-readable bullet.

    The function is intentionally extractive rather than generative: it selects the
    strongest complete statement already supplied by the candidate or model, removes
    formatting labels, and applies a conservative length cap without adding claims.
    """
    raw = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    candidates: list[tuple[str, str]] = []
    for part in raw.split("\n"):
        label, body = _clean_line(part)
        if body:
            candidates.append((label, body))

    if not candidates:
        _, fallback = _clean_line(raw)
        candidates = [("", fallback)] if fallback else []
    if not candidates:
        return ""

    preferred = next(
        (
            body
            for label, body in candidates
            if any(token in label.casefold() for token in _PRIORITY_LABELS)
        ),
        "",
    )
    chosen = preferred or next(
        (body for _, body in candidates if _word_count(body) >= 5),
        candidates[0][1],
    )
    chosen = _first_complete_thought(chosen)
    chosen = _MARKDOWN_DECORATION_RE.sub("", chosen)
    chosen = _ITALIC_MARKER_RE.sub("", chosen)
    chosen = _MARKDOWN_LINE_PREFIX_RE.sub("", chosen)
    chosen = re.sub(r"\s+", " ", chosen).strip()
    chosen = _trim_to_word_limit(chosen, max(8, max_words))
    chosen = chosen.strip(" -*•▪◦‣\t")
    if chosen and chosen[-1] not in ".!?":
        chosen += "."
    return chosen
