from __future__ import annotations

from collections import OrderedDict

from .models import DiscoveredJob
from .normalization import canonicalize_url, stable_text_key


def deduplicate_jobs(jobs: list[DiscoveredJob]) -> list[DiscoveredJob]:
    """Deduplicate exact source records and likely cross-source duplicates."""

    exact: OrderedDict[tuple[str, str], DiscoveredJob] = OrderedDict()
    for job in jobs:
        key = (job.source_id, job.external_id)
        exact[key] = _richer(exact.get(key), job)

    merged: OrderedDict[tuple[str, str, str], DiscoveredJob] = OrderedDict()
    url_index: dict[str, tuple[str, str, str]] = {}
    for job in exact.values():
        signature = (
            stable_text_key(job.company),
            stable_text_key(job.title),
            stable_text_key(job.location),
        )
        canonical_url = canonicalize_url(job.job_url)
        existing_key = url_index.get(canonical_url) if canonical_url else None
        key = existing_key or signature
        merged[key] = _richer(merged.get(key), job)
        if canonical_url:
            url_index[canonical_url] = key
    return list(merged.values())


def _richer(current: DiscoveredJob | None, candidate: DiscoveredJob) -> DiscoveredJob:
    if current is None:
        return candidate
    return candidate if _richness(candidate) > _richness(current) else current


def _richness(job: DiscoveredJob) -> tuple[int, int, int, int]:
    populated = sum(
        bool(value)
        for value in (
            job.description,
            job.location,
            job.employment_type,
            job.department,
            job.team,
            job.salary_summary,
            job.apply_url,
            job.posted_at,
        )
    )
    return (populated, len(job.description), len(job.skills), len(job.locations))
