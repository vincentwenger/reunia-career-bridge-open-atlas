"""Canonical company-source configuration shared by UI and catalog storage."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .models import CompanySource, JobSourceType
from .normalization import canonicalize_url

_LISTING_PARSER_PATHS = {
    JobSourceType.ORACLE_CLOUD_HCM: (
        "oracle_cloud_hcm",
        "parse_oracle_cloud_hcm_careers_url",
    ),
    JobSourceType.ICIMS: ("icims", "parse_icims_careers_url"),
    JobSourceType.SMARTRECRUITERS: (
        "smartrecruiters",
        "parse_smartrecruiters_careers_url",
    ),
    JobSourceType.AVATURE: ("avature", "parse_avature_careers_url"),
    JobSourceType.EIGHTFOLD: ("eightfold", "parse_eightfold_careers_url"),
    JobSourceType.TALEO: ("taleo", "parse_taleo_careers_url"),
    JobSourceType.DAYFORCE: ("dayforce", "parse_dayforce_careers_url"),
    JobSourceType.TALEMETRY_TTC: (
        "talemetry_ttc",
        "parse_talemetry_ttc_careers_url",
    ),
    JobSourceType.JOBVITE: ("jobvite", "parse_jobvite_careers_url"),
    JobSourceType.UKG_PRO: ("ukg_pro", "parse_ukg_pro_careers_url"),
    JobSourceType.PEOPLEADMIN: ("peopleadmin", "parse_peopleadmin_careers_url"),
    JobSourceType.RADANCY_TALENTBREW: (
        "radancy_talentbrew",
        "parse_radancy_talentbrew_careers_url",
    ),
    JobSourceType.AMAZON_JOBS: ("amazon_jobs", "parse_amazon_jobs_careers_url"),
    JobSourceType.BRANDED_REQUISITION: (
        "branded_requisition",
        "parse_branded_requisition_careers_url",
    ),
}


def _source_function(module_name: str, function_name: str) -> Callable[..., Any]:
    module = import_module(f"job_discovery.sources.{module_name}")
    return getattr(module, function_name)


def _listing_parser(source_type: JobSourceType) -> Callable[[str], Any] | None:
    path = _LISTING_PARSER_PATHS.get(source_type)
    return _source_function(*path) if path else None


def _parse_workday(careers_url: str, *, site_identifier: str = "") -> Any:
    parser = _source_function("workday", "parse_workday_careers_url")
    return parser(careers_url, site_identifier=site_identifier)


def _successfactors_search_url(careers_url: str) -> str:
    parser = _source_function("successfactors", "successfactors_search_url")
    return str(parser(careers_url))


_IDENTIFIER_FREE_SOURCE_TYPES = frozenset(
    {
        JobSourceType.ORACLE_CLOUD_HCM,
        JobSourceType.ICIMS,
        JobSourceType.SMARTRECRUITERS,
        JobSourceType.AVATURE,
        JobSourceType.EIGHTFOLD,
        JobSourceType.TALEO,
        JobSourceType.DAYFORCE,
        JobSourceType.TALEMETRY_TTC,
        JobSourceType.JOBVITE,
        JobSourceType.UKG_PRO,
        JobSourceType.PEOPLEADMIN,
        JobSourceType.RADANCY_TALENTBREW,
        JobSourceType.AMAZON_JOBS,
    }
)

_DEFAULT_BOARD_HOSTS = {
    JobSourceType.GREENHOUSE: "https://boards.greenhouse.io/{identifier}",
    JobSourceType.LEVER: "https://jobs.lever.co/{identifier}",
    JobSourceType.ASHBY: "https://jobs.ashbyhq.com/{identifier}",
}


def normalized_source_identifier(
    source_type: JobSourceType, raw: str, careers_url: str = ""
) -> str:
    """Return the identifier used by identifier-based public job boards."""

    value = str(raw or "").strip()
    candidate_url = value if "://" in value else str(careers_url or "").strip()
    if source_type is JobSourceType.WORKDAY and candidate_url:
        return _parse_workday(
            candidate_url,
            site_identifier="" if "://" in value else value,
        ).site
    if source_type is JobSourceType.SUCCESSFACTORS:
        return value.strip().strip("/")
    if source_type in _IDENTIFIER_FREE_SOURCE_TYPES:
        return ""
    if candidate_url and "://" in candidate_url:
        path_parts = [
            part for part in urlsplit(candidate_url).path.split("/") if part
        ]
        if path_parts:
            value = path_parts[0]
    return value.strip().strip("/")


def default_source_url(source_type: JobSourceType, identifier: str) -> str:
    """Build a public board URL for source types that use a short identifier."""

    value = str(identifier or "").strip().strip("/")
    template = _DEFAULT_BOARD_HOSTS.get(source_type)
    return template.format(identifier=value) if value and template else ""


def normalize_source_configuration(
    source_type: JobSourceType,
    careers_url: str,
    source_identifier: str = "",
) -> tuple[str, str]:
    """Return canonical ``(careers_url, source_identifier)`` values."""

    url = str(careers_url or "").strip()
    identifier = normalized_source_identifier(source_type, source_identifier, url)

    if source_type is JobSourceType.GENERIC_JSONLD:
        return url, ""
    if source_type is JobSourceType.SUCCESSFACTORS:
        return _successfactors_search_url(url), ""
    if source_type is JobSourceType.WORKDAY:
        target = _parse_workday(url, site_identifier=identifier)
        return target.careers_url, target.site

    parser = _listing_parser(source_type)
    if parser is not None:
        return parser(url).listing_url, ""

    return url or default_source_url(source_type, identifier), identifier


def _successfactors_catalog_url(value: str) -> str:
    """Preserve equivalent SuccessFactors listing URLs under one cache key."""

    canonical = canonicalize_url(value)
    parsed = urlsplit(canonical)
    path = parsed.path or "/"
    if (
        "/search" not in path.casefold()
        and "/go/" not in path.casefold()
        and "company=" not in parsed.query.casefold()
    ):
        path = path.rstrip("/") + "/search/"
    return canonicalize_url(
        urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
    )


def public_catalog_source_location(source: CompanySource) -> str:
    """Return the canonical source locator used to share public job scans."""

    canonical_url = canonicalize_url(source.careers_url)
    if source.source_type is JobSourceType.SUCCESSFACTORS:
        return _successfactors_catalog_url(source.careers_url)
    if source.source_type in _LISTING_PARSER_PATHS:
        parser = _listing_parser(source.source_type)
        assert parser is not None
        return parser(source.careers_url).listing_url
    if source.source_type is JobSourceType.GENERIC_JSONLD:
        return canonical_url
    if source.source_type is JobSourceType.WORKDAY:
        parsed = urlsplit(canonical_url)
        return parsed.hostname.casefold() if parsed.hostname else canonical_url
    return source.source_identifier.strip().casefold()
