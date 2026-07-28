from __future__ import annotations

import re
from collections import OrderedDict

from .models import CandidateProfile, JobAnalysis, SkillSet

# These are resume-writing targets, not a reason to invent or retain irrelevant skills.
# The balancing routine always stays within the candidate's verified profile and the
# overall 20-30 skill guideline takes precedence over the sum of category maxima.
SKILL_CATEGORY_RULES = OrderedDict(
    (
        ("hard_skills", {"label": "Hard Skills", "minimum": 8, "maximum": 14}),
        ("soft_skills", {"label": "Soft Skills", "minimum": 3, "maximum": 5}),
        ("tools_software", {"label": "Tools & Software", "minimum": 6, "maximum": 12}),
        ("industry_knowledge", {"label": "Industry Knowledge", "minimum": 4, "maximum": 8}),
    )
)
SKILL_TOTAL_RECOMMENDED_MINIMUM = 20
SKILL_TOTAL_MAXIMUM = 30

_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
_PRIORITY_WEIGHT = {"critical": 4, "important": 2, "secondary": 1}


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.casefold()) if len(token) > 1}


def _profile_skills_by_category(profile: CandidateProfile) -> dict[str, list[str]]:
    """Return canonical, cross-category de-duplicated profile skills."""

    seen: set[str] = set()
    result: dict[str, list[str]] = {field: [] for field in SKILL_CATEGORY_RULES}
    for field in SKILL_CATEGORY_RULES:
        for raw_skill in getattr(profile.skills, field):
            skill = " ".join(raw_skill.split())
            key = _normalize(skill)
            if skill and key and key not in seen:
                result[field].append(skill)
                seen.add(key)
    return result


def _skill_relevance(skill: str, analysis: JobAnalysis) -> int:
    """Give exact JD wording the strongest weight, then reward keyword overlap."""

    skill_key = _normalize(skill)
    skill_tokens = _tokens(skill)
    score = 0
    for requirement in analysis.requirements:
        weight = _PRIORITY_WEIGHT.get(requirement.priority, 1)
        phrases = [requirement.requirement, *requirement.keywords]
        for phrase in phrases:
            phrase_key = _normalize(phrase)
            if not phrase_key:
                continue
            if skill_key == phrase_key:
                score += 100 * weight
            elif skill_key in phrase_key or phrase_key in skill_key:
                score += 35 * weight
            else:
                score += 8 * weight * len(skill_tokens & _tokens(phrase))
    return score


def balance_skill_categories(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    skills: SkillSet,
) -> SkillSet:
    """Return a supported, category-correct, reasonably balanced skill set.

    Existing selected skills are preserved when possible, but each skill is placed in
    the same category used by the Candidate Profile. Underfilled groups are completed
    with the most job-relevant verified skills. Category and total caps prevent a long
    keyword dump. When the profile has fewer skills than a target, all available skills
    are retained without creating a validation failure.
    """

    profile_by_category = _profile_skills_by_category(profile)
    canonical_lookup: dict[str, tuple[str, str]] = {}
    for field, values in profile_by_category.items():
        for value in values:
            canonical_lookup[_normalize(value)] = (field, value)

    selected: dict[str, list[str]] = {field: [] for field in SKILL_CATEGORY_RULES}
    selected_keys: set[str] = set()
    originally_selected: set[str] = set()

    # Correct misclassification by routing each verified skill to its profile category.
    for source_field in SKILL_CATEGORY_RULES:
        for raw_skill in getattr(skills, source_field):
            key = _normalize(raw_skill)
            canonical = canonical_lookup.get(key)
            if canonical is None or key in selected_keys:
                continue
            target_field, canonical_value = canonical
            selected[target_field].append(canonical_value)
            selected_keys.add(key)
            originally_selected.add(key)

    # Apply per-category caps first, preferring AI-selected and job-relevant items.
    for field, rule in SKILL_CATEGORY_RULES.items():
        maximum = int(rule["maximum"])
        values = selected[field]
        if len(values) > maximum:
            indexed = list(enumerate(values))
            indexed.sort(
                key=lambda item: (
                    -_skill_relevance(item[1], analysis),
                    item[0],
                )
            )
            keep = {value for _index, value in indexed[:maximum]}
            selected[field] = [value for value in values if value in keep]
            selected_keys = {
                _normalize(value)
                for category_values in selected.values()
                for value in category_values
            }

    # Fill underrepresented groups from verified profile skills, ranked for this job.
    for field, rule in SKILL_CATEGORY_RULES.items():
        target = min(int(rule["minimum"]), len(profile_by_category[field]))
        candidates = [
            value
            for value in profile_by_category[field]
            if _normalize(value) not in selected_keys
        ]
        indexed_candidates = list(enumerate(candidates))
        indexed_candidates.sort(
            key=lambda item: (-_skill_relevance(item[1], analysis), item[0])
        )
        for _index, value in indexed_candidates:
            if len(selected[field]) >= target:
                break
            selected[field].append(value)
            selected_keys.add(_normalize(value))

    # Keep the overall section concise. Never prune a category below the smaller of
    # its recommended minimum and the number of verified skills actually available.
    def category_floor(field: str) -> int:
        return min(
            int(SKILL_CATEGORY_RULES[field]["minimum"]),
            len(profile_by_category[field]),
        )

    while sum(len(values) for values in selected.values()) > SKILL_TOTAL_MAXIMUM:
        removable: list[tuple[int, int, str, str]] = []
        for field, values in selected.items():
            floor = category_floor(field)
            if len(values) <= floor:
                continue
            for index, value in enumerate(values):
                key = _normalize(value)
                # Prefer keeping model-selected items when relevance is otherwise equal.
                selected_bonus = 1 if key in originally_selected else 0
                removable.append(
                    (
                        _skill_relevance(value, analysis),
                        selected_bonus,
                        field,
                        value,
                    )
                )
        if not removable:
            break
        _score, _selected_bonus, field, value = min(removable)
        selected[field].remove(value)
        selected_keys.discard(_normalize(value))

    return SkillSet(**selected)


def skill_category_counts(skills: SkillSet) -> dict[str, int]:
    return {field: len(getattr(skills, field)) for field in SKILL_CATEGORY_RULES}
