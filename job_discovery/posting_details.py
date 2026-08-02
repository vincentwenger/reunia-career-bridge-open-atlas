from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import CompanySource, DiscoveredJob, JobSourceType
from .normalization import normalize_whitespace
from .sources.base import HttpClient, SourceFetchError, UrllibHttpClient
from .sources.generic_jsonld import GenericJsonLdJobSource
from .sources.workday import WorkdayJobSource
from .sources.successfactors import SuccessFactorsJobSource
from .sources.oracle_cloud_hcm import OracleCloudHcmJobSource
from .sources.icims import IcmsJobSource
from .sources.smartrecruiters import SmartRecruitersJobSource
from .sources.avature import AvatureJobSource
from .sources.eightfold import EightfoldJobSource
from .sources.taleo import TaleoJobSource
from .sources.dayforce import DayforceJobSource
from .sources.talemetry_ttc import TalemetryTtcJobSource
from .sources.jobvite import JobviteJobSource
from .sources.ukg_pro import UkgProJobSource
from .sources.peopleadmin import PeopleAdminJobSource
from .sources.radancy_talentbrew import RadancyTalentBrewJobSource
from .sources.amazon_jobs import AmazonJobsJobSource
from .sources.branded_requisition import BrandedRequisitionJobSource


MIN_COMPLETE_DESCRIPTION_CHARS = 500
MIN_COMPLETE_DESCRIPTION_WORDS = 75


@dataclass(frozen=True, slots=True)
class PostingDescriptionFetchResult:
    description: str
    attempted: bool = False
    refreshed: bool = False
    method: str = "stored"
    error: str = ""


class PostingDescriptionFetcherProtocol(Protocol):
    def fetch(self, job: DiscoveredJob) -> PostingDescriptionFetchResult:
        ...


def description_needs_enrichment(job: DiscoveredJob) -> bool:
    """Return whether the stored posting looks like a listing-card summary."""

    text = normalize_whitespace(job.description)
    detail_status = normalize_whitespace((job.metadata or {}).get("detail_status")).casefold()
    if detail_status in {"deferred", "failed", "missing"}:
        return True
    return (
        len(text) < MIN_COMPLETE_DESCRIPTION_CHARS
        or len(text.split()) < MIN_COMPLETE_DESCRIPTION_WORDS
    )


def is_more_complete_description(candidate: str, current: str) -> bool:
    candidate_text = normalize_whitespace(candidate)
    current_text = normalize_whitespace(current)
    if not candidate_text:
        return False
    if not current_text:
        return True
    candidate_words = len(candidate_text.split())
    current_words = len(current_text.split())
    return (
        len(candidate_text) >= len(current_text) + 120
        and candidate_words >= current_words + 20
    ) or (
        len(candidate_text) >= max(MIN_COMPLETE_DESCRIPTION_CHARS, int(len(current_text) * 1.5))
        and candidate_words >= max(MIN_COMPLETE_DESCRIPTION_WORDS, int(current_words * 1.35))
    )


class PostingDescriptionFetcher:
    """Resolve a full description for one posting when a workspace is opened.

    The targeted lookup is intentionally separate from bulk discovery refreshes.
    Workday, SuccessFactors, Oracle Cloud HCM, iCIMS, SmartRecruiters,
    Avature, Eightfold, Taleo, Dayforce, Talemetry / TTC Portals, UKG Pro / UltiPro, PeopleAdmin, Radancy / TalentBrew, and Amazon Jobs use their bounded public detail paths first. Other sources get a robots-aware JSON-LD lookup of the same URL
    used by View posting.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self.http = http_client or UrllibHttpClient()

    def fetch(self, job: DiscoveredJob) -> PostingDescriptionFetchResult:
        stored = normalize_whitespace(job.description)
        if not description_needs_enrichment(job):
            return PostingDescriptionFetchResult(description=stored)

        errors: list[str] = []
        if job.source_type is JobSourceType.WORKDAY:
            try:
                candidate = WorkdayJobSource(http_client=self.http).fetch_job_description(job)
            except (SourceFetchError, ValueError) as exc:
                errors.append(str(exc))
            else:
                if is_more_complete_description(candidate, stored):
                    return PostingDescriptionFetchResult(
                        description=normalize_whitespace(candidate),
                        attempted=True,
                        refreshed=True,
                        method="workday_detail",
                    )

        if job.source_type is JobSourceType.SUCCESSFACTORS:
            try:
                candidate = SuccessFactorsJobSource(http_client=self.http).fetch_job_description(job)
            except (SourceFetchError, ValueError) as exc:
                errors.append(str(exc))
            else:
                if is_more_complete_description(candidate, stored):
                    return PostingDescriptionFetchResult(
                        description=normalize_whitespace(candidate),
                        attempted=True,
                        refreshed=True,
                        method="successfactors_detail",
                    )

        if job.source_type is JobSourceType.ORACLE_CLOUD_HCM:
            try:
                candidate = OracleCloudHcmJobSource(
                    http_client=self.http
                ).fetch_job_description(job)
            except (SourceFetchError, ValueError) as exc:
                errors.append(str(exc))
            else:
                if is_more_complete_description(candidate, stored):
                    return PostingDescriptionFetchResult(
                        description=normalize_whitespace(candidate),
                        attempted=True,
                        refreshed=True,
                        method="oracle_cloud_hcm_detail",
                    )

        if job.source_type is JobSourceType.ICIMS:
            try:
                candidate = IcmsJobSource(http_client=self.http).fetch_job_description(job)
            except (SourceFetchError, ValueError) as exc:
                errors.append(str(exc))
            else:
                if is_more_complete_description(candidate, stored):
                    return PostingDescriptionFetchResult(
                        description=normalize_whitespace(candidate),
                        attempted=True,
                        refreshed=True,
                        method="icims_detail",
                    )

        dedicated_sources = {
            JobSourceType.SMARTRECRUITERS: (SmartRecruitersJobSource, "smartrecruiters_detail"),
            JobSourceType.AVATURE: (AvatureJobSource, "avature_detail"),
            JobSourceType.EIGHTFOLD: (EightfoldJobSource, "eightfold_detail"),
            JobSourceType.TALEO: (TaleoJobSource, "taleo_detail"),
            JobSourceType.DAYFORCE: (DayforceJobSource, "dayforce_detail"),
            JobSourceType.TALEMETRY_TTC: (TalemetryTtcJobSource, "talemetry_ttc_detail"),
            JobSourceType.JOBVITE: (JobviteJobSource, "jobvite_detail"),
            JobSourceType.UKG_PRO: (UkgProJobSource, "ukg_pro_detail"),
            JobSourceType.PEOPLEADMIN: (PeopleAdminJobSource, "peopleadmin_detail"),
            JobSourceType.RADANCY_TALENTBREW: (
                RadancyTalentBrewJobSource,
                "radancy_talentbrew_detail",
            ),
            JobSourceType.AMAZON_JOBS: (AmazonJobsJobSource, "amazon_jobs_detail"),
            JobSourceType.BRANDED_REQUISITION: (
                BrandedRequisitionJobSource,
                "branded_requisition_detail",
            ),
        }
        dedicated = dedicated_sources.get(job.source_type)
        if dedicated is not None:
            source_class, method = dedicated
            try:
                candidate = source_class(http_client=self.http).fetch_job_description(job)
            except (SourceFetchError, ValueError) as exc:
                errors.append(str(exc))
            else:
                if is_more_complete_description(candidate, stored):
                    return PostingDescriptionFetchResult(
                        description=normalize_whitespace(candidate),
                        attempted=True,
                        refreshed=True,
                        method=method,
                    )

        try:
            candidate = self._fetch_jsonld_description(job)
        except (SourceFetchError, ValueError) as exc:
            errors.append(str(exc))
        else:
            if is_more_complete_description(candidate, stored):
                return PostingDescriptionFetchResult(
                    description=normalize_whitespace(candidate),
                    attempted=True,
                    refreshed=True,
                    method="posting_page_jsonld",
                )

        return PostingDescriptionFetchResult(
            description=stored,
            attempted=True,
            refreshed=False,
            error="; ".join(dict.fromkeys(error for error in errors if error))[:1000],
        )

    def _fetch_jsonld_description(self, job: DiscoveredJob) -> str:
        source = CompanySource(
            id=f"detail-{job.source_id}",
            owner_id=job.owner_id,
            company_name=job.company,
            careers_url=job.canonical_url,
            source_type=JobSourceType.GENERIC_JSONLD,
            source_identifier="",
            filters={
                "max_pages": 1,
                "follow_job_links": False,
                "cache_seconds": 0,
                "timeout_seconds": 8.0,
                "max_redirects": 3,
                "min_request_interval_seconds": 0.0,
            },
        )
        candidates = GenericJsonLdJobSource(http_client=self.http).fetch_jobs(source)
        descriptions = [normalize_whitespace(item.description) for item in candidates]
        descriptions = [value for value in descriptions if value]
        if not descriptions:
            return ""
        title_key = normalize_whitespace(job.title).casefold()
        title_matches = [
            item.description
            for item in candidates
            if normalize_whitespace(item.title).casefold() == title_key and item.description
        ]
        pool = title_matches or descriptions
        return max(pool, key=lambda value: (len(normalize_whitespace(value).split()), len(value)))
