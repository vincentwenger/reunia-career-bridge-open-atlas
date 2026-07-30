"""Contract tests for the separate DynamoDB job-discovery repository."""

from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from job_discovery.models import (
    CompanySource,
    DiscoveredJob,
    JobFitSnapshot,
    JobSourceType,
    WorkplaceType,
    discovered_job_id,
)
from job_discovery.storage import (
    DISCOVERY_TABLE_CONFIG_KEY,
    DiscoveryStorageConfigurationError,
    DiscoveryStore,
    DynamoDBDiscoveryStore,
)


class FakeDynamoTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _key(value: dict[str, Any]) -> tuple[str, str]:
        return str(value["owner_id"]), str(value["storage_key"])

    def put_item(self, *, Item: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.items[self._key(Item)] = deepcopy(Item)
        return {}

    def get_item(self, *, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        item = self.items.get(self._key(Key))
        return {"Item": deepcopy(item)} if item is not None else {}

    def delete_item(self, *, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        item = self.items.pop(self._key(Key), None)
        if kwargs.get("ReturnValues") == "ALL_OLD" and item is not None:
            return {"Attributes": deepcopy(item)}
        return {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        values = kwargs["ExpressionAttributeValues"]
        owner_id = str(values[":owner_id"])
        prefix = str(values[":prefix"])
        items = [
            deepcopy(item)
            for (stored_owner, storage_key), item in self.items.items()
            if stored_owner == owner_id and storage_key.startswith(prefix)
        ]
        items.sort(key=lambda item: str(item["storage_key"]))
        return {"Items": items}


def configured_source(owner_id: str = "owner-a") -> CompanySource:
    return CompanySource(
        id="source-one",
        owner_id=owner_id,
        company_name="Example Bank",
        careers_url="https://jobs.example.com",
        source_type=JobSourceType.GREENHOUSE,
        source_identifier="example",
        filters={"location": "Portland"},
    )


def discovered_job(owner_id: str = "owner-a") -> DiscoveredJob:
    source_id = "source-one"
    external_id = "external-1"
    return DiscoveredJob(
        id=discovered_job_id(owner_id, source_id, external_id),
        owner_id=owner_id,
        source_id=source_id,
        external_job_id=external_id,
        company="Example Bank",
        title="Senior Data Engineer",
        location="Portland, OR",
        workplace_type=WorkplaceType.HYBRID,
        employment_type="Full-time",
        salary_text="USD 150,000–190,000 / year",
        description="Build data platforms with Python, SQL, and AWS.",
        canonical_url="https://jobs.example.com/external-1",
        posted_at="2026-07-29T00:00:00+00:00",
        first_seen_at="2026-07-30T17:00:00+00:00",
        last_seen_at="2026-07-30T17:00:00+00:00",
        active=True,
        source_type=JobSourceType.GREENHOUSE,
        skills=("Python", "SQL", "AWS"),
    )


class DynamoDBDiscoveryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = FakeDynamoTable()
        self.store = DynamoDBDiscoveryStore(
            {DISCOVERY_TABLE_CONFIG_KEY: "career-bridge-job-discovery"},
            table=self.table,
            clock=lambda: "2026-07-30T18:00:00+00:00",
        )

    def test_implements_discovery_contract_and_requires_separate_table(self) -> None:
        self.assertIsInstance(self.store, DiscoveryStore)
        with self.assertRaises(DiscoveryStorageConfigurationError):
            DynamoDBDiscoveryStore({})

    def test_round_trips_sources_jobs_and_fit_snapshots_with_owner_isolation(self) -> None:
        source = configured_source()
        self.store.put_company_source(source)
        synchronized = self.store.sync_discovered_jobs(source, [discovered_job()])
        stored_job = synchronized[0]
        snapshot = JobFitSnapshot(
            job_id=stored_job.id,
            owner_id="owner-a",
            profile_fingerprint="profile-123",
            fit_score=84.5,
            recommendation="Strong match",
            confidence="high",
            supported_requirements=("Python", "SQL"),
            partial_requirements=("AWS",),
            unsupported_requirements=("Kafka",),
            analyzed_at="2026-07-30T18:01:00+00:00",
        )
        self.store.put_fit_snapshot(snapshot)

        self.assertEqual(
            "2026-07-30T18:00:00+00:00",
            self.store.get_company_source("owner-a", source.id).last_checked_at,
        )
        self.assertEqual(
            stored_job,
            self.store.get_discovered_job("owner-a", source.id, stored_job.id),
        )
        self.assertEqual(
            snapshot,
            self.store.get_fit_snapshot("owner-a", stored_job.id, "profile-123"),
        )
        self.assertEqual([], self.store.list_discovered_jobs("owner-b"))
        self.assertEqual([], self.store.list_company_sources("owner-b"))
        self.assertEqual([], self.store.list_fit_snapshots("owner-b"))

    def test_refresh_preserves_first_seen_and_marks_missing_jobs_inactive(self) -> None:
        source = configured_source()
        first = self.store.sync_discovered_jobs(
            source,
            [discovered_job()],
            checked_at="2026-07-30T18:00:00+00:00",
        )[0]
        refreshed_payload = discovered_job()
        refreshed_payload = DiscoveredJob(
            **{
                **{
                    field: getattr(refreshed_payload, field)
                    for field in refreshed_payload.__dataclass_fields__
                },
                "description": "Updated public description.",
                "first_seen_at": "2026-07-30T19:00:00+00:00",
                "last_seen_at": "2026-07-30T19:00:00+00:00",
            }
        )
        refreshed = self.store.sync_discovered_jobs(
            source,
            [refreshed_payload],
            checked_at="2026-07-30T19:00:00+00:00",
        )[0]

        self.assertEqual(first.first_seen_at, refreshed.first_seen_at)
        self.assertEqual("2026-07-30T19:00:00+00:00", refreshed.last_seen_at)

        self.store.sync_discovered_jobs(
            source,
            [],
            checked_at="2026-07-30T20:00:00+00:00",
        )
        self.assertEqual([], self.store.list_discovered_jobs("owner-a", active_only=True))
        historical = self.store.list_discovered_jobs("owner-a", active_only=False)
        self.assertEqual(1, len(historical))
        self.assertFalse(historical[0].active)
        self.assertEqual("2026-07-30T19:00:00+00:00", historical[0].last_seen_at)

    def test_production_image_includes_job_discovery_package(self) -> None:
        dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY job_discovery ./job_discovery", dockerfile)

    def test_table_contains_only_discovery_entity_types(self) -> None:
        source = configured_source()
        job = self.store.sync_discovered_jobs(source, [discovered_job()])[0]
        self.store.put_fit_snapshot(
            JobFitSnapshot(
                job_id=job.id,
                owner_id=job.owner_id,
                profile_fingerprint="profile-1",
                fit_score=70,
                recommendation="Worth reviewing",
                confidence="medium",
            )
        )

        entity_types = {item["entity_type"] for item in self.table.items.values()}
        storage_keys = {storage_key for _, storage_key in self.table.items}
        self.assertEqual(
            {"company_source", "discovered_job", "job_fit_snapshot"},
            entity_types,
        )
        self.assertFalse(any(key.startswith("APP#") for key in storage_keys))
        self.assertFalse(any(item.get("entity_type") == "application" for item in self.table.items.values()))


if __name__ == "__main__":
    unittest.main()
