from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .models import BulletProposal, JobRequirement
from .validation import numeric_tokens, word_count

_PRIORITY_SCORE = {"critical": 3, "important": 2, "secondary": 1}

_OUTCOME_WORDS = {
    "achieved",
    "automated",
    "built",
    "completed",
    "created",
    "delivered",
    "developed",
    "generated",
    "implemented",
    "improved",
    "increased",
    "launched",
    "led",
    "managed",
    "optimized",
    "reduced",
    "resolved",
    "saved",
    "supported",
}

_TOKEN_STOPWORDS = {
    "and",
    "for",
    "from",
    "into",
    "the",
    "their",
    "this",
    "through",
    "using",
    "with",
}


@dataclass(frozen=True)
class BulletScore:
    relevance: int
    evidence_strength: int
    unique_coverage: int = 0

    @property
    def total(self) -> int:
        return self.relevance + self.evidence_strength + self.unique_coverage


@dataclass(frozen=True)
class BulletSelection:
    selected_ids: frozenset[str]
    scores: dict[str, BulletScore]
    duplicate_ids: frozenset[str]
    covered_requirement_ids: frozenset[str]
    # For each unselected bullet, identify up to two selected bullets that most
    # directly competed for the same role-level resume space.
    selected_instead_ids: dict[str, tuple[str, ...]]


def relevance_score(
    item: BulletProposal,
    requirement_lookup: dict[str, JobRequirement],
) -> int:
    """Return 0-3 based on the strongest target-job requirement matched."""

    return max(
        (
            _PRIORITY_SCORE[requirement_lookup[requirement_id].priority]
            for requirement_id in set(item.matched_requirement_ids)
            if requirement_id in requirement_lookup
        ),
        default=0,
    )


def evidence_strength_score(text: str, *, candidate_confirmed: bool = False) -> int:
    """Return 0-2 using conservative, explainable evidence signals."""

    words = re.findall(r"[A-Za-z0-9+#.'’-]+", text)
    if len(words) < 5:
        return 0
    normalized_words = {word.casefold().strip(".'’-") for word in words}
    has_result_signal = bool(normalized_words & _OUTCOME_WORDS)
    has_measure = bool(numeric_tokens(text))
    if candidate_confirmed or has_measure or (has_result_signal and len(words) >= 10):
        return 2
    return 1


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9+#.]+", value.casefold())
        if len(token) >= 3 and token not in _TOKEN_STOPWORDS
    }


def similarity(left: str, right: str) -> float:
    """Return a lightweight Jaccard similarity for duplicate suppression."""

    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def is_redundant(
    candidate: BulletProposal,
    selected: Iterable[BulletProposal],
) -> bool:
    """Return whether selected evidence already covers substantially the same point."""

    candidate_requirements = set(candidate.matched_requirement_ids)
    for other in selected:
        overlap = similarity(candidate.proposed_text, other.proposed_text)
        other_requirements = set(other.matched_requirement_ids)
        same_requirement_area = bool(candidate_requirements) and bool(
            candidate_requirements & other_requirements
        )
        if overlap >= 0.55 or (same_requirement_area and overlap >= 0.36):
            return True
    return False


def select_job_aligned_bullets(
    items: list[BulletProposal],
    requirements: list[JobRequirement],
    *,
    source_order: dict[str, int],
    confirmed_source_ids: set[str],
    minimum_count: int,
    maximum_count: int,
) -> BulletSelection:
    """Select bullets with one deterministic, two-pass algorithm.

    Pass 1 covers critical and important requirements with the strongest verified
    bullet available. Pass 2 fills the normal role-level space with the highest
    relevance + evidence + unique-coverage scores while avoiding duplicates.

    The model's ``include`` value is intentionally ignored. AI maps and rewrites
    evidence; deterministic code decides which bullets appear in the resume.
    """

    requirement_lookup = {requirement.id: requirement for requirement in requirements}
    base_scores = {
        item.source_bullet_id: BulletScore(
            relevance=relevance_score(item, requirement_lookup),
            evidence_strength=evidence_strength_score(
                item.proposed_text,
                candidate_confirmed=item.source_bullet_id in confirmed_source_ids,
            ),
        )
        for item in items
    }
    selected: list[BulletProposal] = []
    selected_ids: set[str] = set()
    covered: set[str] = set()
    duplicate_ids: set[str] = set()

    important_requirements = sorted(
        (
            requirement
            for requirement in requirements
            if requirement.priority in {"critical", "important"}
        ),
        key=lambda requirement: (
            -_PRIORITY_SCORE[requirement.priority],
            requirements.index(requirement),
        ),
    )

    # Pass 1: ensure the best available evidence covers the important job needs.
    for requirement in important_requirements:
        if requirement.id in covered or len(selected) >= maximum_count:
            continue
        candidates = [
            item
            for item in items
            if item.source_bullet_id not in selected_ids
            and requirement.id in item.matched_requirement_ids
        ]
        if not candidates:
            continue

        def coverage_rank(item: BulletProposal) -> tuple[int, int, int, int, int]:
            score = base_scores[item.source_bullet_id]
            uncovered_important = sum(
                1
                for requirement_id in set(item.matched_requirement_ids) - covered
                if requirement_id in requirement_lookup
                and requirement_lookup[requirement_id].priority in {"critical", "important"}
            )
            return (
                score.evidence_strength,
                uncovered_important,
                score.relevance,
                0 if is_redundant(item, selected) else 1,
                -source_order[item.source_bullet_id],
            )

        chosen = max(candidates, key=coverage_rank)
        selected.append(chosen)
        selected_ids.add(chosen.source_bullet_id)
        covered.update(chosen.matched_requirement_ids)

    # Pass 2: fill normal resume space with the strongest non-duplicative evidence.
    while len(selected) < min(minimum_count, len(items)):
        candidates = [
            item for item in items if item.source_bullet_id not in selected_ids
        ]
        if not candidates:
            break

        def fill_score(item: BulletProposal) -> BulletScore:
            base = base_scores[item.source_bullet_id]
            matched = set(item.matched_requirement_ids)
            if matched - covered:
                unique = 2
            elif matched:
                unique = 1
            else:
                unique = 0
            return BulletScore(base.relevance, base.evidence_strength, unique)

        # A matched accomplishment always outranks an unmatched transferable one.
        # Redundancy decides between matched bullets; it never lets a zero-match bullet
        # displace available job evidence.
        matched_candidates = [item for item in candidates if item.matched_requirement_ids]
        priority_candidates = matched_candidates or candidates
        non_redundant = [
            item for item in priority_candidates if not is_redundant(item, selected)
        ]
        pool = non_redundant or priority_candidates

        def fill_rank(item: BulletProposal) -> tuple[int, int, int, int, int]:
            score = fill_score(item)
            return (
                score.total,
                score.relevance,
                score.evidence_strength,
                len(set(item.matched_requirement_ids) - covered),
                -source_order[item.source_bullet_id],
            )

        chosen = max(pool, key=fill_rank)
        if is_redundant(chosen, selected):
            duplicate_ids.add(chosen.source_bullet_id)
        selected.append(chosen)
        selected_ids.add(chosen.source_bullet_id)
        covered.update(chosen.matched_requirement_ids)

    # Mark unselected candidates whose evidence substantially duplicates a selected item.
    for item in items:
        if item.source_bullet_id not in selected_ids and is_redundant(item, selected):
            duplicate_ids.add(item.source_bullet_id)

    final_scores: dict[str, BulletScore] = {}
    for item in items:
        base = base_scores[item.source_bullet_id]
        matched = set(item.matched_requirement_ids)
        if item.source_bullet_id in selected_ids:
            other_selected_requirements = {
                requirement_id
                for other in selected
                if other.source_bullet_id != item.source_bullet_id
                for requirement_id in other.matched_requirement_ids
            }
            unique = 2 if matched - other_selected_requirements else (1 if matched else 0)
        else:
            unique = 2 if matched - covered else (1 if matched else 0)
        final_scores[item.source_bullet_id] = BulletScore(
            relevance=base.relevance,
            evidence_strength=base.evidence_strength,
            unique_coverage=unique,
        )

    selected_by_id = {item.source_bullet_id: item for item in selected}
    selected_instead_ids: dict[str, tuple[str, ...]] = {}
    for item in items:
        item_id = item.source_bullet_id
        if item_id in selected_ids:
            continue

        item_requirements = set(item.matched_requirement_ids)
        item_score = final_scores[item_id]
        ranked: list[tuple[tuple[int, int, int, int, int, int], str]] = []
        for selected_id, other in selected_by_id.items():
            other_requirements = set(other.matched_requirement_ids)
            shared_requirements = item_requirements & other_requirements
            overlap = similarity(item.proposed_text, other.proposed_text)
            other_score = final_scores[selected_id]

            # Prefer bullets that genuinely competed in the same requirement area.
            # For unmatched bullets, the strongest selected job evidence is the
            # natural comparison because it consumed the available role-level space.
            directly_related = bool(shared_requirements) or overlap >= 0.36
            stronger_overall = (
                other_score.total > item_score.total
                or other_score.relevance > item_score.relevance
                or other_score.unique_coverage > item_score.unique_coverage
            )
            if item_requirements and not directly_related and not stronger_overall:
                continue

            rank = (
                1 if shared_requirements else 0,
                1 if overlap >= 0.36 else 0,
                other_score.total - item_score.total,
                other_score.unique_coverage,
                other_score.evidence_strength,
                -source_order[selected_id],
            )
            ranked.append((rank, selected_id))

        ranked.sort(reverse=True)
        selected_instead_ids[item_id] = tuple(
            selected_id for _, selected_id in ranked[:2]
        )

    return BulletSelection(
        selected_ids=frozenset(selected_ids),
        scores=final_scores,
        duplicate_ids=frozenset(duplicate_ids),
        covered_requirement_ids=frozenset(covered),
        selected_instead_ids=selected_instead_ids,
    )
