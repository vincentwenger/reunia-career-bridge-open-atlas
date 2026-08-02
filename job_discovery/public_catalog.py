from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from .models import (
    CompanySource,
    DiscoveredJob,
    JobSourceType,
    PublicJobCatalogStatus,
    discovered_job_id,
    normalize_iso_timestamp,
)
from .normalization import canonicalize_url

PUBLIC_CATALOG_OWNER_ID = "__PUBLIC_JOB_CATALOG__"
SHARED_CATALOG_SOURCE_OWNER_ID = "__SHARED_JOB_CATALOG_SOURCES__"

_DEFAULT_FRESHNESS_SECONDS = {
    JobSourceType.GREENHOUSE: 6 * 60 * 60,
    JobSourceType.LEVER: 6 * 60 * 60,
    JobSourceType.ASHBY: 6 * 60 * 60,
    JobSourceType.WORKDAY: 12 * 60 * 60,
    JobSourceType.SUCCESSFACTORS: 12 * 60 * 60,
    JobSourceType.ORACLE_CLOUD_HCM: 12 * 60 * 60,
    JobSourceType.ICIMS: 12 * 60 * 60,
    JobSourceType.SMARTRECRUITERS: 6 * 60 * 60,
    JobSourceType.AVATURE: 12 * 60 * 60,
    JobSourceType.EIGHTFOLD: 12 * 60 * 60,
    JobSourceType.TALEO: 12 * 60 * 60,
    JobSourceType.DAYFORCE: 12 * 60 * 60,
    JobSourceType.TALEMETRY_TTC: 12 * 60 * 60,
    JobSourceType.JOBVITE: 12 * 60 * 60,
    JobSourceType.UKG_PRO: 12 * 60 * 60,
    JobSourceType.PEOPLEADMIN: 12 * 60 * 60,
    JobSourceType.RADANCY_TALENTBREW: 12 * 60 * 60,
    JobSourceType.AMAZON_JOBS: 6 * 60 * 60,
    JobSourceType.BRANDED_REQUISITION: 12 * 60 * 60,
    JobSourceType.GENERIC_JSONLD: 24 * 60 * 60,
}


def _successfactors_catalog_url(value: str) -> str:
    canonical = canonicalize_url(value)
    parsed = urlsplit(canonical)
    path = parsed.path or "/"
    if "/search" not in path.casefold() and "/go/" not in path.casefold() and "company=" not in parsed.query.casefold():
        path = path.rstrip("/") + "/search/"
    return canonicalize_url(parsed._replace(path=path, fragment="").geturl())


def public_catalog_enabled(source: CompanySource) -> bool:
    value = source.filters.get("public_catalog_enabled", True)
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "no", "off"}
    return bool(value)


def public_source_key(source: CompanySource) -> str:
    """Stable identity shared by equivalent public company-source configurations."""

    selectors: dict[str, Any] = {}
    for name in ("locale", "search_text", "applied_facets"):
        value = source.filters.get(name)
        if value not in (None, "", {}, []):
            selectors[name] = value
    canonical_url = canonicalize_url(source.careers_url)
    if source.source_type is JobSourceType.SUCCESSFACTORS:
        source_location = _successfactors_catalog_url(source.careers_url)
    elif source.source_type is JobSourceType.ORACLE_CLOUD_HCM:
        from .sources.oracle_cloud_hcm import parse_oracle_cloud_hcm_careers_url

        source_location = parse_oracle_cloud_hcm_careers_url(
            source.careers_url
        ).listing_url
    elif source.source_type is JobSourceType.ICIMS:
        from .sources.icims import parse_icims_careers_url

        source_location = parse_icims_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.SMARTRECRUITERS:
        from .sources.smartrecruiters import parse_smartrecruiters_careers_url

        source_location = parse_smartrecruiters_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.AVATURE:
        from .sources.avature import parse_avature_careers_url

        source_location = parse_avature_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.EIGHTFOLD:
        from .sources.eightfold import parse_eightfold_careers_url

        source_location = parse_eightfold_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.TALEO:
        from .sources.taleo import parse_taleo_careers_url

        source_location = parse_taleo_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.DAYFORCE:
        from .sources.dayforce import parse_dayforce_careers_url

        source_location = parse_dayforce_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.TALEMETRY_TTC:
        from .sources.talemetry_ttc import parse_talemetry_ttc_careers_url

        source_location = parse_talemetry_ttc_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.JOBVITE:
        from .sources.jobvite import parse_jobvite_careers_url

        source_location = parse_jobvite_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.UKG_PRO:
        from .sources.ukg_pro import parse_ukg_pro_careers_url

        source_location = parse_ukg_pro_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.PEOPLEADMIN:
        from .sources.peopleadmin import parse_peopleadmin_careers_url

        source_location = parse_peopleadmin_careers_url(source.careers_url).listing_url
    elif source.source_type is JobSourceType.RADANCY_TALENTBREW:
        from .sources.radancy_talentbrew import (
            parse_radancy_talentbrew_careers_url,
        )

        source_location = parse_radancy_talentbrew_careers_url(
            source.careers_url
        ).listing_url
    elif source.source_type is JobSourceType.AMAZON_JOBS:
        from .sources.amazon_jobs import parse_amazon_jobs_careers_url

        source_location = parse_amazon_jobs_careers_url(
            source.careers_url
        ).listing_url
    elif source.source_type is JobSourceType.BRANDED_REQUISITION:
        from .sources.branded_requisition import (
            parse_branded_requisition_careers_url,
        )

        source_location = parse_branded_requisition_careers_url(
            source.careers_url
        ).listing_url
    elif source.source_type is JobSourceType.GENERIC_JSONLD:
        source_location = canonical_url
    elif source.source_type is JobSourceType.WORKDAY:
        parsed = urlsplit(canonical_url)
        source_location = parsed.hostname.casefold() if parsed.hostname else canonical_url
    else:
        source_location = source.source_identifier.strip().casefold()
    material = {
        "source_type": source.source_type.value,
        "source_identifier": source.source_identifier.strip().casefold(),
        "source_location": source_location,
        "selectors": selectors,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:40]


def catalog_freshness_seconds(source: CompanySource) -> int:
    default = _DEFAULT_FRESHNESS_SECONDS[source.source_type]
    try:
        value = int(source.filters.get("public_cache_ttl_seconds", default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, 5 * 60), 24 * 60 * 60)


def catalog_lock_seconds(source: CompanySource) -> int:
    try:
        value = int(source.filters.get("public_refresh_lock_seconds", 5 * 60))
    except (TypeError, ValueError):
        value = 5 * 60
    return min(max(value, 60), 30 * 60)


def parse_utc(value: str | datetime | None) -> datetime | None:
    normalized = normalize_iso_timestamp(value)
    if not normalized:
        return None
    return datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone(timezone.utc)


def is_catalog_fresh(
    status: PublicJobCatalogStatus | None,
    source: CompanySource,
    *,
    now: str | datetime,
    require_complete: bool = False,
) -> bool:
    if status is None or not status.last_success_at:
        return False
    if require_complete and not status.complete_scan:
        return False
    last_success = parse_utc(status.last_success_at)
    current = parse_utc(now)
    if last_success is None or current is None:
        return False
    return current - last_success <= timedelta(seconds=catalog_freshness_seconds(source))


def catalog_lock_expiry(source: CompanySource, acquired_at: str | datetime) -> str:
    acquired = parse_utc(acquired_at) or datetime.now(timezone.utc)
    return (acquired + timedelta(seconds=catalog_lock_seconds(source))).isoformat(timespec="seconds")


def to_public_catalog_job(job: DiscoveredJob, source_key: str) -> DiscoveredJob:
    return replace(
        job,
        id=discovered_job_id(PUBLIC_CATALOG_OWNER_ID, source_key, job.external_job_id),
        owner_id=PUBLIC_CATALOG_OWNER_ID,
        source_id=source_key,
    )


def materialize_catalog_job(job: DiscoveredJob, source: CompanySource) -> DiscoveredJob:
    return replace(
        job,
        id=discovered_job_id(source.owner_id, source.id, job.external_job_id),
        owner_id=source.owner_id,
        source_id=source.id,
        company=source.company_name,
    )
