from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module
from .storage_base import DiscoveryStore

"""DynamoDB discovery persistence for production."""

class DynamoDBDiscoveryStore:
    """Dedicated DynamoDB repository for source, posting, and fit records.

    The table is deliberately separate from the application table. It uses
    ``owner_id`` as the partition key and ``storage_key`` as the sort key:

    * ``SOURCE#<source_id>``
    * ``JOB#<source_id>#<job_id>``
    * ``STATE#<source_id>#<job_id>``
    * ``PREFERENCES#SEARCH``
    * ``PREFERENCES#SCHEDULE``
    * ``ANALYSIS#<job_id>#<description_fingerprint>``
    * ``FIT#<job_id>#<profile_fingerprint>#<description_fingerprint>``

    All queries are owner-scoped and prefix-based; the adapter never scans the
    table and never writes a JobApplication item.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        table: Any | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._table_override = table
        self._resolved_table: Any | None = None
        self._clock = clock or utc_now_iso
        table_name = str(config.get(DISCOVERY_TABLE_CONFIG_KEY) or "").strip()
        if table is None and not table_name:
            raise DiscoveryStorageConfigurationError(
                f"{DISCOVERY_TABLE_CONFIG_KEY} is required for DynamoDB job discovery storage."
            )
        self._table_name = table_name

    def _table(self) -> Any:
        if self._table_override is not None:
            return self._table_override
        if self._resolved_table is None:
            import boto3

            region = str(
                self._config.get("AWS_REGION")
                or self._config.get("AWS_DEFAULT_REGION")
                or "us-west-2"
            ).strip()
            self._resolved_table = boto3.resource("dynamodb", region_name=region).Table(
                self._table_name
            )
        return self._resolved_table

    @staticmethod
    def _key(owner_id: str, storage_key: str) -> dict[str, str]:
        return {"owner_id": owner_id, "storage_key": storage_key}

    def _put(self, item: dict[str, Any]) -> None:
        self._table().put_item(Item=_to_dynamodb(item))

    def _put_many(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        table = self._table()
        batch_writer = getattr(table, "batch_writer", None)
        if not callable(batch_writer):
            for item in items:
                self._put(item)
            return
        with batch_writer(overwrite_by_pkeys=["owner_id", "storage_key"]) as batch:
            for item in items:
                batch.put_item(Item=_to_dynamodb(item))

    def _delete_many(self, keys: list[dict[str, str]]) -> None:
        if not keys:
            return
        table = self._table()
        batch_writer = getattr(table, "batch_writer", None)
        if callable(batch_writer):
            with batch_writer(overwrite_by_pkeys=["owner_id", "storage_key"]) as batch:
                delete_item = getattr(batch, "delete_item", None)
                if callable(delete_item):
                    for key in keys:
                        delete_item(Key=key)
                    return
        for key in keys:
            table.delete_item(Key=key)

    def _mark_result_dirty(self, owner_id: str) -> str:
        token = uuid.uuid4().hex
        self._put(
            {
                "owner_id": owner_id,
                "storage_key": _RESULT_REVISION_KEY,
                "entity_type": "discovery_result_revision",
                "revision_token": token,
            }
        )
        return token

    def _put_versioned_source(self, source: CompanySource) -> CompanySource:
        expected_revision = int(source.revision)
        stored = _source_item(replace(source, revision=expected_revision + 1))
        values: dict[str, Any] = {}
        if expected_revision == 0:
            condition = "attribute_not_exists(#storage_key)"
            names = {"#storage_key": "storage_key"}
        else:
            condition = "#revision = :expected_revision"
            names = {"#revision": "revision"}
            values[":expected_revision"] = expected_revision
        kwargs: dict[str, Any] = {
            "Item": _to_dynamodb(stored),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
        }
        if values:
            kwargs["ExpressionAttributeValues"] = _to_dynamodb(values)
        try:
            self._table().put_item(**kwargs)
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
            if code == "ConditionalCheckFailedException":
                raise DiscoveryOptimisticLockError(
                    f"Source {source.id} was updated by another process; reload before saving."
                ) from exc
            raise
        return _company_source_from_dict(stored)

    def _get(self, owner_id: str, storage_key: str) -> dict[str, Any] | None:
        response = self._table().get_item(
            Key=self._key(owner_id, storage_key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return _from_dynamodb(item) if item else None

    def _query_prefix(self, owner_id: str, prefix: str) -> list[dict[str, Any]]:
        query_args: dict[str, Any] = {
            "KeyConditionExpression": "#owner_id = :owner_id AND begins_with(#storage_key, :prefix)",
            "ExpressionAttributeNames": {
                "#owner_id": "owner_id",
                "#storage_key": "storage_key",
            },
            "ExpressionAttributeValues": {
                ":owner_id": owner_id,
                ":prefix": prefix,
            },
        }
        items: list[dict[str, Any]] = []
        while True:
            response = self._table().query(**query_args)
            items.extend(_from_dynamodb(item) for item in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            query_args["ExclusiveStartKey"] = last_key

    def _query_range(
        self,
        owner_id: str,
        start_key: str,
        end_key: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        remaining = max(0, int(limit))
        if remaining == 0:
            return []
        query_args: dict[str, Any] = {
            "KeyConditionExpression": (
                "#owner_id = :owner_id AND #storage_key BETWEEN :start_key AND :end_key"
            ),
            "ExpressionAttributeNames": {
                "#owner_id": "owner_id",
                "#storage_key": "storage_key",
            },
            "ExpressionAttributeValues": {
                ":owner_id": owner_id,
                ":start_key": start_key,
                ":end_key": end_key,
            },
            "Limit": remaining,
        }
        items: list[dict[str, Any]] = []
        while remaining > 0:
            query_args["Limit"] = remaining
            response = self._table().query(**query_args)
            page = [_from_dynamodb(item) for item in response.get("Items", [])]
            items.extend(page)
            remaining = max(0, limit - len(items))
            last_key = response.get("LastEvaluatedKey")
            if not last_key or remaining == 0:
                return items
            query_args["ExclusiveStartKey"] = last_key
        return items

    def put_company_source(self, source: CompanySource) -> CompanySource:
        stored = self._put_versioned_source(source)
        self._mark_result_dirty(source.owner_id)
        return stored

    def get_company_source(self, owner_id: str, source_id: str) -> CompanySource | None:
        item = self._get(owner_id, _source_key(source_id))
        return _company_source_from_dict(item) if item else None

    def list_company_sources(self, owner_id: str, *, enabled_only: bool = False) -> list[CompanySource]:
        sources = [_company_source_from_dict(item) for item in self._query_prefix(owner_id, _SOURCE_PREFIX)]
        if enabled_only:
            sources = [source for source in sources if source.enabled]
        return sorted(sources, key=lambda source: (source.company_name.casefold(), source.id))

    def delete_company_source(self, owner_id: str, source_id: str) -> bool:
        response = self._table().delete_item(
            Key=self._key(owner_id, _source_key(source_id)),
            ReturnValues="ALL_OLD",
        )
        deleted = bool(response.get("Attributes"))
        if deleted:
            self._mark_result_dirty(owner_id)
        return deleted

    def put_search_preferences(
        self, preferences: DiscoverySearchPreferences
    ) -> DiscoverySearchPreferences:
        self._put(_search_preferences_item(preferences))
        self._mark_result_dirty(preferences.owner_id)
        return preferences

    def get_search_preferences(
        self, owner_id: str
    ) -> DiscoverySearchPreferences | None:
        item = self._get(owner_id, _PREFERENCES_KEY)
        return _search_preferences_from_dict(item) if item else None

    def put_scan_schedule(
        self, schedule: DiscoveryScanSchedule
    ) -> DiscoveryScanSchedule:
        self._put(_scan_schedule_item(schedule))
        return schedule

    def get_scan_schedule(
        self, owner_id: str
    ) -> DiscoveryScanSchedule | None:
        item = self._get(owner_id, _SCHEDULE_KEY)
        return _scan_schedule_from_dict(item) if item else None

    def sync_discovered_jobs(
        self,
        source: CompanySource,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str | None = None,
    ) -> list[DiscoveredJob]:
        checked = normalize_iso_timestamp(checked_at) or self._clock()
        _validate_sync(source, jobs)
        threshold = _deactivation_threshold(source)
        existing = {
            job.external_job_id: job
            for job in self.list_discovered_jobs(
                source.owner_id,
                source_id=source.id,
                active_only=False,
            )
        }
        synchronized: list[DiscoveredJob] = []
        seen_external_ids: set[str] = set()
        job_items: list[dict[str, Any]] = []
        for job in jobs:
            previous = existing.get(job.external_job_id)
            current = job.seen(
                checked,
                first_seen_at=previous.first_seen_at if previous else checked,
            )
            job_items.append(_job_item(current))
            synchronized.append(current)
            seen_external_ids.add(current.external_job_id)

        for external_id, previous in existing.items():
            if external_id in seen_external_ids or not previous.active:
                continue
            job_items.append(_job_item(previous.missed(threshold)))

        self._put_many(job_items)
        self._mark_result_dirty(source.owner_id)
        stored_source = self.get_company_source(source.owner_id, source.id)
        effective_source = source
        if stored_source is not None and source.revision == 0:
            effective_source = replace(source, revision=stored_source.revision)
        self._put_versioned_source(effective_source.checked(checked))
        return sorted(synchronized, key=_job_sort_key)

    def get_discovered_job(
        self,
        owner_id: str,
        source_id: str,
        job_id: str,
    ) -> DiscoveredJob | None:
        item = self._get(owner_id, _job_key(source_id, job_id))
        return _job_from_dict(item) if item else None

    def list_discovered_jobs(
        self,
        owner_id: str,
        *,
        source_id: str | None = None,
        active_only: bool = True,
    ) -> list[DiscoveredJob]:
        prefix = f"{_JOB_PREFIX}{source_id}#" if source_id else _JOB_PREFIX
        jobs = [_job_from_dict(item) for item in self._query_prefix(owner_id, prefix)]
        if active_only:
            jobs = [job for job in jobs if job.active]
        return sorted(jobs, key=_job_sort_key)

    def put_job_state(self, state: DiscoveryJobState) -> None:
        if self.get_discovered_job(state.owner_id, state.source_id, state.job_id) is None:
            raise ValueError("The discovered job does not exist.")
        self._put(_state_item(state))
        self._mark_result_dirty(state.owner_id)

    def get_job_state(
        self,
        owner_id: str,
        source_id: str,
        job_id: str,
    ) -> DiscoveryJobState | None:
        item = self._get(owner_id, _state_key(source_id, job_id))
        return _state_from_dict(item) if item else None

    def list_job_states(self, owner_id: str) -> list[DiscoveryJobState]:
        states = [_state_from_dict(item) for item in self._query_prefix(owner_id, _STATE_PREFIX)]
        return sorted(states, key=lambda item: item.updated_at, reverse=True)

    def put_job_analysis(self, analysis: JobAnalysisRecord) -> None:
        self._put(_analysis_item(analysis))

    def get_job_analysis(
        self,
        owner_id: str,
        job_id: str,
        description_fingerprint: str,
    ) -> JobAnalysisRecord | None:
        item = self._get(owner_id, _analysis_key(job_id, description_fingerprint))
        return _analysis_from_dict(item) if item else None

    def put_fit_snapshot(self, snapshot: JobFitSnapshot) -> None:
        self._put(_fit_item(snapshot))
        self._mark_result_dirty(snapshot.owner_id)

    def get_fit_snapshot(
        self,
        owner_id: str,
        job_id: str,
        profile_fingerprint: str,
        description_fingerprint: str = "",
    ) -> JobFitSnapshot | None:
        if description_fingerprint:
            item = self._get(
                owner_id,
                _fit_key(job_id, profile_fingerprint, description_fingerprint),
            )
            return _fit_from_dict(item) if item else None
        prefix = _fit_key(job_id, profile_fingerprint, "")
        matches = [_fit_from_dict(item) for item in self._query_prefix(owner_id, prefix)]
        return max(matches, key=lambda item: item.analyzed_at, default=None)

    def list_fit_snapshots(
        self,
        owner_id: str,
        *,
        job_id: str | None = None,
    ) -> list[JobFitSnapshot]:
        prefix = f"{_FIT_PREFIX}{job_id}#" if job_id else _FIT_PREFIX
        snapshots = [_fit_from_dict(item) for item in self._query_prefix(owner_id, prefix)]
        return sorted(snapshots, key=lambda item: (item.analyzed_at, item.job_id), reverse=True)

    def get_result_revision(self, owner_id: str) -> str:
        item = self._get(owner_id, _RESULT_REVISION_KEY)
        if item is not None and str(item.get("revision_token") or "").strip():
            return str(item["revision_token"])
        return self._mark_result_dirty(owner_id)

    def replace_result_index(
        self,
        summary: DiscoveryResultIndexSummary,
        records: list[DiscoveryResultRecord],
    ) -> None:
        current_revision = self.get_result_revision(summary.owner_id)
        if summary.revision_token != current_revision:
            return
        stale_items = [
            item
            for item in self._query_prefix(summary.owner_id, _RESULT_PREFIX)
            if str(item.get("entity_type") or "").startswith(
                "discovery_result_index"
            )
            or item.get("entity_type") == "discovery_result_record"
        ]
        self._delete_many(
            [self._key(summary.owner_id, str(item["storage_key"])) for item in stale_items]
        )
        for record in records:
            if (
                record.owner_id != summary.owner_id
                or record.evidence_fingerprint != summary.evidence_fingerprint
                or record.preference_fingerprint != summary.preference_fingerprint
            ):
                raise ValueError("result record does not belong to the supplied index")
        self._put_many([_result_record_item(item) for item in records])
        if self.get_result_revision(summary.owner_id) == summary.revision_token:
            self._put(_result_summary_item(summary))

    def get_result_index_summary(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
    ) -> DiscoveryResultIndexSummary | None:
        item = self._get(
            owner_id,
            _result_summary_key(evidence_fingerprint, preference_fingerprint),
        )
        return _result_summary_from_dict(item) if item else None

    def list_result_records(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
        result_group: str,
    ) -> list[DiscoveryResultRecord]:
        prefix = _result_group_prefix(
            evidence_fingerprint,
            preference_fingerprint,
            result_group,
        )
        return [
            _result_record_from_dict(item)
            for item in self._query_prefix(owner_id, prefix)
        ]

    def list_result_records_page(
        self,
        owner_id: str,
        evidence_fingerprint: str,
        preference_fingerprint: str,
        result_group: str,
        *,
        offset: int,
        limit: int,
    ) -> list[DiscoveryResultRecord]:
        prefix = _result_group_prefix(
            evidence_fingerprint,
            preference_fingerprint,
            result_group,
        )
        start = max(0, int(offset))
        size = max(0, int(limit))
        if size == 0:
            return []
        start_key = prefix + f"{start:08d}#"
        end_key = prefix + f"{start + size - 1:08d}#\uffff"
        return [
            _result_record_from_dict(item)
            for item in self._query_range(
                owner_id, start_key, end_key, limit=size
            )
        ]


    def get_public_catalog_status(
        self, source_key: str
    ) -> PublicJobCatalogStatus | None:
        item = self._get(PUBLIC_CATALOG_OWNER_ID, _public_source_key(source_key))
        return _public_catalog_status_from_dict(item) if item else None

    def list_public_catalog_statuses(self) -> list[PublicJobCatalogStatus]:
        statuses = [
            _public_catalog_status_from_dict(item)
            for item in self._query_prefix(
                PUBLIC_CATALOG_OWNER_ID, _PUBLIC_SOURCE_PREFIX
            )
        ]
        return sorted(statuses, key=lambda item: (item.company_name.casefold(), item.source_key))

    def list_public_catalog_jobs(
        self, source_key: str, *, active_only: bool = True
    ) -> list[DiscoveredJob]:
        jobs = [
            _job_from_dict(item)
            for item in self._query_prefix(
                PUBLIC_CATALOG_OWNER_ID, _public_job_group_prefix(source_key)
            )
        ]
        if active_only:
            jobs = [job for job in jobs if job.active]
        return sorted(jobs, key=_job_sort_key)

    def sync_public_catalog(
        self,
        source: CompanySource,
        source_key: str,
        jobs: list[DiscoveredJob],
        *,
        checked_at: str,
        complete_scan: bool,
    ) -> PublicJobCatalogStatus:
        checked = normalize_iso_timestamp(checked_at) or self._clock()
        threshold = _deactivation_threshold(source)
        existing = {
            job.external_job_id: job
            for job in self.list_public_catalog_jobs(source_key, active_only=False)
        }
        seen_external_ids: set[str] = set()
        projected = dict(existing)
        items: list[dict[str, Any]] = []
        for job in jobs:
            public_job = to_public_catalog_job(job, source_key)
            previous = existing.get(public_job.external_job_id)
            current = public_job.seen(
                checked,
                first_seen_at=previous.first_seen_at if previous else checked,
            )
            projected[current.external_job_id] = current
            items.append(_public_job_item(current, source_key))
            seen_external_ids.add(current.external_job_id)
        if complete_scan:
            for external_id, previous in existing.items():
                if external_id in seen_external_ids or not previous.active:
                    continue
                missed = previous.missed(threshold)
                projected[external_id] = missed
                items.append(_public_job_item(missed, source_key))
        self._put_many(items)
        active_count = sum(1 for item in projected.values() if item.active)
        status = PublicJobCatalogStatus(
            source_key=source_key,
            source_type=source.source_type,
            source_identifier=source.source_identifier,
            careers_url=source.careers_url,
            company_name=source.company_name,
            last_success_at=checked,
            last_attempt_at=checked,
            job_count=active_count,
            complete_scan=complete_scan,
            last_error="",
        )
        self._put(_public_catalog_status_item(status))
        return status

    def try_acquire_public_refresh_lock(
        self,
        source_key: str,
        refresh_token: str,
        *,
        acquired_at: str,
        expires_at: str,
    ) -> bool:
        now = normalize_iso_timestamp(acquired_at) or self._clock()
        expiry = normalize_iso_timestamp(expires_at)
        storage_key = _public_lock_key(source_key)
        existing = self._get(PUBLIC_CATALOG_OWNER_ID, storage_key)
        if existing and str(existing.get("expires_at") or "") > now:
            return False
        if existing:
            try:
                self._table().delete_item(
                    Key=self._key(PUBLIC_CATALOG_OWNER_ID, storage_key),
                    ConditionExpression="#refresh_token = :refresh_token",
                    ExpressionAttributeNames={"#refresh_token": "refresh_token"},
                    ExpressionAttributeValues=_to_dynamodb(
                        {":refresh_token": str(existing.get("refresh_token") or "")}
                    ),
                )
            except Exception as exc:
                response = getattr(exc, "response", {}) or {}
                code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
                if code == "ConditionalCheckFailedException":
                    return False
                raise
        item = {
            "owner_id": PUBLIC_CATALOG_OWNER_ID,
            "storage_key": storage_key,
            "entity_type": "public_job_catalog_refresh_lock",
            "refresh_token": str(refresh_token),
            "acquired_at": now,
            "expires_at": expiry,
            "ttl": int(datetime.fromisoformat(expiry).timestamp()),
        }
        try:
            self._table().put_item(
                Item=_to_dynamodb(item),
                ConditionExpression="attribute_not_exists(#storage_key)",
                ExpressionAttributeNames={"#storage_key": "storage_key"},
            )
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
            if code == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def release_public_refresh_lock(
        self, source_key: str, refresh_token: str
    ) -> None:
        storage_key = _public_lock_key(source_key)
        existing = self._get(PUBLIC_CATALOG_OWNER_ID, storage_key)
        if existing and str(existing.get("refresh_token") or "") == str(refresh_token):
            try:
                self._table().delete_item(
                    Key=self._key(PUBLIC_CATALOG_OWNER_ID, storage_key),
                    ConditionExpression="#refresh_token = :refresh_token",
                    ExpressionAttributeNames={"#refresh_token": "refresh_token"},
                    ExpressionAttributeValues=_to_dynamodb(
                        {":refresh_token": str(refresh_token)}
                    ),
                )
            except Exception as exc:
                response = getattr(exc, "response", {}) or {}
                code = ((response.get("Error") or {}).get("Code") if isinstance(response, dict) else "")
                if code != "ConditionalCheckFailedException":
                    raise

    def mark_public_catalog_failure(
        self,
        source: CompanySource,
        source_key: str,
        *,
        attempted_at: str,
        message: str,
    ) -> None:
        previous = self.get_public_catalog_status(source_key)
        status = PublicJobCatalogStatus(
            source_key=source_key,
            source_type=source.source_type,
            source_identifier=source.source_identifier,
            careers_url=source.careers_url,
            company_name=source.company_name,
            last_success_at=previous.last_success_at if previous else "",
            last_attempt_at=attempted_at,
            job_count=previous.job_count if previous else 0,
            complete_scan=previous.complete_scan if previous else False,
            last_error=message,
        )
        self._put(_public_catalog_status_item(status))

def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
