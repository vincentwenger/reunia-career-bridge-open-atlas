from __future__ import annotations

import html
import re
from difflib import SequenceMatcher

_TOKEN_PATTERN = re.compile(r"\s+|[\w]+(?:[’'-][\w]+)*|[^\w\s]", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Split text into words, punctuation, and whitespace while preserving layout."""
    return _TOKEN_PATTERN.findall(text)


def _escape_tokens(tokens: list[str]) -> str:
    return "".join(html.escape(token) for token in tokens)


def build_word_diff(original: str, proposed: str) -> tuple[str, str]:
    """Return safe HTML for side-by-side original and proposed word-level diffs.

    Removed/replaced source text receives ``diff-removed`` and added/replacement
    proposed text receives ``diff-added``. Unchanged text is escaped and left
    unstyled. The returned fragments are safe to place inside trusted wrapper
    markup in a trusted Jinja template using the ``safe`` filter.
    """
    original_tokens = _tokenize(original)
    proposed_tokens = _tokenize(proposed)
    matcher = SequenceMatcher(a=original_tokens, b=proposed_tokens, autojunk=False)

    original_parts: list[str] = []
    proposed_parts: list[str] = []

    for operation, i1, i2, j1, j2 in matcher.get_opcodes():
        original_segment = _escape_tokens(original_tokens[i1:i2])
        proposed_segment = _escape_tokens(proposed_tokens[j1:j2])

        if operation == "equal":
            original_parts.append(original_segment)
            proposed_parts.append(proposed_segment)
        elif operation == "delete":
            original_parts.append(f'<span class="diff-removed">{original_segment}</span>')
        elif operation == "insert":
            proposed_parts.append(f'<span class="diff-added">{proposed_segment}</span>')
        elif operation == "replace":
            original_parts.append(f'<span class="diff-removed">{original_segment}</span>')
            proposed_parts.append(f'<span class="diff-added">{proposed_segment}</span>')

    return "".join(original_parts), "".join(proposed_parts)
