from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType
from job_discovery.sources.avature import AvatureJobSource, parse_avature_careers_url
from job_discovery.sources.base import HttpResponse
from job_discovery.sources.dayforce import DayforceJobSource, parse_dayforce_careers_url
from job_discovery.sources.eightfold import EightfoldJobSource, parse_eightfold_careers_url
from job_discovery.sources.smartrecruiters import (
    SmartRecruitersJobSource,
    _detail_api_url,
    _listing_api_url,
    parse_smartrecruiters_careers_url,
)
from job_discovery.sources.taleo import TaleoJobSource, parse_taleo_careers_url
from job_discovery.sources.talemetry_ttc import (
    TalemetryTtcJobSource,
    parse_talemetry_ttc_careers_url,
)
from job_discovery.service import default_adapters
from job_discovery.public_catalog import public_source_key


@dataclass
class StubHttpClient:
    responses: dict[str, HttpResponse]

    def __post_init__(self) -> None:
        self.calls: list[str] = []

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
            raise AssertionError("stub response exceeds max_bytes")
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
        raise AssertionError("These connectors should not POST")


def response(url: str, payload, *, content_type: str = "application/json") -> HttpResponse:
    if isinstance(payload, bytes):
        body = payload
    elif isinstance(payload, str):
        body = payload.encode("utf-8")
    else:
        body = json.dumps(payload).encode("utf-8")
    return HttpResponse(200, {"content-type": content_type}, body, url)


def robots(url: str) -> HttpResponse:
    return response(url, "User-agent: *\nAllow: /\n", content_type="text/plain")


def job_jsonld(*, title: str, identifier: str, url: str, location: str = "Portland, Oregon") -> str:
    description = " ".join(
        ["Build secure data platforms with Python SQL cloud services and governance."] * 14
    )
    return f'''<html><body><script type="application/ld+json">{{
      "@context":"https://schema.org",
      "@type":"JobPosting",
      "identifier":{{"value":"{identifier}"}},
      "title":"{title}",
      "description":"<p>{description}</p>",
      "employmentType":"FULL_TIME",
      "jobLocationType":"HYBRID",
      "jobLocation":{{"address":{{"addressLocality":"Portland","addressRegion":"Oregon","addressCountry":"US"}}}},
      "datePosted":"2026-07-30",
      "url":"{url}"
    }}</script></body></html>'''


class AdditionalSourceRegistrationTests(unittest.TestCase):
    def test_default_service_registers_every_new_adapter(self) -> None:
        adapters = default_adapters()
        for source_type in (
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
            JobSourceType.BRANDED_REQUISITION,
        ):
            self.assertIn(source_type, adapters)

    def test_talemetry_rejects_non_ttc_portal_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "ttcportals.com"):
            CompanySource(
                id="not-ttc",
                owner_id="owner-1",
                company_name="Example",
                careers_url="https://example.com/search/jobs",
                source_type=JobSourceType.TALEMETRY_TTC,
                source_identifier="",
            )

    def test_eightfold_domain_query_is_part_of_shared_catalog_identity(self) -> None:
        first = CompanySource(
            id="eightfold-one",
            owner_id="owner-1",
            company_name="One",
            careers_url="https://app.eightfold.ai/careers?domain=one.example&start=0",
            source_type=JobSourceType.EIGHTFOLD,
            source_identifier="",
        )
        second = CompanySource(
            id="eightfold-two",
            owner_id="owner-1",
            company_name="Two",
            careers_url="https://app.eightfold.ai/careers?domain=two.example&start=0",
            source_type=JobSourceType.EIGHTFOLD,
            source_identifier="",
        )
        self.assertNotEqual(public_source_key(first), public_source_key(second))

    def test_eightfold_vanity_domain_is_accepted_and_canonicalized(self) -> None:
        source = CompanySource(
            id="costco-eightfold",
            owner_id="owner-1",
            company_name="Costco Wholesale",
            careers_url="https://careers.costco.com/jobs/2244?lang=en-US",
            source_type=JobSourceType.EIGHTFOLD,
            source_identifier="",
        )

        target = parse_eightfold_careers_url(source.careers_url)

        self.assertEqual("https://careers.costco.com/jobs?lang=en-US", target.listing_url)
        self.assertEqual(("careers.costco.com",), target.allowed_domains)

    def test_eightfold_vanity_domain_requires_a_job_catalog_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "/jobs or /careers"):
            CompanySource(
                id="invalid-eightfold-vanity",
                owner_id="owner-1",
                company_name="Example",
                careers_url="https://careers.example.com/",
                source_type=JobSourceType.EIGHTFOLD,
                source_identifier="",
            )

    def test_eightfold_vanity_listing_and_detail_share_catalog_identity(self) -> None:
        listing = CompanySource(
            id="costco-listing",
            owner_id="owner-1",
            company_name="Costco Wholesale",
            careers_url="https://careers.costco.com/jobs?lang=en-US",
            source_type=JobSourceType.EIGHTFOLD,
            source_identifier="",
        )
        detail = CompanySource(
            id="costco-detail",
            owner_id="owner-1",
            company_name="Costco Wholesale",
            careers_url="https://careers.costco.com/jobs/2244?lang=en-US",
            source_type=JobSourceType.EIGHTFOLD,
            source_identifier="",
        )
        self.assertEqual(public_source_key(listing), public_source_key(detail))


class SmartRecruitersSourceTests(unittest.TestCase):
    def test_normalizes_public_career_and_job_urls(self) -> None:
        self.assertEqual(
            "ServiceNow",
            parse_smartrecruiters_careers_url(
                "https://careers.smartrecruiters.com/ServiceNow/?search=AI"
            ).company_identifier,
        )
        self.assertEqual(
            "ServiceNow",
            parse_smartrecruiters_careers_url(
                "https://jobs.smartrecruiters.com/ServiceNow/744000012345678-engineer"
            ).company_identifier,
        )

    def test_fetches_listing_and_full_detail_from_public_api(self) -> None:
        identifier = "ExampleBank"
        listing = _listing_api_url(identifier, 0, 100)
        detail = _detail_api_url(identifier, "42")
        http = StubHttpClient(
            {
                listing: response(
                    listing,
                    {
                        "totalFound": 1,
                        "content": [
                            {
                                "id": "42",
                                "name": "Senior Data Engineer",
                                "location": {
                                    "city": "Portland",
                                    "region": "Oregon",
                                    "country": "United States",
                                },
                                "releasedDate": "2026-07-30T10:00:00Z",
                                "typeOfEmployment": {"label": "Full-time"},
                                "ref": detail,
                            }
                        ],
                    },
                ),
                detail: response(
                    detail,
                    {
                        "id": "42",
                        "name": "Senior Data Engineer",
                        "jobAdUrl": "https://jobs.smartrecruiters.com/ExampleBank/42-senior-data-engineer",
                        "applyUrl": "https://jobs.smartrecruiters.com/ExampleBank/42-senior-data-engineer?oga=true",
                        "location": {
                            "city": "Portland",
                            "region": "Oregon",
                            "country": "United States",
                        },
                        "jobAd": {
                            "sections": {
                                "jobDescription": {
                                    "text": "<p>Build governed banking data products.</p>"
                                },
                                "qualifications": {
                                    "text": "<p>Python and SQL experience.</p>"
                                },
                            }
                        },
                    },
                ),
            }
        )
        source = CompanySource(
            id="example-smartrecruiters",
            owner_id="owner-1",
            company_name="Example Bank",
            careers_url="https://careers.smartrecruiters.com/ExampleBank",
            source_type=JobSourceType.SMARTRECRUITERS,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )

        jobs = SmartRecruitersJobSource(http).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual("Senior Data Engineer", jobs[0].title)
        self.assertIn("Python and SQL", jobs[0].description)
        self.assertEqual("Portland, Oregon, United States", jobs[0].location)


class AvatureSourceTests(unittest.TestCase):
    def test_feed_and_detail_are_supported(self) -> None:
        target = parse_avature_careers_url(
            "https://example.avature.net/en_US/careers/SearchJobs"
        )
        detail_url = "https://example.avature.net/en_US/careers/JobDetail/Data-Engineer/42"
        robots_url = "https://example.avature.net/robots.txt"
        feed = target.feed_url
        feed_xml = f'''<?xml version="1.0"?><rss><channel><item>
          <title>Data Engineer</title><link>{detail_url}</link>
          <description>Build data systems.</description>
          <pubDate>Thu, 30 Jul 2026 10:00:00 +0000</pubDate>
        </item></channel></rss>'''
        http = StubHttpClient(
            {
                robots_url: robots(robots_url),
                feed: response(feed, feed_xml, content_type="application/rss+xml"),
                detail_url: response(
                    detail_url,
                    job_jsonld(title="Data Engineer", identifier="42", url=detail_url),
                    content_type="text/html",
                ),
            }
        )
        source = CompanySource(
            id="example-avature",
            owner_id="owner-1",
            company_name="Example",
            careers_url=target.listing_url,
            source_type=JobSourceType.AVATURE,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )

        jobs = AvatureJobSource(http).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual("Data Engineer", jobs[0].title)
        self.assertGreater(len(jobs[0].description), 500)


class PublicPortalSourceTests(unittest.TestCase):
    def test_eightfold_embedded_job_and_detail(self) -> None:
        target = parse_eightfold_careers_url(
            "https://app.eightfold.ai/careers?domain=example.com&start=20&pid=42"
        )
        self.assertNotIn("pid=", target.listing_url)
        detail_url = "https://app.eightfold.ai/careers/job/data-engineer/42?domain=example.com"
        robots_url = "https://app.eightfold.ai/robots.txt"
        listing_html = f'''<html><body><script type="application/json">{{
          "positions":[{{"positionId":"42","title":"Data Engineer",
          "location":"Portland, Oregon","url":"{detail_url}"}}]
        }}</script></body></html>'''
        http = StubHttpClient(
            {
                robots_url: robots(robots_url),
                target.listing_url: response(target.listing_url, listing_html, content_type="text/html"),
                detail_url: response(
                    detail_url,
                    job_jsonld(title="Data Engineer", identifier="42", url=detail_url),
                    content_type="text/html",
                ),
            }
        )
        source = CompanySource(
            id="example-eightfold",
            owner_id="owner-1",
            company_name="Example",
            careers_url=target.listing_url,
            source_type=JobSourceType.EIGHTFOLD,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )
        jobs = EightfoldJobSource(http).fetch_jobs(source)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Data Engineer", jobs[0].title)

    def test_eightfold_vanity_domain_embedded_job_and_detail(self) -> None:
        target = parse_eightfold_careers_url(
            "https://careers.costco.com/jobs/locations?lang=en-US"
        )
        detail_url = "https://careers.costco.com/jobs/2244?lang=en-US"
        robots_url = "https://careers.costco.com/robots.txt"
        listing_html = f'''<html><body><script type="application/json">{{
          "positions":[{{"positionId":"2244","title":"Membership Clerk",
          "location":"Tukwila, Washington","url":"{detail_url}"}}]
        }}</script></body></html>'''
        http = StubHttpClient(
            {
                robots_url: robots(robots_url),
                target.listing_url: response(
                    target.listing_url, listing_html, content_type="text/html"
                ),
                detail_url: response(
                    detail_url,
                    job_jsonld(
                        title="Membership Clerk", identifier="2244", url=detail_url
                    ),
                    content_type="text/html",
                ),
            }
        )
        source = CompanySource(
            id="costco-eightfold",
            owner_id="owner-1",
            company_name="Costco Wholesale",
            careers_url=target.listing_url,
            source_type=JobSourceType.EIGHTFOLD,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )

        jobs = EightfoldJobSource(http).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual("Membership Clerk", jobs[0].title)
        self.assertEqual("2244", jobs[0].external_job_id)
        self.assertEqual(detail_url, jobs[0].canonical_url)
        self.assertGreater(len(jobs[0].description), 500)

    def test_eightfold_vanity_domain_builds_detail_url_when_embedded_url_is_missing(self) -> None:
        target = parse_eightfold_careers_url("https://careers.costco.com/jobs")
        detail_url = "https://careers.costco.com/jobs/2244"
        robots_url = "https://careers.costco.com/robots.txt"
        listing_html = '''<html><body><script type="application/json">{
          "positions":[{"positionId":"2244","title":"Membership Clerk",
          "location":"Tukwila, Washington"}]
        }</script></body></html>'''
        http = StubHttpClient(
            {
                robots_url: robots(robots_url),
                target.listing_url: response(
                    target.listing_url, listing_html, content_type="text/html"
                ),
                detail_url: response(
                    detail_url,
                    job_jsonld(
                        title="Membership Clerk", identifier="2244", url=detail_url
                    ),
                    content_type="text/html",
                ),
            }
        )
        source = CompanySource(
            id="costco-eightfold",
            owner_id="owner-1",
            company_name="Costco Wholesale",
            careers_url=target.listing_url,
            source_type=JobSourceType.EIGHTFOLD,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )

        jobs = EightfoldJobSource(http).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual(detail_url, jobs[0].canonical_url)

    def test_taleo_job_links_and_detail(self) -> None:
        target = parse_taleo_careers_url(
            "https://example.taleo.net/careersection/external/jobdetail.ftl?job=42"
        )
        listing = target.listing_url
        detail_url = "https://example.taleo.net/careersection/external/jobdetail.ftl?job=42"
        robots_url = "https://example.taleo.net/robots.txt"
        listing_html = f'''<html><body><div class="job-result">
          <a href="{detail_url}">Senior Auditor</a><span>Location: Portland, Oregon</span>
        </div></body></html>'''
        http = StubHttpClient(
            {
                robots_url: robots(robots_url),
                listing: response(listing, listing_html, content_type="text/html"),
                detail_url: response(
                    detail_url,
                    job_jsonld(title="Senior Auditor", identifier="42", url=detail_url),
                    content_type="text/html",
                ),
            }
        )
        source = CompanySource(
            id="example-taleo",
            owner_id="owner-1",
            company_name="Example",
            careers_url=listing,
            source_type=JobSourceType.TALEO,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )
        jobs = TaleoJobSource(http).fetch_jobs(source)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Senior Auditor", jobs[0].title)

    def test_dayforce_embedded_jsonld_listing(self) -> None:
        target = parse_dayforce_careers_url(
            "https://jobs.dayforcehcm.com/en-US/example/CAREERS"
        )
        listing = target.listing_url
        detail_url = "https://jobs.dayforcehcm.com/en-US/example/CAREERS/jobs/42"
        robots_url = "https://jobs.dayforcehcm.com/robots.txt"
        listing_html = job_jsonld(
            title="Platform Engineer", identifier="42", url=detail_url
        )
        http = StubHttpClient(
            {
                robots_url: robots(robots_url),
                listing: response(listing, listing_html, content_type="text/html"),
                detail_url: response(
                    detail_url,
                    listing_html,
                    content_type="text/html",
                ),
            }
        )
        source = CompanySource(
            id="example-dayforce",
            owner_id="owner-1",
            company_name="Example",
            careers_url=listing,
            source_type=JobSourceType.DAYFORCE,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )
        jobs = DayforceJobSource(http).fetch_jobs(source)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Platform Engineer", jobs[0].title)

    def test_talemetry_ttc_json_listing_normalization_and_job_detail(self) -> None:
        target = parse_talemetry_ttc_careers_url(
            "https://examplecareers.ttcportals.com/jobs/17599619-platform-engineer?source=linkedin"
        )
        self.assertEqual(
            "https://examplecareers.ttcportals.com/search/jobs", target.listing_url
        )
        feed_page_one = (
            "https://examplecareers.ttcportals.com/search/jobs.json?page=1"
        )
        feed_page_two = (
            "https://examplecareers.ttcportals.com/search/jobs.json?page=2"
        )
        detail_url = (
            "https://examplecareers.ttcportals.com/"
            "jobs/17599619-platform-engineer"
        )
        robots_url = "https://examplecareers.ttcportals.com/robots.txt"
        detail_html = job_jsonld(
            title="Platform Engineer", identifier="17599619", url=detail_url
        )
        http = StubHttpClient(
            {
                robots_url: robots(robots_url),
                feed_page_one: response(
                    feed_page_one,
                    {
                        "current_page": 1,
                        "per_page": 1,
                        "total_entries": 2,
                        "entries": [
                            {
                                "id": 987,
                                "talemetry_job_id": "17599619",
                                "title": "Platform Engineer",
                                "location": "Hillsboro, Oregon",
                                "permalink": "/jobs/17599619-platform-engineer",
                                "date_posted": "2026-07-30",
                            }
                        ],
                    },
                ),
                feed_page_two: response(
                    feed_page_two,
                    {
                        "current_page": 2,
                        "per_page": 1,
                        "total_entries": 2,
                        "entries": [],
                    },
                ),
                detail_url: response(
                    detail_url, detail_html, content_type="text/html"
                ),
            }
        )
        source = CompanySource(
            id="example-talemetry",
            owner_id="owner-1",
            company_name="Example",
            careers_url=target.listing_url,
            source_type=JobSourceType.TALEMETRY_TTC,
            source_identifier="",
            filters={
                "min_request_interval_seconds": 0,
                "detail_fetch_limit": 1,
            },
        )

        jobs = TalemetryTtcJobSource(http).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual("Platform Engineer", jobs[0].title)
        self.assertEqual("17599619", jobs[0].external_job_id)
        self.assertEqual("Portland, Oregon, US", jobs[0].location)
        self.assertGreater(len(jobs[0].description), 500)
        self.assertEqual("talemetry_json", jobs[0].metadata["listing_source"])
        self.assertIn(feed_page_two, http.calls)


if __name__ == "__main__":
    unittest.main()
