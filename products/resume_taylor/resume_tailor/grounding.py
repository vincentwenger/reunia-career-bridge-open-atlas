from __future__ import annotations

"""Deterministic guardrails for generated candidate claims.

The checks in this module are intentionally conservative. They do not attempt to
prove that two sentences are semantically identical. Instead, they block the
highest-risk forms of unsupported generation that can be detected reliably
without another model call: new numbers, named entities, technologies,
credentials, strengthened leadership/outcome language, and low-overlap factual
claims.
"""

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Sequence


@dataclass(frozen=True)
class GroundingFinding:
    code: str
    message: str
    unsupported_terms: tuple[str, ...] = ()
    unsupported_numbers: tuple[str, ...] = ()
    unsupported_entities: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return True


_WORD_RE = re.compile(r"[^\W_]+(?:[+#./-][^\W_]+)*", re.UNICODE)
_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_RE = re.compile(
    r"\s*(?:;|\s+[—–-]\s+|,\s+(?=(?:and|but|while|whereas|plus)\b))\s*",
    re.IGNORECASE,
)

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
    "by", "for", "from", "had", "has", "have", "having", "he", "her",
    "hers", "him", "his", "i", "in", "into", "is", "it", "its", "me",
    "my", "of", "on", "or", "our", "ours", "she", "that", "the", "their",
    "theirs", "them", "they", "this", "those", "to", "was", "we", "were",
    "while", "with", "within", "you", "your", "yours",
}

# Common career-writing words do not establish a factual claim on their own.
_GENERIC_TERMS = {
    "ability", "abilities", "accurate", "accurately", "alignment", "application",
    "applications", "approach", "background", "backgrounds", "bring", "brings", "candidate",
    "capability", "capabilities", "career", "careers", "claim", "claims",
    "clear", "clearly", "communication", "communications", "credible",
    "current", "documented", "evidence", "experience", "experiences",
    "experienced", "explicit", "explicitly", "focus", "focused", "focusing",
    "job", "jobs", "knowledge", "learning", "need", "needs", "offer",
    "offers", "professional", "professionally", "profile", "profiles",
    "relevant", "requirement", "requirements", "resume", "role", "roles",
    "skill", "skills", "specific", "strength", "strengths", "strong",
    "support", "supported", "target", "targets", "targeting", "transferable",
    "treat", "treated", "treating", "instead", "existing", "present", "presenting",
    "claim", "claimed", "gap", "gaps", "rather", "than", "identify", "identified",
    "identifying", "unsupported", "domain", "mandatory", "must", "before", "apply",
    "applying", "preserve", "preserved", "preserving", "translate", "translated",
    "translating", "responsibility", "responsibilities", "carefully", "keep", "keeping",
    "official", "employer", "employers", "title", "titles", "fact", "facts", "unchanged",
    "transition", "transitioning", "translation", "verified", "work", "working",
}

# These words may safely appear in honest gap/learning language when they come
# from the job description rather than candidate evidence.
_GAP_MARKERS = {
    "absence", "absent", "gap", "gaps", "learning", "missing", "need",
    "needs", "not", "outside", "partial", "pursuing", "requirement",
    "requirements", "unsupported", "unverified", "verify", "verified",
    "without", "rather", "instead", "before", "future", "target", "targets",
    "targeting", "pursue", "pursuing",
}

# Adding one of these terms can materially strengthen a claim even when the
# surrounding nouns overlap with evidence.
_STRENGTHENING_FAMILIES: dict[str, set[str]] = {
    "leadership": {
        "lead", "led", "leader", "leadership", "manage", "managed", "manager",
        "management", "mentor", "mentored", "coach", "coached", "direct",
        "directed", "oversee", "oversaw", "own", "owned", "spearhead",
        "spearheaded", "supervise", "supervised",
    },
    "outcome": {
        "achieve", "achieved", "accelerate", "accelerated", "boost", "boosted",
        "deliver", "delivered", "generate", "generated", "improve", "improved",
        "increase", "increased", "optimize", "optimized", "reduce", "reduced",
        "save", "saved", "transform", "transformed",
    },
    "scope": {
        "enterprise", "enterprise-wide", "global", "international", "large-scale",
        "organization-wide", "company-wide", "cross-functional", "executive",
        "strategic", "end-to-end",
    },
    "credential": {
        "certified", "certification", "certificate", "licensed", "license",
        "credential", "credentialed", "degree", "master", "bachelor", "doctorate",
    },
}

_ACTION_WORDS = {
    "administer", "administered", "analyze", "analyzed", "assess", "assessed",
    "build", "built", "communicate", "communicated", "consolidate", "consolidated",
    "coordinate", "coordinated", "create", "created", "deploy", "deployed",
    "design", "designed", "develop", "developed", "document", "documented",
    "educate", "educated", "evaluate", "evaluated", "explain", "explained",
    "facilitate", "facilitated", "implement", "implemented", "maintain",
    "maintained", "monitor", "monitored", "partner", "partnered", "prepare",
    "prepared", "produce", "produced", "provide", "provided", "reconcile",
    "reconciled", "resolve", "resolved", "respond", "responded", "review",
    "reviewed", "track", "tracked", "train", "trained", "write", "wrote",
} | set().union(*_STRENGTHENING_FAMILIES.values())


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _tokens(value: str) -> list[str]:
    return [match.group(0).casefold() for match in _WORD_RE.finditer(_normalize(value))]


def _stem(token: str) -> str:
    token = token.strip("._-/")
    if len(token) <= 4:
        return token
    replacements = (
        ("ies", "y"),
        ("ments", ""),
        ("ment", ""),
        ("ations", ""),
        ("ation", ""),
        ("ingly", ""),
        ("edly", ""),
        ("ing", ""),
        ("ers", ""),
        ("er", ""),
        ("ed", ""),
        ("es", ""),
        ("s", ""),
    )
    for suffix, replacement in replacements:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)] + replacement
    return token


_ALIAS_GROUPS = (
    {"arabic", "العربية", "عربي"},
    {"french", "français", "francais", "française", "francaise"},
    {"morocco", "maroc"},
    {"coordinator", "coordinateur", "coordinatrice"},
    {"project", "projet", "projets"},
    {"management", "gestion"},
    {"university", "université", "universite"},
)


def _token_stems(value: str) -> set[str]:
    stems = {_stem(token) for token in _tokens(value) if token}
    normalized_tokens = set(_tokens(value))
    for group in _ALIAS_GROUPS:
        group_tokens = {item.casefold() for item in group}
        if normalized_tokens & group_tokens:
            stems.update(_stem(item) for item in group_tokens)
    return stems


def _salient_terms(value: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in _tokens(value):
        stem = _stem(token)
        if (
            len(stem) < 4
            or token in _STOPWORDS
            or token in _GENERIC_TERMS
            or token in _ACTION_WORDS
            or stem in {_stem(item) for item in _STOPWORDS | _GENERIC_TERMS | _ACTION_WORDS}
            or token.isdigit()
        ):
            continue
        if stem not in seen:
            seen.add(stem)
            terms.append(token)
    return terms


def _named_entities(value: str) -> list[str]:
    """Extract high-signal capitalized/acronym tokens without treating sentence starts as entities."""

    raw = str(value or "")
    entities: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b(?:[A-Z][A-Z0-9+#./-]{1,}|[A-Z][a-z]+(?:\s+[A-Z][A-Za-z0-9+#./-]+)+)\b", raw):
        entity = " ".join(match.group(0).split())
        key = _normalize(entity)
        if key and key not in seen:
            entities.append(entity)
            seen.add(key)
    return entities


def _split_claim_units(value: str) -> list[str]:
    units: list[str] = []
    for sentence in _SENTENCE_RE.split(str(value or "")):
        sentence = sentence.strip()
        if not sentence:
            continue
        parts = [part.strip(" ,;:-") for part in _CLAUSE_RE.split(sentence)]
        units.extend(part for part in parts if part)
    return units


def _contains_gap_language(value: str) -> bool:
    tokens = set(_tokens(value))
    return bool(tokens & _GAP_MARKERS)


def _family_present(value: str, family_terms: set[str]) -> bool:
    stems = _token_stems(value)
    return bool(stems & {_stem(item) for item in family_terms})


def _term_supported(term: str, evidence_stems: set[str]) -> bool:
    stem = _stem(term)
    if stem in evidence_stems:
        return True
    # Hyphenated compounds may be fully supported by their components.
    components = [_stem(part) for part in re.split(r"[-/.]+", term) if len(part) >= 3]
    return bool(components) and all(component in evidence_stems for component in components)


def validate_candidate_claim(
    text: str,
    evidence_texts: Iterable[str],
    *,
    context_texts: Iterable[str] = (),
    allow_gap_context: bool = False,
    require_overlap: bool = True,
) -> list[GroundingFinding]:
    """Return deterministic evidence-grounding findings for candidate-facing text.

    ``context_texts`` may contain a job description or role information. Context
    can support honest gap language, but it is never accepted as positive
    candidate evidence.
    """

    candidate_text = "\n".join(str(item or "") for item in evidence_texts if str(item or "").strip())
    context_text = "\n".join(str(item or "") for item in context_texts if str(item or "").strip())
    evidence_normalized = _normalize(candidate_text)
    context_normalized = _normalize(context_text)
    evidence_stems = _token_stems(candidate_text)
    context_stems = _token_stems(context_text)
    evidence_numbers = set(_NUMBER_RE.findall(candidate_text))

    findings: list[GroundingFinding] = []
    for unit in _split_claim_units(text):
        normalized_unit = _normalize(unit)
        is_gap = allow_gap_context and _contains_gap_language(unit)

        numbers = set(_NUMBER_RE.findall(unit))
        unsupported_numbers = tuple(sorted(numbers - evidence_numbers))
        if unsupported_numbers and not (
            is_gap and all(number in set(_NUMBER_RE.findall(context_text)) for number in unsupported_numbers)
        ):
            findings.append(
                GroundingFinding(
                    code="unsupported_number",
                    message=(
                        "Generated candidate claim introduces number(s) not present in verified evidence: "
                        + ", ".join(unsupported_numbers)
                        + f". Claim: {unit}"
                    ),
                    unsupported_numbers=unsupported_numbers,
                )
            )
            continue

        unsupported_entities: list[str] = []
        for entity in _named_entities(unit):
            key = _normalize(entity)
            if key in {"u.s", "u.s.", "us"}:
                continue
            context_key = re.sub(r"^(?:targets?|targeting|pursuing)\s+", "", key)
            if key in evidence_normalized:
                continue
            if is_gap and (key in context_normalized or context_key in context_normalized):
                continue
            # Single ordinary title-case words are not extracted; acronym and
            # multiword entities are high-signal enough to block.
            unsupported_entities.append(entity)
        if unsupported_entities:
            findings.append(
                GroundingFinding(
                    code="unsupported_entity",
                    message=(
                        "Generated candidate claim introduces named term(s) not present in verified evidence: "
                        + ", ".join(unsupported_entities)
                        + f". Claim: {unit}"
                    ),
                    unsupported_entities=tuple(unsupported_entities),
                )
            )
            continue

        strengthening: list[str] = []
        for family_name, family_terms in _STRENGTHENING_FAMILIES.items():
            if _family_present(unit, family_terms) and not _family_present(candidate_text, family_terms):
                # Credential and scope terms in an explicit gap are permitted only
                # when the job context contains them.
                if is_gap and _family_present(context_text, family_terms):
                    continue
                strengthening.append(family_name)
        if strengthening:
            findings.append(
                GroundingFinding(
                    code="strengthened_claim",
                    message=(
                        "Generated candidate claim adds unsupported "
                        + ", ".join(strengthening)
                        + f" language. Claim: {unit}"
                    ),
                    unsupported_terms=tuple(strengthening),
                )
            )
            continue

        salient = _salient_terms(unit)
        if not salient:
            continue
        supported = [term for term in salient if _term_supported(term, evidence_stems)]
        unsupported = [term for term in salient if not _term_supported(term, evidence_stems)]
        if is_gap:
            unsupported = [
                term
                for term in unsupported
                if not _term_supported(term, context_stems)
            ]

        coverage = len(supported) / max(1, len(salient))
        # A single unsupported paraphrase is tolerated when the rest of the claim
        # is traceable. Two or more unsupported factual terms with weak overlap are
        # blocked as a likely invented responsibility/accomplishment.
        if require_overlap and len(unsupported) >= 2 and coverage < 0.5:
            findings.append(
                GroundingFinding(
                    code="insufficient_evidence_overlap",
                    message=(
                        "Generated candidate claim is not sufficiently traceable to verified evidence; "
                        "unsupported factual term(s): "
                        + ", ".join(unsupported[:8])
                        + f". Claim: {unit}"
                    ),
                    unsupported_terms=tuple(unsupported[:8]),
                )
            )

    # De-duplicate findings while preserving order.
    unique: list[GroundingFinding] = []
    seen_keys: set[tuple[str, str]] = set()
    for finding in findings:
        key = (finding.code, finding.message)
        if key not in seen_keys:
            unique.append(finding)
            seen_keys.add(key)
    return unique


def is_candidate_claim_grounded(
    text: str,
    evidence_texts: Iterable[str],
    *,
    context_texts: Iterable[str] = (),
    allow_gap_context: bool = False,
    require_overlap: bool = True,
) -> bool:
    return not validate_candidate_claim(
        text,
        evidence_texts,
        context_texts=context_texts,
        allow_gap_context=allow_gap_context,
        require_overlap=require_overlap,
    )


def filter_grounded_texts(
    values: Sequence[str],
    evidence_texts: Iterable[str],
    *,
    context_texts: Iterable[str] = (),
    allow_gap_context: bool = False,
    require_overlap: bool = True,
) -> list[str]:
    evidence = tuple(evidence_texts)
    context = tuple(context_texts)
    return [
        value
        for value in values
        if is_candidate_claim_grounded(
            value,
            evidence,
            context_texts=context,
            allow_gap_context=allow_gap_context,
            require_overlap=require_overlap,
        )
    ]
