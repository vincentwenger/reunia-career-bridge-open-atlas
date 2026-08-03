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

_CONFIRMATION_GENERIC_RE = re.compile(
    r"(?:\b(?:throughout|over|during)\s+(?:my\s+)?\d+\+?\s+years?\b"
    r"|\bhas been (?:a |an )?(?:major|critical|constant|important|essential)\b"
    r"|\b(?:a major|an important) part of my responsibilities\b"
    r"|\bstrong communication has been\b"
    r"|\bperformance optimization has been\b"
    r"|\bwhile working at\b.*\bour team was implementing\b)",
    re.IGNORECASE,
)
_CONFIRMATION_ACTION_RE = re.compile(
    r"\b(?:analy[sz](?:e|ed|ing)|automate(?:d|s|ing)?|build|built|collaborat(?:e|ed|ing)|"
    r"configure(?:d|s|ing)?|create(?:d|s|ing)?|deliver(?:ed|s|ing)?|design(?:ed|s|ing)?|"
    r"develop(?:ed|s|ing)?|diagnos(?:e|ed|ing)|implement(?:ed|s|ing)?|improv(?:e|ed|ing)|"
    r"investigat(?:e|ed|ing)|lead|led|maintain(?:ed|s|ing)?|manag(?:e|ed|ing)|monitor(?:ed|s|ing)?|"
    r"optimi[sz](?:e|ed|ing)|partner(?:ed|s|ing)?|reduc(?:e|ed|ing)|resolv(?:e|ed|ing)|"
    r"review(?:ed|s|ing)?|streamlin(?:e|ed|ing)|support(?:ed|s|ing)?|test(?:ed|s|ing)?|"
    r"troubleshoot(?:ed|s|ing)?|tun(?:e|ed|ing)|validat(?:e|ed|ing)?)\b",
    re.IGNORECASE,
)
_CONFIRMATION_OUTCOME_RE = re.compile(
    r"\b(?:accuracy|availability|bottleneck|compliance|efficien(?:cy|t)|high availability|impact|"
    r"latency|performance|production|quality|reliability|response times?|risk|scalab(?:ility|le)|"
    r"throughput|timeliness|uptime)\b",
    re.IGNORECASE,
)
_CONFIRMATION_TECHNICAL_RE = re.compile(
    r"\b(?:API|AWS|CI/CD|database|execution plans?|index(?:es|ing)?|Jenkins|Oracle|pipeline|"
    r"PL/SQL|PostgreSQL|Python|query|queries|reporting|SQL|system|systems|workflow|workflows)\b",
    re.IGNORECASE,
)

# Candidate answers often contain conversational transitions that make sense in an
# interview response but not in a standalone resume bullet. Remove only leading
# discourse markers; the evidence-bearing clause remains unchanged and is then
# converted from first person to resume style below.
_CONFIRMATION_LEAD_IN_RE = re.compile(
    r"^(?:(?:from there|in addition|additionally|furthermore|moreover|after that|"
    r"as a result|for example|also)\s*[,;:]\s*)+",
    re.IGNORECASE,
)

# Confirmation answers frequently put useful context before the candidate's action,
# for example: "In a financial platform environment, I designed ...". Resume bullets
# scan better when the verified action leads and the context follows it.
_CONTEXTUAL_FIRST_PERSON_RE = re.compile(
    r"^(?P<context>(?:in|within|across|during|at|for|on)\b[^,]{2,120}),\s*"
    r"(?P<subject>I|we|our team)\s+(?P<body>.+)$",
    re.IGNORECASE,
)
_GENERIC_CONFIRMATION_CONTEXT_RE = re.compile(
    r"^(?:on the (?:infrastructure|technical|business|development) side|"
    r"from (?:an?|the) [^,]{1,40} perspective|"
    r"in (?:this|that|my|the) role|in this case)$",
    re.IGNORECASE,
)
_FOR_EXAMPLE_RE = re.compile(
    r"(?<=\S)\s*(?:[,;:]|[—–-])?\s*for example\s*,?\s*",
    re.IGNORECASE,
)

# Conservative opening-verb inflections used only for completed roles. The map
# changes grammatical form, never responsibility, scope, technology, or outcome.
_PAST_TENSE_ACTION_OPENERS = {
    "achieve": "Achieved",
    "achieves": "Achieved",
    "analyze": "Analyzed",
    "analyzes": "Analyzed",
    "automate": "Automated",
    "automates": "Automated",
    "build": "Built",
    "builds": "Built",
    "collaborate": "Collaborated",
    "collaborates": "Collaborated",
    "configure": "Configured",
    "configures": "Configured",
    "create": "Created",
    "creates": "Created",
    "deliver": "Delivered",
    "delivers": "Delivered",
    "design": "Designed",
    "designs": "Designed",
    "develop": "Developed",
    "develops": "Developed",
    "drive": "Drove",
    "drives": "Drove",
    "implement": "Implemented",
    "implements": "Implemented",
    "improve": "Improved",
    "improves": "Improved",
    "lead": "Led",
    "leads": "Led",
    "maintain": "Maintained",
    "maintains": "Maintained",
    "manage": "Managed",
    "manages": "Managed",
    "monitor": "Monitored",
    "monitors": "Monitored",
    "optimize": "Optimized",
    "optimizes": "Optimized",
    "orchestrate": "Orchestrated",
    "orchestrates": "Orchestrated",
    "oversee": "Oversaw",
    "oversees": "Oversaw",
    "partner": "Partnered",
    "partners": "Partnered",
    "reduce": "Reduced",
    "reduces": "Reduced",
    "resolve": "Resolved",
    "resolves": "Resolved",
    "review": "Reviewed",
    "reviews": "Reviewed",
    "streamline": "Streamlined",
    "streamlines": "Streamlined",
    "support": "Supported",
    "supports": "Supported",
    "test": "Tested",
    "tests": "Tested",
    "troubleshoot": "Troubleshot",
    "troubleshoots": "Troubleshot",
    "tune": "Tuned",
    "tunes": "Tuned",
    "validate": "Validated",
    "validates": "Validated",
}



# Resume experience bullets use a clean phrase style by default: a terminal
# period is omitted for a single-sentence bullet, while true multi-sentence
# bullets retain their final period. Intrinsic periods in abbreviations are not
# treated as optional sentence punctuation.
_INTRINSIC_TERMINAL_PERIOD_RE = re.compile(
    r"(?:\b(?:e\.g|i\.e|U\.S|U\.K|Inc|Corp|Ltd|Co|Dr|Mr|Mrs|Ms|Jr|Sr|No|St)|(?:\b[A-Z]\.){2,})\.$",
    re.IGNORECASE,
)
_INTERNAL_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?P<ending>[.!?])[\"'’”)]*\s+(?=[A-Z0-9])"
)
_SENTENCE_BOUNDARY_ABBREVIATIONS = {
    "e.g.",
    "i.e.",
    "u.s.",
    "u.k.",
    "inc.",
    "corp.",
    "ltd.",
    "co.",
    "dr.",
    "mr.",
    "mrs.",
    "ms.",
    "jr.",
    "sr.",
    "no.",
    "st.",
}


def _is_abbreviation_boundary(text: str, punctuation_index: int) -> bool:
    prefix = text[: punctuation_index + 1].rstrip()
    token_match = re.search(r"(?:[A-Za-z]\.){2,}$|[A-Za-z]+\.$", prefix)
    if not token_match:
        return False
    return token_match.group(0).casefold() in _SENTENCE_BOUNDARY_ABBREVIATIONS or bool(
        re.fullmatch(r"(?:[A-Za-z]\.){2,}", token_match.group(0))
    )


def bullet_has_multiple_complete_sentences(value: str) -> bool:
    """Return whether a bullet contains at least two sentence-like clauses.

    The check is intentionally conservative so periods in abbreviations such as
    ``e.g.`` or ``U.S.`` do not make a single resume bullet look multi-sentence.
    """

    text = " ".join((value or "").split()).strip()
    if not text:
        return False
    for match in _INTERNAL_SENTENCE_BOUNDARY_RE.finditer(text):
        punctuation_index = match.start("ending")
        if match.group("ending") == "." and _is_abbreviation_boundary(
            text, punctuation_index
        ):
            continue
        before = text[: punctuation_index + 1]
        after = text[match.end() :]
        if _word_count(before) >= 3 and _word_count(after) >= 3:
            return True
    return False


def normalize_resume_bullet_terminal_punctuation(value: str) -> str:
    """Apply Career Bridge's default no-period style to one resume bullet.

    Single-sentence bullets lose only an optional final period. Multi-sentence
    bullets and intrinsic abbreviation periods remain unchanged. Internal
    punctuation is never modified.
    """

    text = " ".join((value or "").split()).strip()
    if not text or not text.endswith("."):
        return text
    if _INTRINSIC_TERMINAL_PERIOD_RE.search(text):
        return text
    if bullet_has_multiple_complete_sentences(text):
        return text
    return text[:-1].rstrip()

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


def _confirmation_sentence_score(value: str, *, position: int) -> float:
    """Rank confirmation sentences by resume value rather than input order."""
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return -100.0
    words = _word_count(text)
    score = min(words, 28) / 12.0
    action_count = len(_CONFIRMATION_ACTION_RE.findall(text))
    outcome_count = len(_CONFIRMATION_OUTCOME_RE.findall(text))
    technical_count = len(_CONFIRMATION_TECHNICAL_RE.findall(text))
    score += min(action_count, 6) * 1.45
    score += min(outcome_count, 5) * 0.55
    score += min(technical_count, 5) * 1.15
    if re.search(r"\d|%", text):
        score += 2.0
    if text.count(",") >= 2 or ";" in text:
        score += 1.0
    if _CONFIRMATION_GENERIC_RE.search(text):
        score -= 6.0
    if words < 7:
        score -= 2.0
    # Later sentences commonly contain the concrete examples after a generic opening.
    score += min(position, 3) * 0.15
    return score


def _context_as_trailing_phrase(value: str) -> str:
    context = re.sub(r"\s+", " ", value).strip(" ,.;:")
    if not context or _GENERIC_CONFIRMATION_CONTEXT_RE.fullmatch(context):
        return ""
    return context[0].lower() + context[1:]


def _use_past_tense_opening(value: str) -> str:
    match = re.match(r"^(?P<verb>[A-Za-z]+)(?P<rest>\b.*)$", value)
    if not match:
        return value
    replacement = _PAST_TENSE_ACTION_OPENERS.get(match.group("verb").casefold())
    if not replacement:
        return value
    return replacement + match.group("rest")


def _resume_style_confirmation_sentence(
    value: str,
    *,
    use_past_tense: bool = True,
) -> str:
    """Convert first-person confirmation prose into an extractive resume clause."""
    text = re.sub(r"\s+", " ", value).strip().strip(" -*•▪◦‣\t")
    text = _CONFIRMATION_LEAD_IN_RE.sub("", text).strip()
    text = re.sub(
        r"^(?:while|during)\s+working\s+(?:at|for|with)\s+[^,]+,\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    trailing_context = ""
    contextual = _CONTEXTUAL_FIRST_PERSON_RE.match(text)
    if contextual:
        trailing_context = _context_as_trailing_phrase(contextual.group("context"))
        subject = contextual.group("subject")
        text = f"{subject} {contextual.group('body')}"

    replacements = (
        (r"^I\s+(?:have\s+)?been\s+responsible\s+for\s+", "Managed "),
        (r"^My responsibilities (?:have )?included\s+", "Managed "),
        (r"^I\s+(?:also\s+)?worked\s+with\s+", "Collaborated with "),
        (r"^I\s+(?:also\s+)?collaborated\s+with\s+", "Collaborated with "),
        (r"^(?:Our team|We)\s+was\s+implementing\s+", "Contributed to implementing "),
        (r"^(?:Our team|We)\s+implemented\s+", "Contributed to implementing "),
        (r"^I\s+(?:also\s+)?(?:have|had)\s+", ""),
        (r"^I\s+(?:also\s+|regularly\s+)?", ""),
        (r"^This (?:also )?involved\s+", "Included "),
        (r"^This (?:also )?included\s+", "Included "),
    )
    for pattern, replacement in replacements:
        updated = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        if updated != text:
            text = updated
            break
    text = re.sub(r"\bworked with\b", "collaborated with", text, flags=re.IGNORECASE)
    text = _FOR_EXAMPLE_RE.sub(", including ", text)
    text = _remove_explanatory_asides(text)
    text = text.strip(" .;:")
    if use_past_tense:
        text = _use_past_tense_opening(text)
    if trailing_context:
        text = f"{text.rstrip(' .;:')} {trailing_context}"
    if text:
        text = text[0].upper() + text[1:]
    return text


def _confirmation_sentences(value: str) -> list[str]:
    raw = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    sentences: list[str] = []
    for line in raw.split("\n"):
        _label, body = _clean_line(line)
        if not body:
            continue
        parts = re.split(r"(?<=[.!?])\s+", body)
        sentences.extend(part.strip() for part in parts if part.strip())
    if not sentences:
        _label, body = _clean_line(raw)
        if body:
            sentences.append(body)
    return sentences


def summarize_confirmation_answer_as_bullet(
    value: str,
    *,
    max_words: int = 35,
    use_past_tense: bool = True,
) -> str:
    """Create one evidence-grounded resume bullet from a full confirmation answer.

    Unlike ``normalize_resume_bullet_text``, this function does not assume that the
    first sentence is the strongest one. Candidates often begin with a broad summary
    and provide the concrete techniques, tools, and outcomes afterward. The function
    ranks the complete answer, converts the strongest clauses to resume style, and
    combines complementary details without inventing new facts.
    """
    sentences = _confirmation_sentences(value)
    if not sentences:
        return ""

    ranked: list[tuple[float, int, str, bool]] = []
    for index, sentence in enumerate(sentences):
        resume_sentence = _resume_style_confirmation_sentence(
            sentence,
            use_past_tense=use_past_tense,
        )
        if not resume_sentence:
            continue
        ranked.append(
            (
                _confirmation_sentence_score(sentence, position=index),
                index,
                resume_sentence,
                bool(_CONFIRMATION_GENERIC_RE.search(sentence)),
            )
        )
    if not ranked:
        return normalize_resume_bullet_text(value, max_words=max_words)

    ranked.sort(key=lambda item: (-item[0], item[1]))
    primary = ranked[0][2]
    secondary = ""
    primary_tokens = set(re.findall(r"[A-Za-z0-9+#./-]+", primary.casefold()))
    for score, _index, candidate, is_generic in ranked[1:]:
        if score <= 0 or is_generic:
            continue
        candidate_tokens = set(re.findall(r"[A-Za-z0-9+#./-]+", candidate.casefold()))
        overlap = len(primary_tokens & candidate_tokens) / max(1, len(candidate_tokens))
        if overlap < 0.72:
            secondary = candidate
            break

    limit = max(12, max_words)
    primary_part = _trim_to_word_limit(primary, limit).rstrip(" .;:")
    combined = primary_part
    if secondary:
        remaining = limit - _word_count(primary_part)
        # Add a second complete clause only when it fits. Cutting a second sentence
        # mid-phrase produces the same low-quality fragments this helper replaces.
        if remaining >= 8 and _word_count(secondary) <= remaining:
            combined = f"{primary_part}; {secondary.rstrip(' .;:')}"

    combined = re.sub(r"\s+", " ", combined).strip(" -*•▪◦‣\t")
    if combined and combined[-1] not in ".!?":
        combined += "."
    return combined


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
