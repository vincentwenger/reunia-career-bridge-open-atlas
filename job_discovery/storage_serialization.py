from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module, module_exports

"""DynamoDB keys, serialization, and conversion helpers for discovery stores."""

def _deactivation_threshold(source: CompanySource, default: int = 3) -> int:
    try:
        threshold = int(source.filters.get("deactivate_after_missed_scans", default))
    except (TypeError, ValueError):
        threshold = default
    return min(max(threshold, 2), 10)


def _validate_sync(source: CompanySource, jobs: list[DiscoveredJob]) -> None:
    for job in jobs:
        if job.owner_id != source.owner_id:
            raise ValueError("job owner_id must match source owner_id")
        if job.source_id != source.id:
            raise ValueError("job source_id must match source id")


def _source_key(source_id: str) -> str:
    return f"{_SOURCE_PREFIX}{source_id}"


def _job_key(source_id: str, job_id: str) -> str:
    return f"{_JOB_PREFIX}{source_id}#{job_id}"


def _state_key(source_id: str, job_id: str) -> str:
    return f"{_STATE_PREFIX}{source_id}#{job_id}"


def _analysis_key(job_id: str, description_fingerprint: str) -> str:
    return f"{_ANALYSIS_PREFIX}{job_id}#{description_fingerprint}"


def _fit_key(job_id: str, profile_fingerprint: str, description_fingerprint: str) -> str:
    suffix = f"#{description_fingerprint}" if description_fingerprint else "#"
    return f"{_FIT_PREFIX}{job_id}#{profile_fingerprint}{suffix}"


def _result_index_prefix(evidence_fingerprint: str, preference_fingerprint: str) -> str:
    return f"{_RESULT_PREFIX}{evidence_fingerprint}#{preference_fingerprint}#"


def _result_summary_key(evidence_fingerprint: str, preference_fingerprint: str) -> str:
    return _result_index_prefix(evidence_fingerprint, preference_fingerprint) + "META"


def _result_group_prefix(
    evidence_fingerprint: str,
    preference_fingerprint: str,
    result_group: str,
) -> str:
    group = str(result_group or "").strip().casefold()
    if group not in {"recommended", "possible", "pending", "low_match", "saved", "ignored"}:
        raise ValueError("Unknown result group")
    return _result_index_prefix(evidence_fingerprint, preference_fingerprint) + f"GROUP#{group}#"


def _result_record_key(record: DiscoveryResultRecord) -> str:
    return (
        _result_group_prefix(
            record.evidence_fingerprint,
            record.preference_fingerprint,
            record.result_group,
        )
        + f"{record.sort_rank}#{record.job.source_id}#{record.job.id}"
    )


def _public_source_key(source_key: str) -> str:
    return f"{_PUBLIC_SOURCE_PREFIX}{source_key}"


def _public_job_group_prefix(source_key: str) -> str:
    return f"{_PUBLIC_JOB_PREFIX}{source_key}#"


def _public_job_key(source_key: str, job_id: str) -> str:
    return _public_job_group_prefix(source_key) + job_id


def _public_lock_key(source_key: str) -> str:
    return f"{_PUBLIC_LOCK_PREFIX}{source_key}"


def _job_sort_key(job: DiscoveredJob) -> tuple[str, str, str]:
    return job.company.casefold(), job.title.casefold(), job.external_job_id


def _public_catalog_status_item(status: PublicJobCatalogStatus) -> dict[str, Any]:
    item = _public_catalog_status_to_dict(status)
    item.update(
        {
            "owner_id": PUBLIC_CATALOG_OWNER_ID,
            "storage_key": _public_source_key(status.source_key),
            "entity_type": "public_job_catalog_status",
        }
    )
    return item


def _public_job_item(job: DiscoveredJob, source_key: str) -> dict[str, Any]:
    item = _job_to_dict(job)
    item.update(
        {
            "owner_id": PUBLIC_CATALOG_OWNER_ID,
            "storage_key": _public_job_key(source_key, job.id),
            "entity_type": "public_job_catalog_posting",
        }
    )
    return item


def _source_item(source: CompanySource) -> dict[str, Any]:
    item = _company_source_to_dict(source)
    item.update(
        {
            "owner_id": source.owner_id,
            "storage_key": _source_key(source.id),
            "entity_type": "company_source",
        }
    )
    return item


def _search_preferences_item(
    preferences: DiscoverySearchPreferences,
) -> dict[str, Any]:
    item = _search_preferences_to_dict(preferences)
    item.update(
        {
            "owner_id": preferences.owner_id,
            "storage_key": _PREFERENCES_KEY,
            "entity_type": "discovery_search_preferences",
        }
    )
    return item


def _scan_schedule_item(schedule: DiscoveryScanSchedule) -> dict[str, Any]:
    item = _scan_schedule_to_dict(schedule)
    item.update(
        {
            "owner_id": schedule.owner_id,
            "storage_key": _SCHEDULE_KEY,
            "entity_type": "discovery_scan_schedule",
        }
    )
    return item


def _job_item(job: DiscoveredJob) -> dict[str, Any]:
    item = _job_to_dict(job)
    item.update(
        {
            "owner_id": job.owner_id,
            "storage_key": _job_key(job.source_id, job.id),
            "entity_type": "discovered_job",
        }
    )
    return item


def _state_item(state: DiscoveryJobState) -> dict[str, Any]:
    item = _state_to_dict(state)
    item.update(
        {
            "owner_id": state.owner_id,
            "storage_key": _state_key(state.source_id, state.job_id),
            "entity_type": "discovery_job_state",
        }
    )
    return item


def _analysis_item(analysis: JobAnalysisRecord) -> dict[str, Any]:
    item = _analysis_to_dict(analysis)
    item.update(
        {
            "owner_id": analysis.owner_id,
            "storage_key": _analysis_key(analysis.job_id, analysis.description_fingerprint),
            "entity_type": "job_analysis",
        }
    )
    return item


def _fit_item(snapshot: JobFitSnapshot) -> dict[str, Any]:
    item = _fit_to_dict(snapshot)
    item.update(
        {
            "owner_id": snapshot.owner_id,
            "storage_key": _fit_key(
                snapshot.job_id,
                snapshot.profile_fingerprint,
                snapshot.description_fingerprint,
            ),
            "entity_type": "job_fit_snapshot",
        }
    )
    return item


def _result_summary_item(summary: DiscoveryResultIndexSummary) -> dict[str, Any]:
    item = _result_summary_to_dict(summary)
    item.update(
        {
            "owner_id": summary.owner_id,
            "storage_key": _result_summary_key(
                summary.evidence_fingerprint,
                summary.preference_fingerprint,
            ),
            "entity_type": "discovery_result_index_summary",
        }
    )
    return item


def _result_record_item(record: DiscoveryResultRecord) -> dict[str, Any]:
    item = _result_record_to_dict(record)
    item.update(
        {
            "owner_id": record.owner_id,
            "storage_key": _result_record_key(record),
            "entity_type": "discovery_result_record",
        }
    )
    return item


def _public_catalog_status_to_dict(status: PublicJobCatalogStatus) -> dict[str, Any]:
    return {
        "source_key": status.source_key,
        "source_type": status.source_type.value,
        "source_identifier": status.source_identifier,
        "careers_url": status.careers_url,
        "company_name": status.company_name,
        "last_success_at": status.last_success_at,
        "last_attempt_at": status.last_attempt_at,
        "job_count": status.job_count,
        "complete_scan": status.complete_scan,
        "last_error": status.last_error,
    }


def _public_catalog_status_from_dict(data: Mapping[str, Any]) -> PublicJobCatalogStatus:
    return PublicJobCatalogStatus(
        source_key=str(data.get("source_key") or ""),
        source_type=str(data.get("source_type") or JobSourceType.GENERIC_JSONLD.value),
        source_identifier=str(data.get("source_identifier") or ""),
        careers_url=str(data.get("careers_url") or ""),
        company_name=str(data.get("company_name") or ""),
        last_success_at=str(data.get("last_success_at") or ""),
        last_attempt_at=str(data.get("last_attempt_at") or ""),
        job_count=int(data.get("job_count") or 0),
        complete_scan=bool(data.get("complete_scan", True)),
        last_error=str(data.get("last_error") or ""),
    )


def _company_source_to_dict(source: CompanySource) -> dict[str, Any]:
    return {
        "id": source.id,
        "owner_id": source.owner_id,
        "company_name": source.company_name,
        "careers_url": source.careers_url,
        "source_type": source.source_type.value,
        "source_identifier": source.source_identifier,
        "enabled": source.enabled,
        "last_checked_at": source.last_checked_at,
        "filters": dict(source.filters),
        "revision": source.revision,
    }


def _company_source_from_dict(payload: Mapping[str, Any]) -> CompanySource:
    return CompanySource(
        id=str(payload.get("id") or ""),
        owner_id=str(payload.get("owner_id") or ""),
        company_name=str(payload.get("company_name") or ""),
        careers_url=str(payload.get("careers_url") or ""),
        source_type=str(payload.get("source_type") or ""),
        source_identifier=str(payload.get("source_identifier") or ""),
        enabled=bool(payload.get("enabled", True)),
        last_checked_at=str(payload.get("last_checked_at") or ""),
        filters=dict(payload.get("filters") or {}),
        revision=int(payload.get("revision") or 0),
    )


def _search_preferences_to_dict(
    preferences: DiscoverySearchPreferences,
) -> dict[str, Any]:
    return {
        "owner_id": preferences.owner_id,
        "target_titles": list(preferences.target_titles),
        "preferred_locations": list(preferences.preferred_locations),
        "accepted_workplace_types": [
            item.value for item in preferences.accepted_workplace_types
        ],
        "preferred_employment_types": list(preferences.preferred_employment_types),
        "preferred_keywords": list(preferences.preferred_keywords),
        "required_keywords": list(preferences.required_keywords),
        "minimum_salary": preferences.minimum_salary,
        "minimum_salary_currency": preferences.minimum_salary_currency,
        "minimum_salary_interval": preferences.minimum_salary_interval,
        "excluded_terms": list(preferences.excluded_terms),
        "excluded_title_terms": list(preferences.excluded_title_terms),
        "maximum_posting_age_days": preferences.maximum_posting_age_days,
        "require_title_match": preferences.require_title_match,
        "require_location_match": preferences.require_location_match,
        "require_workplace_match": preferences.require_workplace_match,
        "require_employment_type_match": preferences.require_employment_type_match,
        "updated_at": preferences.updated_at,
    }


def _search_preferences_from_dict(
    payload: Mapping[str, Any],
) -> DiscoverySearchPreferences:
    return DiscoverySearchPreferences(
        owner_id=str(payload.get("owner_id") or ""),
        target_titles=tuple(payload.get("target_titles") or ()),
        preferred_locations=tuple(payload.get("preferred_locations") or ()),
        accepted_workplace_types=tuple(payload.get("accepted_workplace_types") or ()),
        preferred_employment_types=tuple(
            payload.get("preferred_employment_types") or ()
        ),
        preferred_keywords=tuple(payload.get("preferred_keywords") or ()),
        required_keywords=tuple(payload.get("required_keywords") or ()),
        minimum_salary=payload.get("minimum_salary"),
        minimum_salary_currency=str(
            payload.get("minimum_salary_currency") or "USD"
        ),
        minimum_salary_interval=str(
            payload.get("minimum_salary_interval") or "year"
        ),
        excluded_terms=tuple(payload.get("excluded_terms") or ()),
        excluded_title_terms=tuple(payload.get("excluded_title_terms") or ()),
        maximum_posting_age_days=payload.get("maximum_posting_age_days", 30),
        require_title_match=bool(payload.get("require_title_match", False)),
        require_location_match=bool(
            payload.get("require_location_match", False)
        ),
        require_workplace_match=bool(
            payload.get("require_workplace_match", False)
        ),
        require_employment_type_match=bool(
            payload.get("require_employment_type_match", False)
        ),
        updated_at=str(payload.get("updated_at") or ""),
    )


def _scan_schedule_to_dict(schedule: DiscoveryScanSchedule) -> dict[str, Any]:
    return {
        "owner_id": schedule.owner_id,
        "cadence": schedule.cadence.value,
        "local_hour": schedule.local_hour,
        "weekday": schedule.weekday,
        "timezone_name": schedule.timezone_name,
        "last_run_at": schedule.last_run_at,
        "updated_at": schedule.updated_at,
    }


def _scan_schedule_from_dict(payload: Mapping[str, Any]) -> DiscoveryScanSchedule:
    return DiscoveryScanSchedule(
        owner_id=str(payload.get("owner_id") or ""),
        cadence=str(payload.get("cadence") or DiscoveryScheduleCadence.MANUAL.value),
        local_hour=int(payload.get("local_hour", 8)),
        weekday=int(payload.get("weekday", 0)),
        timezone_name=str(payload.get("timezone_name") or "UTC"),
        last_run_at=str(payload.get("last_run_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def _job_to_dict(job: DiscoveredJob) -> dict[str, Any]:
    payload = asdict(job)
    payload["source_type"] = job.source_type.value
    payload["workplace_type"] = job.workplace_type.value
    payload["locations"] = list(job.locations)
    payload["skills"] = list(job.skills)
    return payload


def _job_from_dict(payload: Mapping[str, Any]) -> DiscoveredJob:
    allowed = {field.name for field in DiscoveredJob.__dataclass_fields__.values()}
    data = {key: value for key, value in payload.items() if key in allowed}
    data["source_type"] = JobSourceType(str(data.get("source_type") or JobSourceType.GENERIC_JSONLD.value))
    data["workplace_type"] = WorkplaceType(str(data.get("workplace_type") or WorkplaceType.UNSPECIFIED.value))
    data["locations"] = tuple(data.get("locations") or ())
    data["skills"] = tuple(data.get("skills") or ())
    data["metadata"] = dict(data.get("metadata") or {})
    return DiscoveredJob(**data)


def _state_to_dict(state: DiscoveryJobState) -> dict[str, Any]:
    return {
        "owner_id": state.owner_id,
        "source_id": state.source_id,
        "job_id": state.job_id,
        "disposition": state.disposition.value,
        "application_id": state.application_id,
        "updated_at": state.updated_at,
    }


def _state_from_dict(payload: Mapping[str, Any]) -> DiscoveryJobState:
    return DiscoveryJobState(
        owner_id=str(payload.get("owner_id") or ""),
        source_id=str(payload.get("source_id") or ""),
        job_id=str(payload.get("job_id") or ""),
        disposition=DiscoveryJobDisposition(str(payload.get("disposition") or "")),
        application_id=str(payload.get("application_id") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def _analysis_to_dict(analysis: JobAnalysisRecord) -> dict[str, Any]:
    payload = asdict(analysis)
    payload["requirements"] = [dict(item) for item in analysis.requirements]
    payload["ignored_boilerplate"] = list(analysis.ignored_boilerplate)
    return payload


def _analysis_from_dict(payload: Mapping[str, Any]) -> JobAnalysisRecord:
    allowed = {field.name for field in JobAnalysisRecord.__dataclass_fields__.values()}
    data = {key: value for key, value in payload.items() if key in allowed}
    data["requirements"] = tuple(dict(item) for item in data.get("requirements") or ())
    data["ignored_boilerplate"] = tuple(data.get("ignored_boilerplate") or ())
    return JobAnalysisRecord(**data)


def _fit_to_dict(snapshot: JobFitSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    for name in (
        "supported_requirements",
        "partial_requirements",
        "unsupported_requirements",
        "hard_blockers",
    ):
        payload[name] = list(payload[name])
    return payload


def _fit_from_dict(payload: Mapping[str, Any]) -> JobFitSnapshot:
    allowed = {field.name for field in JobFitSnapshot.__dataclass_fields__.values()}
    data = {key: value for key, value in payload.items() if key in allowed}
    for name in (
        "supported_requirements",
        "partial_requirements",
        "unsupported_requirements",
        "hard_blockers",
        "evidence_matches",
    ):
        data[name] = tuple(data.get(name) or ())
    return JobFitSnapshot(**data)


def _result_summary_to_dict(summary: DiscoveryResultIndexSummary) -> dict[str, Any]:
    return asdict(summary)


def _result_summary_from_dict(
    payload: Mapping[str, Any],
) -> DiscoveryResultIndexSummary:
    allowed = {
        field.name for field in DiscoveryResultIndexSummary.__dataclass_fields__.values()
    }
    return DiscoveryResultIndexSummary(
        **{key: value for key, value in payload.items() if key in allowed}
    )


def _result_record_to_dict(record: DiscoveryResultRecord) -> dict[str, Any]:
    return {
        "owner_id": record.owner_id,
        "evidence_fingerprint": record.evidence_fingerprint,
        "preference_fingerprint": record.preference_fingerprint,
        "result_group": record.result_group,
        "job": _job_to_dict(record.job),
        "recommendation_tier": record.recommendation_tier,
        "confidence_tier": record.confidence_tier,
        "visibility_category": record.visibility_category,
        "disposition": record.disposition.value if record.disposition else "",
        "application_id": record.application_id,
        "fit": _fit_to_dict(record.fit) if record.fit is not None else None,
        "preference_score": record.preference_score,
        "freshness_score": record.freshness_score,
        "search_priority": record.search_priority,
        "posted_label": record.posted_label,
        "sort_rank": record.sort_rank,
        "updated_at": record.updated_at,
    }


def _result_record_from_dict(payload: Mapping[str, Any]) -> DiscoveryResultRecord:
    fit_payload = payload.get("fit")
    disposition = str(payload.get("disposition") or "").strip() or None
    return DiscoveryResultRecord(
        owner_id=str(payload.get("owner_id") or ""),
        evidence_fingerprint=str(payload.get("evidence_fingerprint") or ""),
        preference_fingerprint=str(payload.get("preference_fingerprint") or ""),
        result_group=str(payload.get("result_group") or ""),
        job=_job_from_dict(dict(payload.get("job") or {})),
        recommendation_tier=str(payload.get("recommendation_tier") or "unassessed"),
        confidence_tier=str(payload.get("confidence_tier") or "unassessed"),
        visibility_category=str(payload.get("visibility_category") or payload.get("result_group") or ""),
        disposition=disposition,
        application_id=str(payload.get("application_id") or ""),
        fit=_fit_from_dict(dict(fit_payload)) if fit_payload else None,
        preference_score=float(payload.get("preference_score") or 0),
        freshness_score=float(payload.get("freshness_score") or 0),
        search_priority=(
            float(payload["search_priority"])
            if payload.get("search_priority") is not None
            else None
        ),
        posted_label=str(payload.get("posted_label") or ""),
        sort_rank=str(payload.get("sort_rank") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def _to_dynamodb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(key): _to_dynamodb(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamodb(item) for item in value]
    return value


def _from_dynamodb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _from_dynamodb(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamodb(item) for item in value]
    return value

_EXPORT_NAMES = (
    '_deactivation_threshold',
    '_validate_sync',
    '_source_key',
    '_job_key',
    '_state_key',
    '_analysis_key',
    '_fit_key',
    '_result_index_prefix',
    '_result_summary_key',
    '_result_group_prefix',
    '_result_record_key',
    '_public_source_key',
    '_public_job_group_prefix',
    '_public_job_key',
    '_public_lock_key',
    '_job_sort_key',
    '_public_catalog_status_item',
    '_public_job_item',
    '_source_item',
    '_search_preferences_item',
    '_scan_schedule_item',
    '_job_item',
    '_state_item',
    '_analysis_item',
    '_fit_item',
    '_result_summary_item',
    '_result_record_item',
    '_public_catalog_status_to_dict',
    '_public_catalog_status_from_dict',
    '_company_source_to_dict',
    '_company_source_from_dict',
    '_search_preferences_to_dict',
    '_search_preferences_from_dict',
    '_scan_schedule_to_dict',
    '_scan_schedule_from_dict',
    '_job_to_dict',
    '_job_from_dict',
    '_state_to_dict',
    '_state_from_dict',
    '_analysis_to_dict',
    '_analysis_from_dict',
    '_fit_to_dict',
    '_fit_from_dict',
    '_result_summary_to_dict',
    '_result_summary_from_dict',
    '_result_record_to_dict',
    '_result_record_from_dict',
    '_to_dynamodb',
    '_from_dynamodb',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
