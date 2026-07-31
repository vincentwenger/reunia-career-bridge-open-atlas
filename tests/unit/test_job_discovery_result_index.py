"""Materialized Job Discovery result-index contracts."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from job_discovery.models import (
    DiscoveredJob,
    DiscoveryResultIndexSummary,
    DiscoveryResultRecord,
    DiscoverySearchPreferences,
    JobSourceType,
    WorkplaceType,
    discovered_job_id,
)
from job_discovery.storage import InMemoryDiscoveryStore, JsonFileDiscoveryStore


def result_job(index: int) -> DiscoveredJob:
    external_id = f"external-{index:02d}"
    return DiscoveredJob(
        id=discovered_job_id("owner-a", "source-a", external_id),
        owner_id="owner-a",
        source_id="source-a",
        external_job_id=external_id,
        company="Example",
        title=f"Data Engineer {index:02d}",
        location="Portland, OR",
        workplace_type=WorkplaceType.HYBRID,
        description="",  # Result index intentionally omits the full description.
        canonical_url=f"https://jobs.example.com/{external_id}",
        posted_at="2026-07-30T00:00:00+00:00",
        first_seen_at="2026-07-30T00:00:00+00:00",
        last_seen_at="2026-07-30T00:00:00+00:00",
        source_type=JobSourceType.GREENHOUSE,
    )


def build_index(store: InMemoryDiscoveryStore, count: int = 15):
    revision = store.get_result_revision("owner-a")
    summary = DiscoveryResultIndexSummary(
        owner_id="owner-a",
        evidence_fingerprint="evidence-a",
        preference_fingerprint="preferences-a",
        revision_token=revision,
        pending_count=count,
    )
    records = [
        DiscoveryResultRecord(
            owner_id="owner-a",
            evidence_fingerprint="evidence-a",
            preference_fingerprint="preferences-a",
            result_group="pending",
            job=result_job(index),
            preference_score=80 - index,
            freshness_score=100 - index,
            posted_label="Posted today",
            sort_rank=f"{index:08d}",
        )
        for index in range(count)
    ]
    store.replace_result_index(summary, records)
    return summary, records


class DiscoveryResultIndexTests(unittest.TestCase):
    def test_reads_only_the_requested_materialized_page(self) -> None:
        store = InMemoryDiscoveryStore()
        _, records = build_index(store)

        page = store.list_result_records_page(
            "owner-a",
            "evidence-a",
            "preferences-a",
            "pending",
            offset=10,
            limit=5,
        )

        self.assertEqual(records[10:15], page)

    def test_mutation_invalidates_a_precomputed_index(self) -> None:
        store = InMemoryDiscoveryStore()
        summary, _ = build_index(store)

        store.put_search_preferences(
            DiscoverySearchPreferences(
                owner_id="owner-a",
                preferred_keywords=("Python",),
            )
        )

        self.assertNotEqual(summary.revision_token, store.get_result_revision("owner-a"))

    def test_json_store_preserves_compact_index_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            first = JsonFileDiscoveryStore(path)
            summary, records = build_index(first)
            second = JsonFileDiscoveryStore(path)

            self.assertEqual(
                summary,
                second.get_result_index_summary(
                    "owner-a", "evidence-a", "preferences-a"
                ),
            )
            self.assertEqual(
                records[:3],
                second.list_result_records_page(
                    "owner-a",
                    "evidence-a",
                    "preferences-a",
                    "pending",
                    offset=0,
                    limit=3,
                ),
            )
            self.assertEqual("", records[0].job.description)


if __name__ == "__main__":
    unittest.main()
