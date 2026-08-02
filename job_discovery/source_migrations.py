from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

from .models import CompanySource, JobSourceType


_FIRST_TECH_TTC_URL = "https://firsttechfedcareers.ttcportals.com/search/jobs"


def migrate_known_company_source(source: CompanySource) -> CompanySource:
    """Return the current public ATS configuration for known retired portals.

    ATS vendors and hosted career-site URLs can change while a saved company
    source remains in DynamoDB. Keep migrations deliberately narrow so an
    unrelated source is never rewritten merely because it uses the same ATS.
    """

    if source.source_type is not JobSourceType.JOBVITE:
        return source

    parsed = urlsplit(source.careers_url)
    host = (parsed.hostname or "").casefold()
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    if host != "jobs.jobvite.com" or not segments or segments[0] != "firsttechfed":
        return source

    return replace(
        source,
        source_type=JobSourceType.TALEMETRY_TTC,
        careers_url=_FIRST_TECH_TTC_URL,
        source_identifier="",
    )
