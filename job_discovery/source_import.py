"""Bulk import parsing for centrally managed Job Discovery company sources."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Mapping

from job_discovery.models import JobSourceType


MAX_SOURCE_IMPORT_BYTES = 1_000_000
MAX_SOURCE_IMPORT_ROWS = 100


class CompanySourceImportError(ValueError):
    """Raised when an uploaded company-source configuration cannot be parsed."""


@dataclass(frozen=True, slots=True)
class CompanySourceImportRow:
    row_number: int
    company_name: str
    source_type: JobSourceType
    source_identifier: str = ""
    careers_url: str = ""
    enabled: bool = True


_FIELD_ALIASES = {
    "company": "company_name",
    "company_name": "company_name",
    "name": "company_name",
    "source_type": "source_type",
    "type": "source_type",
    "ats": "source_type",
    "ats_site_identifier": "source_identifier",
    "source_identifier": "source_identifier",
    "site_identifier": "source_identifier",
    "identifier": "source_identifier",
    "board": "source_identifier",
    "career_page_url": "careers_url",
    "career_page": "careers_url",
    "careers_url": "careers_url",
    "url": "careers_url",
    "enabled": "enabled",
    "active": "enabled",
}

_SOURCE_TYPE_ALIASES = {
    "greenhouse": JobSourceType.GREENHOUSE,
    "lever": JobSourceType.LEVER,
    "ashby": JobSourceType.ASHBY,
    "workday": JobSourceType.WORKDAY,
    "successfactors": JobSourceType.SUCCESSFACTORS,
    "success_factors": JobSourceType.SUCCESSFACTORS,
    "sap_successfactors": JobSourceType.SUCCESSFACTORS,
    "sap_success_factors": JobSourceType.SUCCESSFACTORS,
    "oracle_cloud_hcm": JobSourceType.ORACLE_CLOUD_HCM,
    "oracle_hcm": JobSourceType.ORACLE_CLOUD_HCM,
    "oracle_recruiting_cloud": JobSourceType.ORACLE_CLOUD_HCM,
    "oracle_recruiting": JobSourceType.ORACLE_CLOUD_HCM,
    "oracle_candidate_experience": JobSourceType.ORACLE_CLOUD_HCM,
    "icims": JobSourceType.ICIMS,
    "i_cims": JobSourceType.ICIMS,
    "icims_ats": JobSourceType.ICIMS,
    "icims_career_site": JobSourceType.ICIMS,
    "smartrecruiters": JobSourceType.SMARTRECRUITERS,
    "smart_recruiters": JobSourceType.SMARTRECRUITERS,
    "smartrecruiters_ats": JobSourceType.SMARTRECRUITERS,
    "avature": JobSourceType.AVATURE,
    "avature_ats": JobSourceType.AVATURE,
    "eightfold": JobSourceType.EIGHTFOLD,
    "eightfold_ai": JobSourceType.EIGHTFOLD,
    "taleo": JobSourceType.TALEO,
    "oracle_taleo": JobSourceType.TALEO,
    "taleo_enterprise": JobSourceType.TALEO,
    "dayforce": JobSourceType.DAYFORCE,
    "dayforce_hcm": JobSourceType.DAYFORCE,
    "ceridian_dayforce": JobSourceType.DAYFORCE,
    "talemetry": JobSourceType.TALEMETRY_TTC,
    "talemetry_ttc": JobSourceType.TALEMETRY_TTC,
    "talemetry_ttc_portals": JobSourceType.TALEMETRY_TTC,
    "ttc": JobSourceType.TALEMETRY_TTC,
    "ttc_portals": JobSourceType.TALEMETRY_TTC,
    "jobvite_talemetry": JobSourceType.TALEMETRY_TTC,
    "jobvite": JobSourceType.JOBVITE,
    "jobvite_hosted": JobSourceType.JOBVITE,
    "jobvite_career_site": JobSourceType.JOBVITE,
    "ukg": JobSourceType.UKG_PRO,
    "ukg_pro": JobSourceType.UKG_PRO,
    "ukg_pro_ultipro": JobSourceType.UKG_PRO,
    "ukg_pro_recruiting": JobSourceType.UKG_PRO,
    "ultipro": JobSourceType.UKG_PRO,
    "ulti_pro": JobSourceType.UKG_PRO,
    "peopleadmin": JobSourceType.PEOPLEADMIN,
    "people_admin": JobSourceType.PEOPLEADMIN,
    "peopleadmin_ats": JobSourceType.PEOPLEADMIN,
    "powerschool_peopleadmin": JobSourceType.PEOPLEADMIN,
    "highered_peopleadmin": JobSourceType.PEOPLEADMIN,
    "radancy": JobSourceType.RADANCY_TALENTBREW,
    "talentbrew": JobSourceType.RADANCY_TALENTBREW,
    "talent_brew": JobSourceType.RADANCY_TALENTBREW,
    "radancy_talentbrew": JobSourceType.RADANCY_TALENTBREW,
    "radancy_talent_brew": JobSourceType.RADANCY_TALENTBREW,
    "tmp_talentbrew": JobSourceType.RADANCY_TALENTBREW,
    "tmp_worldwide_talentbrew": JobSourceType.RADANCY_TALENTBREW,
    "amazon": JobSourceType.AMAZON_JOBS,
    "amazon_jobs": JobSourceType.AMAZON_JOBS,
    "amazon_careers": JobSourceType.AMAZON_JOBS,
    "amazonjobs": JobSourceType.AMAZON_JOBS,
    "branded_requisition": JobSourceType.BRANDED_REQUISITION,
    "branded_requisition_portal": JobSourceType.BRANDED_REQUISITION,
    "requisition_portal": JobSourceType.BRANDED_REQUISITION,
    "public_requisition_portal": JobSourceType.BRANDED_REQUISITION,
    "generic_jsonld": JobSourceType.GENERIC_JSONLD,
    "jsonld": JobSourceType.GENERIC_JSONLD,
    "json_ld": JobSourceType.GENERIC_JSONLD,
    "manual": JobSourceType.GENERIC_JSONLD,
    "manual_jsonld": JobSourceType.GENERIC_JSONLD,
    "manual_career_page_url": JobSourceType.GENERIC_JSONLD,
    "manual_career_page_url_json_ld": JobSourceType.GENERIC_JSONLD,
    "manual_career_page": JobSourceType.GENERIC_JSONLD,
}


def _normalized_key(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold())
    return text.strip("_")


def _normalized_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = _FIELD_ALIASES.get(_normalized_key(key))
        if canonical and canonical not in normalized:
            normalized[canonical] = value
    return normalized


def _parse_bool(value: Any, *, default: bool = True) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "y", "on", "enabled", "active"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "disabled", "inactive"}:
        return False
    raise CompanySourceImportError(
        f"enabled must be true/false, yes/no, or 1/0; received {value!r}"
    )


def _parse_source_type(value: Any) -> JobSourceType:
    normalized = _normalized_key(value)
    source_type = _SOURCE_TYPE_ALIASES.get(normalized)
    if source_type is None:
        supported = (
            "Greenhouse, Lever, Ashby, Workday, SAP SuccessFactors, "
            "Oracle Cloud HCM, iCIMS, SmartRecruiters, Avature, Eightfold, "
            "Taleo, Dayforce, Talemetry / TTC Portals, Jobvite, UKG Pro / UltiPro, "
            "PeopleAdmin, Radancy / TalentBrew, Amazon Jobs, or Manual career-page URL"
        )
        raise CompanySourceImportError(
            f"source_type {value!r} is not supported; use {supported}"
        )
    return source_type


def _row_from_mapping(raw: Mapping[str, Any], row_number: int) -> CompanySourceImportRow:
    item = _normalized_mapping(raw)
    company_name = " ".join(str(item.get("company_name") or "").split())
    if not company_name:
        raise CompanySourceImportError("company is required")
    source_type_value = item.get("source_type")
    if source_type_value is None or str(source_type_value).strip() == "":
        raise CompanySourceImportError("source_type is required")
    source_type = _parse_source_type(source_type_value)
    return CompanySourceImportRow(
        row_number=row_number,
        company_name=company_name,
        source_type=source_type,
        source_identifier=str(item.get("source_identifier") or "").strip(),
        careers_url=str(item.get("careers_url") or "").strip(),
        enabled=_parse_bool(item.get("enabled"), default=True),
    )


def _parse_csv(text: str) -> list[Mapping[str, Any]]:
    try:
        reader = csv.DictReader(StringIO(text))
    except csv.Error as exc:
        raise CompanySourceImportError(f"CSV could not be read: {exc}") from exc
    if not reader.fieldnames:
        raise CompanySourceImportError("CSV must include a header row")
    canonical_headers = {
        _FIELD_ALIASES.get(_normalized_key(header)) for header in reader.fieldnames
    }
    if "company_name" not in canonical_headers or "source_type" not in canonical_headers:
        raise CompanySourceImportError(
            "CSV headers must include Company and Source type"
        )
    try:
        return [dict(row) for row in reader]
    except csv.Error as exc:
        raise CompanySourceImportError(f"CSV could not be read: {exc}") from exc


def _parse_json(text: str) -> list[Mapping[str, Any]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CompanySourceImportError(
            f"JSON is invalid near line {exc.lineno}, column {exc.colno}"
        ) from exc
    if isinstance(payload, Mapping):
        for key in ("companies", "company_sources", "sources"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                payload = candidate
                break
    if not isinstance(payload, list):
        raise CompanySourceImportError(
            'JSON must be an array of company objects or an object containing a "companies" array'
        )
    if any(not isinstance(item, Mapping) for item in payload):
        raise CompanySourceImportError("Every JSON company entry must be an object")
    return list(payload)


def parse_company_source_import(
    filename: str,
    content: bytes,
    *,
    max_bytes: int = MAX_SOURCE_IMPORT_BYTES,
    max_rows: int = MAX_SOURCE_IMPORT_ROWS,
) -> list[CompanySourceImportRow]:
    """Parse an uploaded CSV or JSON company-source configuration."""

    name = str(filename or "").strip()
    if not name:
        raise CompanySourceImportError("Choose a CSV or JSON file to upload")
    suffix = Path(name).suffix.casefold()
    if suffix not in {".csv", ".json"}:
        raise CompanySourceImportError("Only .csv and .json files are supported")
    if not content:
        raise CompanySourceImportError("The uploaded file is empty")
    if len(content) > max_bytes:
        raise CompanySourceImportError(
            f"The uploaded file is too large; maximum size is {max_bytes // 1_000_000 or 1} MB"
        )
    if b"\x00" in content:
        raise CompanySourceImportError("The uploaded file is not valid text")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CompanySourceImportError("The uploaded file must use UTF-8 encoding") from exc

    raw_rows = _parse_csv(text) if suffix == ".csv" else _parse_json(text)
    if not raw_rows:
        raise CompanySourceImportError("The uploaded file does not contain any companies")
    if len(raw_rows) > max_rows:
        raise CompanySourceImportError(
            f"The uploaded file contains {len(raw_rows)} companies; the maximum is {max_rows}"
        )

    rows: list[CompanySourceImportRow] = []
    errors: list[str] = []
    for index, raw in enumerate(raw_rows, start=2 if suffix == ".csv" else 1):
        # Ignore completely blank CSV rows.
        if suffix == ".csv" and not any(str(value or "").strip() for value in raw.values()):
            continue
        try:
            rows.append(_row_from_mapping(raw, index))
        except CompanySourceImportError as exc:
            errors.append(f"Row {index}: {exc}")
    if errors:
        preview = "; ".join(errors[:5])
        remaining = len(errors) - 5
        if remaining > 0:
            preview += f"; and {remaining} more error{'s' if remaining != 1 else ''}"
        raise CompanySourceImportError(preview)
    if not rows:
        raise CompanySourceImportError("The uploaded file does not contain any companies")
    return rows
