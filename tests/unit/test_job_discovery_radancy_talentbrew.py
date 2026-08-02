from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType
from job_discovery.public_catalog import public_source_key
from job_discovery.service import default_adapters
from job_discovery.sources.base import HttpResponse
from job_discovery.sources.radancy_talentbrew import (
    RadancyTalentBrewJobSource,
    parse_radancy_talentbrew_careers_url,
)


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
        raise AssertionError("Radancy / TalentBrew discovery should not POST")


def response(url: str, payload: str, *, content_type: str = "text/html") -> HttpResponse:
    return HttpResponse(
        200,
        {"content-type": content_type},
        payload.encode("utf-8"),
        url,
    )


def job_jsonld(*, title: str, identifier: str, url: str, location: str) -> str:
    description = " ".join(
        [
            "Design, deliver, and operate reliable enterprise systems while partnering "
            "with engineering, security, product, and business stakeholders."
        ]
        * 18
    )
    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {"value": identifier},
        "title": title,
        "description": f"<p>{description}</p>",
        "employmentType": "FULL_TIME",
        "datePosted": "2026-07-30",
        "jobLocation": {
            "address": {
                "addressLocality": location,
                "addressRegion": "Washington",
                "addressCountry": "US",
            }
        },
        "url": url,
    }
    return (
        '<html><body><script type="application/ld+json">'
        + json.dumps(payload)
        + "</script></body></html>"
    )


class RadancyTalentBrewSourceTests(unittest.TestCase):
    def test_normalizes_root_search_filtered_and_job_urls(self) -> None:
        cases = {
            "https://jobs.boeing.com/": "https://jobs.boeing.com/search-jobs",
            "https://jobs.boeing.com/en/search-jobs?p=4": "https://jobs.boeing.com/en/search-jobs",
            "https://jobs.example.com/fr/": "https://jobs.example.com/fr/search-jobs",
            "https://www.commonspirit.careers/location/washington-jobs/35300/6252001-5815135/3": "https://www.commonspirit.careers/search-jobs",
            "https://www.disneycareers.com/en/job/glendale/product-manager/391/98584385040": "https://www.disneycareers.com/en/search-jobs",
            "https://parksjobs.disneycareers.com/category/culinary-jobs/1678/4760/1": "https://parksjobs.disneycareers.com/search-jobs",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    expected,
                    parse_radancy_talentbrew_careers_url(value).listing_url,
                )

    def test_accepts_public_employer_branded_hosts(self) -> None:
        for url in (
            "https://jobs.boeing.com/search-jobs",
            "https://www.disneycareers.com/en/search-jobs",
            "https://www.commonspirit.careers/search-jobs",
        ):
            with self.subTest(url=url):
                source = CompanySource(
                    id="radancy-source",
                    owner_id="owner-1",
                    company_name="Example",
                    careers_url=url,
                    source_type=JobSourceType.RADANCY_TALENTBREW,
                    source_identifier="",
                )
                self.assertEqual(JobSourceType.RADANCY_TALENTBREW, source.source_type)

    def test_fetches_paginated_jobs_and_full_descriptions(self) -> None:
        listing = "https://jobs.example.com/en/search-jobs"
        next_page = listing + "?p=2"
        detail_one = (
            "https://jobs.example.com/en/job/everett/lead-systems-engineer/185/97246600256"
        )
        detail_two = (
            "https://jobs.example.com/en/job/seattle/senior-data-engineer/185/97246600257"
        )
        robots_url = "https://jobs.example.com/robots.txt"
        listing_one = f'''<html><body>
          <ul class="job-results">
            <li class="job-result">
              <a href="{detail_one}">Lead Systems Engineer</a>
              <span>Location: Everett, Washington | 07/30/2026</span>
            </li>
          </ul>
          <a rel="next" href="{next_page}">Next</a>
        </body></html>'''
        listing_two = f'''<html><body>
          <div class="job-result">
            <a href="{detail_two}">Senior Data Engineer</a>
            <span>Location: Seattle, Washington | 07/29/2026</span>
          </div>
        </body></html>'''
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                listing: response(listing, listing_one),
                next_page: response(next_page, listing_two),
                detail_one: response(
                    detail_one,
                    job_jsonld(
                        title="Lead Systems Engineer",
                        identifier="JR-1001",
                        url=detail_one,
                        location="Everett",
                    ),
                ),
                detail_two: response(
                    detail_two,
                    job_jsonld(
                        title="Senior Data Engineer",
                        identifier="JR-1002",
                        url=detail_two,
                        location="Seattle",
                    ),
                ),
            }
        )
        source = CompanySource(
            id="example-radancy",
            owner_id="owner-1",
            company_name="Example Aerospace",
            careers_url=listing,
            source_type=JobSourceType.RADANCY_TALENTBREW,
            source_identifier="",
            filters={
                "min_request_interval_seconds": 0,
                "max_pages": 2,
                "detail_fetch_limit": 2,
            },
        )

        jobs = RadancyTalentBrewJobSource(http).fetch_jobs(source)

        self.assertEqual(2, len(jobs))
        self.assertEqual(
            ["Lead Systems Engineer", "Senior Data Engineer"],
            [job.title for job in jobs],
        )
        self.assertEqual(["JR-1001", "JR-1002"], [job.external_job_id for job in jobs])
        self.assertGreater(len(jobs[0].description), 500)
        self.assertEqual("Everett, Washington, US", jobs[0].location)
        self.assertEqual("Radancy / TalentBrew", jobs[0].metadata["portal_platform"])
        self.assertIn(next_page, http.calls)

    def test_equivalent_urls_share_public_catalog_identity(self) -> None:
        urls = (
            "https://jobs.boeing.com/en/search-jobs",
            "https://jobs.boeing.com/en/location/washington-jobs/185/6252001-5815135/3",
            "https://jobs.boeing.com/en/job/everett/systems-engineer/185/97246600256",
        )
        sources = [
            CompanySource(
                id=f"radancy-{index}",
                owner_id="owner-1",
                company_name="Boeing",
                careers_url=parse_radancy_talentbrew_careers_url(url).listing_url,
                source_type=JobSourceType.RADANCY_TALENTBREW,
                source_identifier="",
            )
            for index, url in enumerate(urls)
        ]
        self.assertEqual(1, len({public_source_key(source) for source in sources}))

    def test_default_service_registers_radancy_adapter(self) -> None:
        self.assertIn(JobSourceType.RADANCY_TALENTBREW, default_adapters())


if __name__ == "__main__":
    unittest.main()
