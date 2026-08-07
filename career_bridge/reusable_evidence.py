"""Reusable confirmation evidence shared across Career Bridge applications.

Matching is based on the factual evidence topic, not only literal question wording.
A stored answer may therefore satisfy a rephrased question about the same skill or
responsibility. More demanding questions are reused automatically only when the
saved answer already contains the requested concrete detail; otherwise the caller
can prefill a targeted follow-up with the existing answer.
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

MATCH_THRESHOLD = 0.86

_STOP_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "brief",
    "briefly",
    "can",
    "could",
    "confirm",
    "describe",
    "detail",
    "detailed",
    "details",
    "did",
    "direct",
    "do",
    "does",
    "example",
    "examples",
    "experience",
    "experiences",
    "explain",
    "for",
    "from",
    "have",
    "how",
    "i",
    "in",
    "is",
    "knowledge",
    "it",
    "of",
    "on",
    "or",
    "personal",
    "personally",
    "please",
    "previous",
    "provide",
    "related",
    "relevant",
    "role",
    "roles",
    "specific",
    "specifically",
    "particular",
    "particularly",
    "state",
    "tell",
    "that",
    "the",
    "this",
    "to",
    "used",
    "using",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
    "work",
    "worked",
    "you",
    "your",
}

_TOKEN_ALIASES = {
    "built": "develop",
    "build": "develop",
    "building": "develop",
    "create": "develop",
    "created": "develop",
    "creating": "develop",
    "development": "develop",
    "developments": "develop",
    "developed": "develop",
    "developing": "develop",
    "etl": "pipeline",
    "elt": "pipeline",
    "processes": "process",
}

_PHRASE_ALIASES = (
    (re.compile(r"\banti[\s-]+money[\s-]+laundering\b", re.IGNORECASE), "aml"),
)

_SPECIFIC_DETAIL_PATTERNS = (
    r"\bspecific example",
    r"\bdetail(?:ed|s)?\b",
    r"\bexamples?\b",
    r"\bwhat (?:you|did you) (?:personally )?do\b",
    r"\bhow (?:you|did you)\b",
    r"\btools? (?:or|and) techniques?\b",
    r"\bresult(?:s)?\b",
    r"\boutcome(?:s)?\b",
    r"\bscope\b",
)

_GENERIC_ANSWER_TOKENS = {
    "confirm",
    "direct",
    "experience",
    "project",
    "projects",
    "responsibility",
    "responsibilities",
    "role",
    "roles",
    "task",
    "tasks",
    "team",
    "teams",
    "work",
}


def normalize_evidence_text(value: str) -> str:
    """Return stable lowercase words for matching and fingerprints.

    Question intent words are handled separately from the factual topic. Common
    professional abbreviations and their expanded forms are normalized here so
    Career Evidence Library answers remain reusable across target-market wording.
    """

    normalized = str(value or "").casefold()
    for pattern, replacement in _PHRASE_ALIASES:
        normalized = pattern.sub(replacement, normalized)
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def _canonical_evidence_token(token: str) -> str:
    """Collapse safe grammatical variants without pretending to understand prose."""

    value = _TOKEN_ALIASES.get(token, token)
    if value != token:
        return value
    if len(value) > 5 and value.endswith("ies"):
        value = value[:-3] + "y"
    elif len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
        if len(value) > 3 and value[-1:] == value[-2:-1]:
            value = value[:-1]
    elif len(value) > 4 and value.endswith("ed"):
        value = value[:-2]
    elif (
        len(value) > 4
        and value.endswith("s")
        and not value.endswith(("ss", "us", "is"))
    ):
        value = value[:-1]
    return _TOKEN_ALIASES.get(value, value)


def evidence_tokens(value: str) -> set[str]:
    """Return distinctive canonical tokens while retaining technical terms."""

    tokens: set[str] = set()
    for token in normalize_evidence_text(value).split():
        if token in _STOP_WORDS or (len(token) <= 2 and not token.isdigit()):
            continue
        canonical = _canonical_evidence_token(token)
        if canonical and canonical not in _STOP_WORDS:
            tokens.add(canonical)
    return tokens


def evidence_answer_key(question: str, requirement: str = "") -> str:
    """Create a deterministic key for an exact reusable question."""

    normalized_question = normalize_evidence_text(question)
    normalized_requirement = normalize_evidence_text(requirement)
    payload = f"{normalized_question}\n{normalized_requirement}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def answer_types_compatible(left: str, right: str) -> bool:
    """Return whether two question types can describe the same career evidence."""

    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if not left_value or not right_value or left_value == right_value:
        return True
    yes_no_types = {"yes_no", "yes_no_with_details"}
    if left_value in yes_no_types and right_value in yes_no_types:
        return True
    evidence_text_types = yes_no_types | {"short_text", "long_text"}
    return left_value in evidence_text_types and right_value in evidence_text_types


def question_requests_specific_detail(
    question: str,
    *,
    details_prompt: str = "",
    answer_type: str = "",
) -> bool:
    """Detect questions that ask for an example rather than a general confirmation."""

    if str(answer_type or "").strip() == "long_text":
        return True
    normalized = normalize_evidence_text(f"{question} {details_prompt}")
    return any(re.search(pattern, normalized) for pattern in _SPECIFIC_DETAIL_PATTERNS)


def answer_has_specific_evidence(
    answer_text: str,
    *,
    question: str = "",
    requirement: str = "",
) -> bool:
    """Conservatively detect whether an answer contains usable example-level facts."""

    normalized = normalize_evidence_text(answer_text)
    words = normalized.split()
    if len(words) < 8:
        return False

    topic_tokens = evidence_tokens(question) | evidence_tokens(requirement)
    answer_topic_tokens = evidence_tokens(answer_text)
    supporting_tokens = {
        token
        for token in answer_topic_tokens - topic_tokens
        if token not in _GENERIC_ANSWER_TOKENS
    }
    if len(supporting_tokens) >= 3:
        return True
    return len(words) >= 12 and len(supporting_tokens) >= 2


def stored_answer_fully_satisfies(
    question: str,
    requirement: str,
    stored: Mapping[str, Any],
    *,
    answer_type: str = "",
    details_prompt: str = "",
) -> bool:
    """Return whether a semantic match can be reused without another question."""

    yes_no = stored.get("yes_no")
    if yes_no is False:
        return True
    answer_text = str(stored.get("answer_text") or "").strip()
    if not answer_text:
        return False
    if not question_requests_specific_detail(
        question,
        details_prompt=details_prompt,
        answer_type=answer_type,
    ):
        return True
    return answer_has_specific_evidence(
        answer_text,
        question=question,
        requirement=requirement,
    )


def question_match_score(
    question: str,
    requirement: str,
    stored: Mapping[str, Any],
    *,
    answer_type: str = "",
) -> float:
    """Score whether one stored answer represents the same factual question."""

    stored_type = str(stored.get("answer_type") or "")
    if answer_type and stored_type and not answer_types_compatible(answer_type, stored_type):
        return 0.0

    normalized_question = normalize_evidence_text(question)
    stored_question = normalize_evidence_text(
        str(stored.get("normalized_question") or stored.get("question") or "")
    )
    if not normalized_question or not stored_question:
        return 0.0
    if normalized_question == stored_question:
        return 1.0

    current_tokens = evidence_tokens(question)
    stored_tokens = evidence_tokens(stored_question)
    if not current_tokens or not stored_tokens:
        return 0.0

    intersection = current_tokens & stored_tokens
    union = current_tokens | stored_tokens
    jaccard = len(intersection) / max(1, len(union))
    containment = len(intersection) / max(1, min(len(current_tokens), len(stored_tokens)))
    sequence = SequenceMatcher(None, normalized_question, stored_question).ratio()

    # Once generic question intent has been removed, identical topic tokens are a
    # high-confidence semantic match even when one question asks broadly and the
    # other asks for confirmation, examples, or additional detail. This is the
    # intended Career Evidence Library behavior for pairs such as AML processes /
    # anti-money-laundering processes and data-pipeline development.
    if current_tokens == stored_tokens:
        return max(0.96, sequence)

    current_requirement = normalize_evidence_text(requirement)
    stored_requirement = normalize_evidence_text(
        str(stored.get("normalized_requirement") or stored.get("requirement") or "")
    )
    requirement_score = 0.0
    if current_requirement and stored_requirement:
        if current_requirement == stored_requirement:
            requirement_score = 1.0
        else:
            current_requirement_tokens = evidence_tokens(current_requirement)
            stored_requirement_tokens = evidence_tokens(stored_requirement)
            if current_requirement_tokens and stored_requirement_tokens:
                overlap = current_requirement_tokens & stored_requirement_tokens
                requirement_score = len(overlap) / max(
                    1, min(len(current_requirement_tokens), len(stored_requirement_tokens))
                )

    # Near matches must share at least two distinctive subject terms. Generic
    # intent words such as "confirm", "experience", and "examples" are removed
    # before this check, so they cannot make unrelated questions appear similar.
    if len(intersection) < 2:
        return 0.0

    score = (0.45 * containment) + (0.35 * jaccard) + (0.10 * sequence) + (
        0.10 * requirement_score
    )
    if containment < 0.75 or jaccard < 0.55:
        return min(score, MATCH_THRESHOLD - 0.01)
    return min(1.0, score)


def find_best_evidence_match(
    question: str,
    requirement: str,
    stored_answers: Iterable[Mapping[str, Any]],
    *,
    answer_type: str = "",
    threshold: float = MATCH_THRESHOLD,
) -> tuple[Mapping[str, Any] | None, float]:
    """Return the highest-confidence reusable answer above the threshold."""

    exact_key = evidence_answer_key(question, requirement)
    best: Mapping[str, Any] | None = None
    best_score = 0.0
    for item in stored_answers:
        if str(item.get("confirmation_status") or "confirmed").strip().lower() != "confirmed":
            continue
        if str(item.get("question_key") or "") == exact_key and answer_types_compatible(
            answer_type, str(item.get("answer_type") or "")
        ):
            return item, 1.0
        score = question_match_score(
            question,
            requirement,
            item,
            answer_type=answer_type,
        )
        if score > best_score:
            best = item
            best_score = score
    if best is None or best_score < threshold:
        return None, best_score
    return best, best_score
