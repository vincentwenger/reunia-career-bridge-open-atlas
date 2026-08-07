"""Contract tests for the separate DynamoDB job-discovery repository."""

from __future__ import annotations

import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from job_discovery.models import (
    CompanySource,
    DiscoveredJob,
    DiscoveryJobDisposition,
    DiscoveryJobState,
    DiscoveryResultIndexSummary,
    DiscoveryResultRecord,
    DiscoverySearchPreferences,
    DiscoveryScanSchedule,
    DiscoveryScheduleCadence,
    EvidenceReference,
    JobAnalysisRecord,
    JobFitSnapshot,
    JobSourceType,
    RequirementEvidenceMatch,
    WorkplaceType,
    discovered_job_id,
)
from job_discovery.public_catalog import PUBLIC_CATALOG_OWNER_ID, public_source_key
from job_discovery.storage import (
    DISCOVERY_TABLE_CONFIG_KEY,
    DiscoveryOptimisticLockError,
    DiscoveryStorageConfigurationError,
    DiscoveryStore,
    DynamoDBDiscoveryStore,
)


class ConditionalCheckFailed(Exception):
    def __init__(self) -> None:
        super().__init__("conditional check failed")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamoTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.query_calls: list[dict[str, Any]] = []

    @staticmethod
    def _key(value: dict[str, Any]) -> tuple[str, str]:
        return str(value["owner_id"]), str(value["storage_key"])

    def put_item(self, *, Item: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        key = self._key(Item)
        existing = self.items.get(key)
        condition = str(kwargs.get("ConditionExpression") or "")
        names = kwargs.get("ExpressionAttributeNames") or {}
        unused_names = [name for name in names if name not in condition]
        if unused_names:
            raise AssertionError(
                f"Unused DynamoDB expression attribute names: {unused_names}"
            )
        values = kwargs.get("ExpressionAttributeValues") or {}
        if condition == "attribute_not_exists(#storage_key)" and existing is not None:
            raise ConditionalCheckFailed()
        if condition == "#revision = :expected_revision":
            expected = int(values[":expected_revision"])
            if existing is None or int(existing.get("revision", 0)) != expected:
                raise ConditionalCheckFailed()
        self.items[key] = deepcopy(Item)
        return {}

    def get_item(self, *, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        item = self.items.get(self._key(Key))
        return {"Item": deepcopy(item)} if item is not None else {}

    def delete_item(self, *, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        key = self._key(Key)
        item = self.items.get(key)
        condition = str(kwargs.get("ConditionExpression") or "")
        if condition == "#refresh_token = :refresh_token":
            values = kwargs.get("ExpressionAttributeValues") or {}
            expected = str(values.get(":refresh_token") or "")
            if item is None or str(item.get("refresh_token") or "") != expected:
                raise ConditionalCheckFailed()
        item = self.items.pop(key, None)
        if kwargs.get("ReturnValues") == "ALL_OLD" and item is not None:
            return {"Attributes": deepcopy(item)}
        return {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.query_calls.append(deepcopy(kwargs))
        values = kwargs["ExpressionAttributeValues"]
        owner_id = str(values[":owner_id"])
        if ":prefix" in values:
            prefix = str(values[":prefix"])
            matches = lambda storage_key: storage_key.startswith(prefix)
        else:
            start_key = str(values[":start_key"])
            end_key = str(values[":end_key"])
            matches = lambda storage_key: start_key <= storage_key <= end_key
        items = [
            deepcopy(item)
            for (stored_owner, storage_key), item in self.items.items()
            if stored_owner == owner_id and matches(storage_key)
        ]
        items.sort(key=lambda item: str(item["storage_key"]))
        limit = int(kwargs.get("Limit") or len(items))
        return {"Items": items[:limit]}



class FakeBatchWriter:
    def __init__(self, table: "FakeBatchDynamoTable") -> None:
        self.table = table

    def __enter__(self) -> "FakeBatchWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def put_item(self, *, Item: dict[str, Any]) -> None:
        self.table.put_item(Item=Item)


class FakeBatchDynamoTable(FakeDynamoTable):
    def __init__(self) -> None:
        super().__init__()
        self.batch_calls = 0

    def batch_writer(self, **_: Any) -> FakeBatchWriter:
        self.batch_calls += 1
        return FakeBatchWriter(self)

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
            {DISCOVERY_TABLE_CONFIG_KEY: "careerbridge_job_discovery"},
            table=self.table,
            clock=lambda: "2026-07-30T18:00:00+00:00",
        )

    def test_implements_discovery_contract_and_requires_separate_table(self) -> None:
        self.assertIsInstance(self.store, DiscoveryStore)
        with self.assertRaises(DiscoveryStorageConfigurationError):
            DynamoDBDiscoveryStore({})

    def test_shared_public_catalog_round_trips_jobs_and_deduplicates_refresh_lock(self) -> None:
        source = configured_source()
        key = public_source_key(source)

        self.assertTrue(
            self.store.try_acquire_public_refresh_lock(
                key,
                "token-a",
                acquired_at="2026-07-30T18:00:00+00:00",
                expires_at="2026-07-30T18:05:00+00:00",
            )
        )
        self.assertFalse(
            self.store.try_acquire_public_refresh_lock(
                key,
                "token-b",
                acquired_at="2026-07-30T18:01:00+00:00",
                expires_at="2026-07-30T18:06:00+00:00",
            )
        )
        status = self.store.sync_public_catalog(
            source,
            key,
            [discovered_job()],
            checked_at="2026-07-30T18:00:00+00:00",
            complete_scan=True,
        )
        self.store.release_public_refresh_lock(key, "token-a")

        self.assertEqual(1, status.job_count)
        self.assertEqual(status, self.store.get_public_catalog_status(key))
        self.assertEqual([status], self.store.list_public_catalog_statuses())
        jobs = self.store.list_public_catalog_jobs(key)
        self.assertEqual(1, len(jobs))
        self.assertEqual(PUBLIC_CATALOG_OWNER_ID, jobs[0].owner_id)
        self.assertIn(
            (PUBLIC_CATALOG_OWNER_ID, f"PUBLIC#SOURCE#{key}"), self.table.items
        )
        self.assertTrue(
            any(
                owner == PUBLIC_CATALOG_OWNER_ID
                and storage_key.startswith(f"PUBLIC#JOB#{key}#")
                for owner, storage_key in self.table.items
            )
        )

    def test_new_workday_source_uses_valid_dynamodb_create_condition(self) -> None:
        source = CompanySource(
            id="intel-workday",
            owner_id="owner-a",
            company_name="Intel",
            careers_url="https://intel.wd1.myworkdayjobs.com/en-US/External",
            source_type=JobSourceType.WORKDAY,
            source_identifier="External",
        )

        stored = self.store.put_company_source(source)

        self.assertEqual(1, stored.revision)
        self.assertEqual(JobSourceType.WORKDAY, stored.source_type)
        self.assertEqual(
            "https://intel.wd1.myworkdayjobs.com/en-US/External",
            stored.careers_url,
        )



    def test_discovered_jobs_use_dynamodb_batch_writer_when_available(self) -> None:
        table = FakeBatchDynamoTable()
        store = DynamoDBDiscoveryStore(
            {DISCOVERY_TABLE_CONFIG_KEY: "careerbridge_job_discovery"},
            table=table,
            clock=lambda: "2026-07-30T18:00:00+00:00",
        )
        source_record = store.put_company_source(configured_source())
        second = replace(
            discovered_job(),
            id=discovered_job_id("owner-a", "source-one", "external-2"),
            external_job_id="external-2",
            canonical_url="https://jobs.example.com/external-2",
        )

        stored = store.sync_discovered_jobs(
            source_record, [discovered_job(), second]
        )

        self.assertEqual(2, len(stored))
        self.assertEqual(1, table.batch_calls)

    def test_source_updates_use_optimistic_revision_and_reject_stale_copy(self) -> None:
        first_store = self.store
        second_store = DynamoDBDiscoveryStore(
            {DISCOVERY_TABLE_CONFIG_KEY: "careerbridge_job_discovery"},
            table=self.table,
        )
        created = first_store.put_company_source(configured_source())
        stale = second_store.get_company_source("owner-a", "source-one")

        updated = first_store.put_company_source(
            CompanySource(
                id=created.id,
                owner_id=created.owner_id,
                company_name=created.company_name,
                careers_url=created.careers_url,
                source_type=created.source_type,
                source_identifier=created.source_identifier,
                enabled=False,
                filters=created.filters,
                revision=created.revision,
            )
        )

        self.assertEqual(2, updated.revision)
        self.assertFalse(updated.enabled)
        with self.assertRaises(DiscoveryOptimisticLockError):
            second_store.put_company_source(stale)
        persisted = second_store.get_company_source("owner-a", "source-one")
        self.assertEqual(2, persisted.revision)
        self.assertFalse(persisted.enabled)

    def test_round_trips_owner_scoped_search_preferences(self) -> None:
        preferences = DiscoverySearchPreferences(
            owner_id="owner-a",
            target_titles=("Senior Data Engineer",),
            preferred_locations=("Portland, OR",),
            accepted_workplace_types=(WorkplaceType.HYBRID,),
            preferred_keywords=("Snowflake",),
            required_keywords=("SQL",),
            minimum_salary=150000,
            excluded_title_terms=("intern",),
            require_location_match=True,
            updated_at="2026-07-30T20:00:00+00:00",
        )
        self.store.put_search_preferences(preferences)

        self.assertEqual(
            preferences, self.store.get_search_preferences("owner-a")
        )
        self.assertIsNone(self.store.get_search_preferences("owner-b"))
        item = self.table.items[("owner-a", "PREFERENCES#SEARCH")]
        self.assertEqual("discovery_search_preferences", item["entity_type"])
        self.assertEqual(["intern"], item["excluded_title_terms"])


    def test_round_trips_owner_scoped_scan_schedule(self) -> None:
        schedule = DiscoveryScanSchedule(
            owner_id="owner-a",
            cadence=DiscoveryScheduleCadence.WEEKLY,
            local_hour=9,
            weekday=2,
            timezone_name="America/Los_Angeles",
            last_run_at="2026-07-29T16:00:00+00:00",
            updated_at="2026-07-30T20:00:00+00:00",
        )
        self.store.put_scan_schedule(schedule)
        self.assertEqual(schedule, self.store.get_scan_schedule("owner-a"))
        self.assertIsNone(self.store.get_scan_schedule("owner-b"))
        item = self.table.items[("owner-a", "PREFERENCES#SCHEDULE")]
        self.assertEqual("discovery_scan_schedule", item["entity_type"])

    def test_two_repository_instances_share_dynamodb_persistence(self) -> None:
        first = self.store
        second = DynamoDBDiscoveryStore(
            {DISCOVERY_TABLE_CONFIG_KEY: "careerbridge_job_discovery"},
            table=self.table,
        )
        source = first.put_company_source(configured_source())
        stored_job = first.sync_discovered_jobs(source, [discovered_job()])[0]

        self.assertEqual(source.id, second.get_company_source("owner-a", source.id).id)
        self.assertEqual(
            stored_job.id,
            second.get_discovered_job("owner-a", source.id, stored_job.id).id,
        )

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
            evidence_matches=(
                RequirementEvidenceMatch(
                    requirement_id="python",
                    requirement="Python",
                    status="supported",
                    evidence=(
                        EvidenceReference(
                            record_id="background-1",
                            record_type="career_background",
                            field_name="skill:python",
                            label="Resume source · Skill",
                            statement="Python",
                            verification_status="resume_source",
                        ),
                    ),
                ),
            ),
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

    def test_round_trips_saved_ignored_and_application_created_state(self) -> None:
        source = configured_source()
        job = self.store.sync_discovered_jobs(source, [discovered_job()])[0]
        saved = DiscoveryJobState(
            owner_id=job.owner_id,
            source_id=job.source_id,
            job_id=job.id,
            disposition=DiscoveryJobDisposition.SAVED,
            updated_at="2026-07-30T18:01:00+00:00",
        )
        self.store.put_job_state(saved)
        self.assertEqual(
            saved,
            self.store.get_job_state(job.owner_id, job.source_id, job.id),
        )

        converted = DiscoveryJobState(
            owner_id=job.owner_id,
            source_id=job.source_id,
            job_id=job.id,
            disposition=DiscoveryJobDisposition.APPLICATION_CREATED,
            application_id="application-1",
            updated_at="2026-07-30T18:02:00+00:00",
        )
        self.store.put_job_state(converted)
        self.assertEqual([converted], self.store.list_job_states(job.owner_id))
        item = self.table.items[(job.owner_id, f"STATE#{job.source_id}#{job.id}")]
        self.assertEqual(item["entity_type"], "discovery_job_state")

    def test_round_trips_cached_job_analysis_by_description_fingerprint(self) -> None:
        record = JobAnalysisRecord(
            job_id="job-1",
            owner_id="owner-a",
            description_fingerprint="description-abc",
            target_title="Senior Data Engineer",
            target_company="Example Bank",
            requirements=(
                {
                    "id": "python",
                    "category": "technical_skill",
                    "priority": "critical",
                    "requirement": "Python",
                    "keywords": ["Python"],
                },
            ),
            analyzed_at="2026-07-30T18:00:00+00:00",
        )

        self.store.put_job_analysis(record)

        self.assertEqual(
            record,
            self.store.get_job_analysis(
                "owner-a",
                "job-1",
                "description-abc",
            ),
        )
        self.assertIsNone(
            self.store.get_job_analysis("owner-a", "job-1", "description-changed")
        )

    def test_fit_snapshot_exact_key_uses_profile_and_description_fingerprints(self) -> None:
        first = JobFitSnapshot(
            job_id="job-1",
            owner_id="owner-a",
            profile_fingerprint="profile-a",
            description_fingerprint="description-a",
            fit_score=80,
            recommendation="Strong match",
            confidence="High",
            analyzed_at="2026-07-30T18:00:00+00:00",
        )
        second = JobFitSnapshot(
            job_id="job-1",
            owner_id="owner-a",
            profile_fingerprint="profile-a",
            description_fingerprint="description-b",
            fit_score=60,
            recommendation="Stretch",
            confidence="High",
            analyzed_at="2026-07-30T19:00:00+00:00",
        )
        self.store.put_fit_snapshot(first)
        self.store.put_fit_snapshot(second)

        self.assertEqual(
            first,
            self.store.get_fit_snapshot(
                "owner-a", "job-1", "profile-a", "description-a"
            ),
        )
        self.assertEqual(
            second,
            self.store.get_fit_snapshot(
                "owner-a", "job-1", "profile-a", "description-b"
            ),
        )
        self.assertEqual(
            second,
            self.store.get_fit_snapshot("owner-a", "job-1", "profile-a"),
        )

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
        still_active = self.store.list_discovered_jobs("owner-a", active_only=True)
        self.assertEqual(1, len(still_active))
        self.assertEqual(1, still_active[0].missed_scan_count)
        self.store.sync_discovered_jobs(
            source,
            [],
            checked_at="2026-07-30T21:00:00+00:00",
        )
        self.store.sync_discovered_jobs(
            source,
            [],
            checked_at="2026-07-30T22:00:00+00:00",
        )
        self.assertEqual([], self.store.list_discovered_jobs("owner-a", active_only=True))
        historical = self.store.list_discovered_jobs("owner-a", active_only=False)
        self.assertEqual(1, len(historical))
        self.assertFalse(historical[0].active)
        self.assertEqual("2026-07-30T19:00:00+00:00", historical[0].last_seen_at)


    def test_materialized_result_index_round_trips_and_is_invalidated(self) -> None:
        source = self.store.put_company_source(configured_source())
        job = self.store.sync_discovered_jobs(source, [discovered_job()])[0]
        revision = self.store.get_result_revision(job.owner_id)
        summary = DiscoveryResultIndexSummary(
            owner_id=job.owner_id,
            evidence_fingerprint="evidence-1",
            preference_fingerprint="preferences-1",
            revision_token=revision,
            pending_count=1,
        )
        record = DiscoveryResultRecord(
            owner_id=job.owner_id,
            evidence_fingerprint="evidence-1",
            preference_fingerprint="preferences-1",
            result_group="pending",
            job=replace(job, description="", skills=(), metadata={}),
            preference_score=82,
            freshness_score=95,
            posted_label="Posted 1 day ago",
            sort_rank="00000000",
        )

        self.store.replace_result_index(summary, [record])

        self.assertEqual(
            summary,
            self.store.get_result_index_summary(
                job.owner_id, "evidence-1", "preferences-1"
            ),
        )
        self.assertEqual(
            [record],
            self.store.list_result_records(
                job.owner_id, "evidence-1", "preferences-1", "pending"
            ),
        )
        self.assertEqual(
            [record],
            self.store.list_result_records_page(
                job.owner_id,
                "evidence-1",
                "preferences-1",
                "pending",
                offset=0,
                limit=10,
            ),
        )
        page_query = self.table.query_calls[-1]
        self.assertIn("BETWEEN", page_query["KeyConditionExpression"])
        self.assertEqual(10, page_query["Limit"])
        self.assertTrue(
            str(page_query["ExpressionAttributeValues"][":start_key"]).endswith(
                "GROUP#pending#00000000#"
            )
        )
        self.assertEqual(
            "",
            self.table.items[(
                job.owner_id,
                next(
                    key
                    for owner, key in self.table.items
                    if owner == job.owner_id and "GROUP#pending#" in key
                ),
            )]["job"]["description"],
        )

        self.store.put_job_state(
            DiscoveryJobState(
                owner_id=job.owner_id,
                source_id=job.source_id,
                job_id=job.id,
                disposition=DiscoveryJobDisposition.SAVED,
            )
        )
        self.assertNotEqual(revision, self.store.get_result_revision(job.owner_id))
        self.assertNotEqual(
            self.store.get_result_revision(job.owner_id),
            self.store.get_result_index_summary(
                job.owner_id, "evidence-1", "preferences-1"
            ).revision_token,
        )

    def test_materialized_index_supports_possible_and_low_match_groups(self) -> None:
        source = self.store.put_company_source(configured_source())
        job = self.store.sync_discovered_jobs(source, [discovered_job()])[0]
        revision = self.store.get_result_revision(job.owner_id)
        summary = DiscoveryResultIndexSummary(
            owner_id=job.owner_id,
            evidence_fingerprint="evidence-quality",
            preference_fingerprint="preferences-quality",
            revision_token=revision,
            possible_count=1,
            low_match_count=1,
        )
        possible = DiscoveryResultRecord(
            owner_id=job.owner_id,
            evidence_fingerprint="evidence-quality",
            preference_fingerprint="preferences-quality",
            result_group="possible",
            job=replace(job, description="", skills=(), metadata={}),
            recommendation_tier="stretch",
            confidence_tier="medium",
            visibility_category="possible",
            fit=JobFitSnapshot(
                job_id=job.id,
                owner_id=job.owner_id,
                profile_fingerprint="evidence-quality",
                description_fingerprint=job.description_fingerprint,
                fit_score=64,
                recommendation="Stretch opportunity — Apply selectively",
                confidence="Medium",
            ),
            preference_score=80,
            freshness_score=90,
            search_priority=70,
            posted_label="Posted today",
            sort_rank="00000000",
        )
        low_job = replace(
            job,
            id=job.id + "-low",
            external_job_id=job.external_job_id + "-low",
            canonical_url=job.canonical_url + "?low=1",
        )
        low_match = DiscoveryResultRecord(
            owner_id=job.owner_id,
            evidence_fingerprint="evidence-quality",
            preference_fingerprint="preferences-quality",
            result_group="low_match",
            job=replace(low_job, description="", skills=(), metadata={}),
            recommendation_tier="low",
            confidence_tier="high",
            visibility_category="low_match",
            fit=JobFitSnapshot(
                job_id=low_job.id,
                owner_id=job.owner_id,
                profile_fingerprint="evidence-quality",
                description_fingerprint=low_job.description_fingerprint,
                fit_score=45,
                recommendation="Low match — Probably not worth your time",
                confidence="High",
            ),
            preference_score=75,
            freshness_score=85,
            search_priority=55,
            posted_label="Posted today",
            sort_rank="00000000",
        )

        self.store.replace_result_index(summary, [possible, low_match])

        self.assertEqual(
            [possible],
            self.store.list_result_records_page(
                job.owner_id,
                "evidence-quality",
                "preferences-quality",
                "possible",
                offset=0,
                limit=10,
            ),
        )
        self.assertEqual(
            [low_match],
            self.store.list_result_records_page(
                job.owner_id,
                "evidence-quality",
                "preferences-quality",
                "low_match",
                offset=0,
                limit=10,
            ),
        )

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
            {
                "company_source",
                "discovered_job",
                "job_fit_snapshot",
                "discovery_result_revision",
            },
            entity_types,
        )
        self.assertFalse(any(key.startswith("APP#") for key in storage_keys))
        self.assertFalse(any(item.get("entity_type") == "application" for item in self.table.items.values()))


if __name__ == "__main__":
    unittest.main()
