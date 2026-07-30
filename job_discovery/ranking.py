from __future__ import annotations

from dataclasses import dataclass

from .models import DiscoveredJob, WorkplaceType
from .normalization import stable_text_key


@dataclass(frozen=True, slots=True)
class CandidateJobProfile:
    target_titles: tuple[str, ...] = ()
    verified_skills: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    accepts_remote: bool = True
    preferred_employment_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RankedJob:
    job: DiscoveredJob
    score: float
    reasons: tuple[str, ...]


def rank_jobs(jobs: list[DiscoveredJob], profile: CandidateJobProfile) -> list[RankedJob]:
    ranked = [_score_job(job, profile) for job in jobs]
    return sorted(
        ranked,
        key=lambda item: (item.score, item.job.posted_at or item.job.updated_at, item.job.title.casefold()),
        reverse=True,
    )


def _score_job(job: DiscoveredJob, profile: CandidateJobProfile) -> RankedJob:
    score = 0.0
    reasons: list[str] = []

    title_score = _best_token_overlap(job.title, profile.target_titles)
    if profile.target_titles:
        contribution = 35.0 * title_score
        score += contribution
        if contribution >= 14:
            reasons.append("Title aligns with a target role")

    verified = {stable_text_key(skill) for skill in profile.verified_skills if stable_text_key(skill)}
    advertised = {stable_text_key(skill) for skill in job.skills if stable_text_key(skill)}
    if verified and advertised:
        overlap = verified & advertised
        contribution = 45.0 * len(overlap) / len(advertised)
        score += contribution
        if overlap:
            reasons.append("Verified skills match: " + ", ".join(sorted(overlap)[:5]))

    if profile.preferred_locations:
        location_score = _best_token_overlap(job.location, profile.preferred_locations)
        score += 10.0 * location_score
        if location_score >= 0.5:
            reasons.append("Location matches a preference")

    if profile.accepts_remote and job.workplace_type is WorkplaceType.REMOTE:
        score += 10.0
        reasons.append("Remote role")

    if profile.preferred_employment_types and job.employment_type:
        desired = {stable_text_key(value) for value in profile.preferred_employment_types}
        if stable_text_key(job.employment_type) in desired:
            score += 5.0
            reasons.append("Employment type matches")

    return RankedJob(job=job, score=round(min(score, 100.0), 2), reasons=tuple(reasons))


def _best_token_overlap(text: str, candidates: tuple[str, ...]) -> float:
    text_tokens = set(stable_text_key(text).split())
    best = 0.0
    for candidate in candidates:
        candidate_tokens = set(stable_text_key(candidate).split())
        if not candidate_tokens:
            continue
        best = max(best, len(text_tokens & candidate_tokens) / len(candidate_tokens))
    return best
