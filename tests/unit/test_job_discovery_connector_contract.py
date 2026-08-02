from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit

from job_discovery.models import CompanySource, DiscoveredJob, JobSourceType, WorkplaceType
from job_discovery.sources.ashby import AshbyJobSource
from job_discovery.sources.base import HttpResponse, JobSource
from job_discovery.sources.generic_jsonld import GenericJsonLdJobSource
from job_discovery.sources.greenhouse import GreenhouseJobSource
from job_discovery.sources.icims import IcmsJobSource
from job_discovery.sources.lever import LeverJobSource
from job_discovery.sources.oracle_cloud_hcm import (
    OracleCloudHcmJobSource,
    _api_detail_url,
    _api_listing_url,
    parse_oracle_cloud_hcm_careers_url,
)
from job_discovery.sources.workday import WorkdayJobSource
from job_discovery.sources.successfactors import SuccessFactorsJobSource
from job_discovery.storage import InMemoryTTLCache

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "job_discovery"


@dataclass
class FixtureHttpClient:
    responses: dict[str, HttpResponse]

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.post_calls: list[tuple[str, bytes]] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        max_bytes: int | None = None,
        max_redirects: int = 3,
        allowed_domains: Sequence[str] = (),
    ) -> HttpResponse:
        self.calls.append(url)
        response = self.responses[url]
        if max_bytes is not None and len(response.body) > max_bytes:
            raise AssertionError("fixture exceeds configured response limit")
        return response

    def post(
        self,
        url: str,
        *,
        body: bytes,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        max_bytes: int | None = None,
        max_redirects: int = 3,
        allowed_domains: Sequence[str] = (),
    ) -> HttpResponse:
        self.calls.append(url)
        self.post_calls.append((url, body))
        response = self.responses[url]
        if max_bytes is not None and len(response.body) > max_bytes:
            raise AssertionError("fixture exceeds configured response limit")
        return response


def _response(url: str, body: bytes, content_type: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={"content-type": content_type},
        body=body,
        url=url,
    )


def _json_response(url: str, filename: str) -> HttpResponse:
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    return _response(url, json.dumps(payload).encode("utf-8"), "application/json")


class JobSourceConnectorContractTests(unittest.TestCase):
    """Run the same public connector contract against every supported adapter."""

    def _cases(self) -> list[tuple[str, JobSource, CompanySource]]:
        greenhouse_url = (
            "https://boards-api.greenhouse.io/v1/boards/examplebank/jobs?content=true"
        )
        greenhouse_http = FixtureHttpClient(
            {greenhouse_url: _json_response(greenhouse_url, "greenhouse.json")}
        )
        lever_url = "https://api.lever.co/v0/postings/examplebank?mode=json"
        lever_http = FixtureHttpClient(
            {lever_url: _json_response(lever_url, "lever.json")}
        )
        ashby_url = (
            "https://api.ashbyhq.com/posting-api/job-board/examplebank"
            "?includeCompensation=true"
        )
        ashby_http = FixtureHttpClient(
            {ashby_url: _json_response(ashby_url, "ashby.json")}
        )

        workday_listing_url = (
            "https://examplebank.wd5.myworkdayjobs.com/"
            "wday/cxs/examplebank/External/jobs"
        )
        workday_external_path = "/job/Portland/Senior-Data-Platform-Engineer_REQ-42"
        workday_detail_url = (
            "https://examplebank.wd5.myworkdayjobs.com/"
            "wday/cxs/examplebank/External" + workday_external_path
        )
        workday_http = FixtureHttpClient(
            {
                workday_listing_url: _response(
                    workday_listing_url,
                    json.dumps(
                        {
                            "total": 1,
                            "jobPostings": [
                                {
                                    "title": "Senior Data Platform Engineer",
                                    "externalPath": workday_external_path,
                                    "locationsText": "Portland, Oregon",
                                    "postedOn": "Posted Today",
                                    "bulletFields": ["REQ-42"],
                                }
                            ],
                        }
                    ).encode("utf-8"),
                    "application/json",
                ),
                workday_detail_url: _response(
                    workday_detail_url,
                    json.dumps(
                        {
                            "jobPostingInfo": {
                                "title": "Senior Data Platform Engineer",
                                "jobReqId": "REQ-42",
                                "jobDescription": "<p>Build regulated data platforms.</p>",
                                "location": "Portland, Oregon",
                                "timeType": "Full time",
                                "remoteType": "Hybrid",
                                "startDate": "2026-07-30",
                                "canApply": True,
                            }
                        }
                    ).encode("utf-8"),
                    "application/json",
                ),
            }
        )

        successfactors_root = "https://examplebank.jobs.hr.cloud.sap/"
        successfactors_search = "https://examplebank.jobs.hr.cloud.sap/search/"
        successfactors_robots = "https://examplebank.jobs.hr.cloud.sap/robots.txt"
        successfactors_job = (
            "https://examplebank.jobs.hr.cloud.sap/job/Portland/"
            "Senior-Data-Platform-Engineer/42-en_US"
        )
        successfactors_http = FixtureHttpClient(
            {
                successfactors_robots: _response(
                    successfactors_robots, b"User-agent: *\nAllow: /\n", "text/plain"
                ),
                successfactors_search: _response(
                    successfactors_search,
                    f"""<html><body><p>Results 1 - 1 of 1</p><table><tr>
                    <td><a href=\"{successfactors_job}/\">Senior Data Platform Engineer</a></td>
                    <td>Portland, Oregon</td></tr></table></body></html>""".encode("utf-8"),
                    "text/html",
                ),
                successfactors_job: _response(
                    successfactors_job,
                    b"""<script type=\"application/ld+json\">{
                    \"@type\":\"JobPosting\",
                    \"identifier\":{\"value\":\"REQ-42\"},
                    \"title\":\"Senior Data Platform Engineer\",
                    \"description\":\"<p>Build regulated data platforms.</p>\",
                    \"employmentType\":\"FULL_TIME\",
                    \"jobLocationType\":\"HYBRID\",
                    \"jobLocation\":{\"address\":{\"addressLocality\":\"Portland\",\"addressRegion\":\"Oregon\"}},
                    \"url\":\"https://examplebank.jobs.hr.cloud.sap/job/Portland/Senior-Data-Platform-Engineer/42-en_US\"
                    }</script>""",
                    "text/html",
                ),
            }
        )

        oracle_root = "https://examplebank.fa.us2.oraclecloud.com"
        oracle_listing = (
            oracle_root + "/hcmUI/CandidateExperience/en/sites/CX_1/jobs"
        )
        oracle_target = parse_oracle_cloud_hcm_careers_url(oracle_listing)
        oracle_api_listing = _api_listing_url(oracle_target, limit=24, offset=0)
        oracle_api_detail = _api_detail_url(oracle_target, "REQ-42")
        oracle_robots = oracle_root + "/robots.txt"
        oracle_http = FixtureHttpClient(
            {
                oracle_robots: _response(
                    oracle_robots, b"User-agent: *\nAllow: /\n", "text/plain"
                ),
                oracle_api_listing: _response(
                    oracle_api_listing,
                    json.dumps(
                        {
                            "items": [
                                {
                                    "requisitionList": [
                                        {
                                            "Id": "REQ-42",
                                            "Title": "Senior Data Platform Engineer",
                                            "PrimaryLocation": "Portland, Oregon",
                                            "JobType": "Full Time",
                                            "WorkplaceType": "Hybrid",
                                        }
                                    ]
                                }
                            ],
                            "hasMore": False,
                        }
                    ).encode("utf-8"),
                    "application/vnd.oracle.adf.resourcecollection+json",
                ),
                oracle_api_detail: _response(
                    oracle_api_detail,
                    json.dumps(
                        {
                            "items": [
                                {
                                    "Id": "REQ-42",
                                    "Title": "Senior Data Platform Engineer",
                                    "ExternalDescriptionStr": (
                                        "<p>Build regulated data platforms.</p>"
                                    ),
                                    "PrimaryLocation": "Portland, Oregon",
                                    "JobType": "Full Time",
                                    "WorkplaceType": "Hybrid",
                                }
                            ]
                        }
                    ).encode("utf-8"),
                    "application/vnd.oracle.adf.resourcecollection+json",
                ),
            }
        )

        icims_root = "https://careers-examplebank.icims.com"
        icims_listing = icims_root + "/jobs/search"
        icims_job = icims_root + "/jobs/47190/senior-data-platform-engineer/job"
        icims_robots = icims_root + "/robots.txt"
        icims_http = FixtureHttpClient(
            {
                icims_robots: _response(
                    icims_robots, b"User-agent: *\nAllow: /\n", "text/plain"
                ),
                icims_listing: _response(
                    icims_listing,
                    f"""<html><body><article class="iCIMS_JobsTable">
                    <div>Position Type Regular Full-Time</div>
                    <div>Requisition ID REQ-42</div>
                    <a href="{icims_job}">Senior Data Platform Engineer</a>
                    <div>Job Locations Portland, Oregon Hybrid</div>
                    </article></body></html>""".encode("utf-8"),
                    "text/html",
                ),
                icims_job: _response(
                    icims_job,
                    f"""<script type="application/ld+json">{{
                    "@type":"JobPosting",
                    "identifier":{{"value":"REQ-42"}},
                    "title":"Senior Data Platform Engineer",
                    "description":"<p>Build regulated data platforms.</p>",
                    "employmentType":"FULL_TIME",
                    "jobLocationType":"HYBRID",
                    "jobLocation":{{"address":{{"addressLocality":"Portland","addressRegion":"Oregon"}}}},
                    "url":"{icims_job}"
                    }}</script>""".encode("utf-8"),
                    "text/html",
                ),
            }
        )

        generic_index = "https://careers.example.com/jobs"
        generic_detail = "https://careers.example.com/jobs/data-platform-engineer"
        generic_robots = "https://careers.example.com/robots.txt"
        generic_http = FixtureHttpClient(
            {
                generic_robots: _response(
                    generic_robots,
                    (FIXTURES / "robots.txt").read_bytes(),
                    "text/plain; charset=utf-8",
                ),
                generic_index: _response(
                    generic_index,
                    (FIXTURES / "generic_index.html").read_bytes(),
                    "text/html; charset=utf-8",
                ),
                generic_detail: _response(
                    generic_detail,
                    (FIXTURES / "generic_job.html").read_bytes(),
                    "text/html; charset=utf-8",
                ),
            }
        )

        common = {
            "owner_id": "owner-contract",
            "company_name": "Example Bank",
            "filters": {"min_request_interval_seconds": 0},
        }
        return [
            (
                "greenhouse",
                GreenhouseJobSource(greenhouse_http),
                CompanySource(
                    id="source-greenhouse",
                    careers_url="https://boards.greenhouse.io/examplebank",
                    source_type=JobSourceType.GREENHOUSE,
                    source_identifier="examplebank",
                    **common,
                ),
            ),
            (
                "lever",
                LeverJobSource(lever_http),
                CompanySource(
                    id="source-lever",
                    careers_url="https://jobs.lever.co/examplebank",
                    source_type=JobSourceType.LEVER,
                    source_identifier="examplebank",
                    **common,
                ),
            ),
            (
                "ashby",
                AshbyJobSource(ashby_http),
                CompanySource(
                    id="source-ashby",
                    careers_url="https://jobs.ashbyhq.com/examplebank",
                    source_type=JobSourceType.ASHBY,
                    source_identifier="examplebank",
                    filters={
                        "min_request_interval_seconds": 0,
                        "include_compensation": True,
                    },
                    owner_id=common["owner_id"],
                    company_name=common["company_name"],
                ),
            ),
            (
                "workday",
                WorkdayJobSource(workday_http),
                CompanySource(
                    id="source-workday",
                    careers_url="https://examplebank.wd5.myworkdayjobs.com/en-US/External",
                    source_type=JobSourceType.WORKDAY,
                    source_identifier="External",
                    filters={"min_request_interval_seconds": 0},
                    owner_id=common["owner_id"],
                    company_name=common["company_name"],
                ),
            ),
            (
                "successfactors",
                SuccessFactorsJobSource(successfactors_http),
                CompanySource(
                    id="source-successfactors",
                    careers_url=successfactors_root,
                    source_type=JobSourceType.SUCCESSFACTORS,
                    source_identifier="",
                    filters={"min_request_interval_seconds": 0},
                    owner_id=common["owner_id"],
                    company_name=common["company_name"],
                ),
            ),
            (
                "oracle_cloud_hcm",
                OracleCloudHcmJobSource(oracle_http),
                CompanySource(
                    id="source-oracle-cloud-hcm",
                    careers_url=oracle_listing,
                    source_type=JobSourceType.ORACLE_CLOUD_HCM,
                    source_identifier="",
                    filters={"min_request_interval_seconds": 0},
                    owner_id=common["owner_id"],
                    company_name=common["company_name"],
                ),
            ),
            (
                "icims",
                IcmsJobSource(icims_http),
                CompanySource(
                    id="source-icims",
                    careers_url=icims_listing,
                    source_type=JobSourceType.ICIMS,
                    source_identifier="",
                    filters={"min_request_interval_seconds": 0},
                    owner_id=common["owner_id"],
                    company_name=common["company_name"],
                ),
            ),
            (
                "generic_jsonld",
                GenericJsonLdJobSource(generic_http, cache=InMemoryTTLCache()),
                CompanySource(
                    id="source-jsonld",
                    careers_url=generic_index,
                    source_type=JobSourceType.GENERIC_JSONLD,
                    source_identifier="",
                    filters={"max_pages": 2, "min_request_interval_seconds": 0},
                    owner_id=common["owner_id"],
                    company_name=common["company_name"],
                ),
            ),
        ]

    def test_same_connector_contract_across_all_initial_sources(self) -> None:
        for name, adapter, source in self._cases():
            with self.subTest(source=name):
                self.assertIsInstance(adapter, JobSource)
                jobs = adapter.fetch_jobs(source)
                self.assertEqual(1, len(jobs))
                job = jobs[0]
                self.assertIsInstance(job, DiscoveredJob)
                self.assertEqual(source.owner_id, job.owner_id)
                self.assertEqual(source.id, job.source_id)
                self.assertEqual(source.source_type, job.source_type)
                self.assertEqual("Example Bank", job.company)
                self.assertEqual("Senior Data Platform Engineer", job.title)
                self.assertEqual("Full-time", job.employment_type)
                self.assertEqual(WorkplaceType.HYBRID, job.workplace_type)
                self.assertTrue(job.description)
                self.assertNotIn("<", job.description)
                self.assertTrue(job.description_fingerprint)
                self.assertTrue(job.external_job_id)
                parsed = urlsplit(job.canonical_url)
                self.assertIn(parsed.scheme, {"http", "https"})
                self.assertTrue(parsed.hostname)
                self.assertNotIn("utm_", job.canonical_url)
                self.assertTrue(job.active)

    def test_normalization_is_consistent_across_source_formats(self) -> None:
        jobs = [adapter.fetch_jobs(source)[0] for _, adapter, source in self._cases()]

        self.assertEqual({"Senior Data Platform Engineer"}, {job.title for job in jobs})
        self.assertEqual({"Example Bank"}, {job.company for job in jobs})
        self.assertEqual({"Full-time"}, {job.employment_type for job in jobs})
        self.assertEqual({WorkplaceType.HYBRID}, {job.workplace_type for job in jobs})
        self.assertTrue(all("regulated data platforms" in job.description for job in jobs))
        self.assertTrue(all("script" not in job.description.casefold() for job in jobs))


if __name__ == "__main__":
    unittest.main()
