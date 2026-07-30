from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import DiscoveredJob, JobFitSnapshot, WorkplaceType, profile_fingerprint
from .normalization import stable_text_key


@dataclass(frozen=True, slots=True)
class CandidateJobProfile:
    target_titles: tuple[str, ...] = ()
    verified_skills: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    accepts_remote: bool = True
    preferred_employment_types: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return profile_fingerprint(asdict(self))


@dataclass(frozen=True, slots=True)
class RankedJob:
    job: DiscoveredJob
    score: float
    reasons: tuple[str, ...]
    fit_snapshot: JobFitSnapshot


def rank_jobs(jobs: list[DiscoveredJob], profile: CandidateJobProfile) -> list[RankedJob]:
    ranked = [_score_job(job, profile) for job in jobs]
    return sorted(
        ranked,
        key=lambda item: (item.score, item.job.posted_at, item.job.title.casefold()),
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

    verified_by_key = {
        stable_text_key(skill): skill
        for skill in profile.verified_skills
        if stable_text_key(skill)
    }
    supported: list[str] = []
    partial: list[str] = []
    unsupported: list[str] = []
    for requirement in job.skills:
        key = stable_text_key(requirement)
        if not key:
            continue
        if key in verified_by_key:
            supported.append(requirement)
        elif any(_token_overlap(key, candidate) >= 0.5 for candidate in verified_by_key):
            partial.append(requirement)
        else:
            unsupported.append(requirement)

    advertised_count = len(supported) + len(partial) + len(unsupported)
    if advertised_count:
        skill_ratio = (len(supported) + (0.5 * len(partial))) / advertised_count
        score += 45.0 * skill_ratio
        if supported:
            reasons.append("Verified skills match: " + ", ".join(supported[:5]))
        if partial:
            reasons.append("Partial skill alignment: " + ", ".join(partial[:3]))

    if profile.preferred_locations:
        location_score = _best_token_overlap(job.location, profile.preferred_locations)
        score += 10.0 * location_score
        if location_score >= 0.5:
            reasons.append("Location matches a preference")

    hard_blockers: list[str] = []
    if profile.accepts_remote and job.workplace_type is WorkplaceType.REMOTE:
        score += 10.0
        reasons.append("Remote role")
    elif not profile.accepts_remote and job.workplace_type is WorkplaceType.REMOTE:
        hard_blockers.append("Remote workplace conflicts with the profile")

    if profile.preferred_employment_types and job.employment_type:
        desired = {stable_text_key(value) for value in profile.preferred_employment_types}
        if stable_text_key(job.employment_type) in desired:
            score += 5.0
            reasons.append("Employment type matches")

    final_score = round(min(score, 100.0), 2)
    recommendation = _recommendation(final_score, hard_blockers)
    confidence = _confidence(job, profile)
    snapshot = JobFitSnapshot(
        job_id=job.id,
        owner_id=job.owner_id,
        profile_fingerprint=profile.fingerprint,
        fit_score=final_score,
        recommendation=recommendation,
        confidence=confidence,
        supported_requirements=tuple(supported),
        partial_requirements=tuple(partial),
        unsupported_requirements=tuple(unsupported),
        hard_blockers=tuple(hard_blockers),
    )
    return RankedJob(
        job=job,
        score=final_score,
        reasons=tuple(reasons),
        fit_snapshot=snapshot,
    )


def _recommendation(score: float, hard_blockers: list[str]) -> str:
    if hard_blockers:
        return "Review blocker before applying"
    if score >= 75:
        return "Strong match"
    if score >= 55:
        return "Worth reviewing"
    if score >= 35:
        return "Possible stretch"
    return "Low fit"


def _confidence(job: DiscoveredJob, profile: CandidateJobProfile) -> str:
    if len(job.skills) >= 3 and len(profile.verified_skills) >= 3:
        return "high"
    if job.skills and profile.verified_skills:
        return "medium"
    return "low"


def _best_token_overlap(text: str, candidates: tuple[str, ...]) -> float:
    text_key = stable_text_key(text)
    best = 0.0
    for candidate in candidates:
        best = max(best, _token_overlap(text_key, stable_text_key(candidate)))
    return best


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(right_tokens)
