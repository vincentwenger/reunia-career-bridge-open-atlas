from __future__ import annotations

from collections import OrderedDict

from .models import DiscoveredJob, description_fingerprint
from .normalization import canonicalize_url


_MIN_CONTENT_DEDUP_CHARS = 40


def deduplicate_jobs(jobs: list[DiscoveredJob]) -> list[DiscoveredJob]:
    """Deduplicate by source identity, canonical URL, and content fingerprint.

    The three keys are evaluated transitively. For example, a Greenhouse record
    can merge with a JSON-LD record by URL while a third record joins the same
    group by an identical normalized description fingerprint.
    """

    exact: OrderedDict[tuple[str, str], DiscoveredJob] = OrderedDict()
    for job in jobs:
        key = (job.source_id, job.external_job_id)
        exact[key] = _richer(exact.get(key), job)

    records = list(exact.values())
    parents = list(range(len(records)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    url_index: dict[str, int] = {}
    fingerprint_index: dict[str, int] = {}
    for index, job in enumerate(records):
        canonical_url = canonicalize_url(job.canonical_url)
        if canonical_url:
            previous = url_index.get(canonical_url)
            if previous is not None:
                union(previous, index)
            else:
                url_index[canonical_url] = index

        fingerprint = _content_fingerprint_key(job)
        if fingerprint:
            previous = fingerprint_index.get(fingerprint)
            if previous is not None:
                union(previous, index)
            else:
                fingerprint_index[fingerprint] = index

    groups: OrderedDict[int, DiscoveredJob] = OrderedDict()
    for index, job in enumerate(records):
        root = find(index)
        groups[root] = _richer(groups.get(root), job)
    return list(groups.values())


def _content_fingerprint_key(job: DiscoveredJob) -> str:
    # Empty or tiny descriptions are frequently shared ATS boilerplate and are
    # not strong enough to identify the same posting safely.
    normalized = " ".join(job.description.split())
    if len(normalized) < _MIN_CONTENT_DEDUP_CHARS:
        return ""
    return description_fingerprint(normalized)


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
