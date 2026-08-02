from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType
from job_discovery.public_catalog import public_source_key
from job_discovery.service import default_adapters
from job_discovery.sources.amazon_jobs import (
    AmazonJobsJobSource,
    parse_amazon_jobs_careers_url,
)
from job_discovery.sources.base import HttpResponse


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
        self.assert_allowed(url, allowed_domains)
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
        raise AssertionError("Amazon Jobs discovery should not POST")

    @staticmethod
    def assert_allowed(url: str, allowed_domains: Sequence[str]) -> None:
        if allowed_domains != ("www.amazon.jobs",):
            raise AssertionError(f"unexpected allowed domains: {allowed_domains!r}")
        if not url.startswith("https://www.amazon.jobs/"):
            raise AssertionError(f"unexpected Amazon Jobs URL: {url}")


def response(url: str, payload: str, *, content_type: str = "text/html") -> HttpResponse:
    return HttpResponse(
        200,
        {"content-type": content_type},
        payload.encode("utf-8"),
        url,
    )


def job_jsonld(*, title: str, identifier: str, url: str, city: str) -> str:
    description = " ".join(
        [
            "Build, deliver, and operate customer-focused technology while partnering "
            "with engineering, product, security, and business stakeholders."
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
                "addressLocality": city,
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


class AmazonJobsSourceTests(unittest.TestCase):
    def test_normalizes_root_search_filters_and_job_urls(self) -> None:
        cases = {
            "https://amazon.jobs/": "https://www.amazon.jobs/en/search",
            "https://www.amazon.jobs/en/search?country=USA&offset=20&sort=relevant": (
                "https://www.amazon.jobs/en/search?country=USA"
            ),
            "https://www.amazon.jobs/fr-ca/jobs/10419273/product-manager": (
                "https://www.amazon.jobs/fr-ca/search"
            ),
            "https://www.amazon.jobs/en/search?region=Washington&country=USA": (
                "https://www.amazon.jobs/en/search?country=USA&region=Washington"
            ),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    expected,
                    parse_amazon_jobs_careers_url(value).listing_url,
                )

    def test_rejects_non_amazon_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "official amazon.jobs"):
            CompanySource(
                id="fake-amazon",
                owner_id="owner-1",
                company_name="Amazon",
                careers_url="https://amazon.example/jobs",
                source_type=JobSourceType.AMAZON_JOBS,
                source_identifier="",
            )
        with self.assertRaisesRegex(ValueError, "official amazon.jobs"):
            parse_amazon_jobs_careers_url("https://amazon.example/jobs")

    def test_fetches_bounded_offset_pages_and_job_details(self) -> None:
        first_page = (
            "https://www.amazon.jobs/en/search?country=USA&offset=0&result_limit=2&sort=recent"
        )
        second_page = (
            "https://www.amazon.jobs/en/search?country=USA&offset=2&result_limit=2&sort=recent"
        )
        robots = "https://www.amazon.jobs/robots.txt"
        details = [
            "https://www.amazon.jobs/en/jobs/10419273/principal-product-manager",
            "https://www.amazon.jobs/en/jobs/10419274/senior-data-engineer",
            "https://www.amazon.jobs/en/jobs/10419275/security-engineer",
        ]
        listing_one = f'''<html><body>
          <div class="job-result"><a href="{details[0]}">Principal Product Manager</a><span>Seattle, WA, USA | Job ID: 10419273</span></div>
          <div class="job-result"><a href="{details[1]}">Senior Data Engineer</a><span>Bellevue, WA, USA | Job ID: 10419274</span></div>
        </body></html>'''
        listing_two = f'''<html><body>
          <div class="job-result"><a href="{details[2]}">Security Engineer</a><span>Redmond, WA, USA | Job ID: 10419275</span></div>
        </body></html>'''
        http = StubHttpClient(
            {
                robots: response(
                    robots,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                first_page: response(first_page, listing_one),
                second_page: response(second_page, listing_two),
                details[0]: response(
                    details[0],
                    job_jsonld(
                        title="Principal Product Manager",
                        identifier="10419273",
                        url=details[0],
                        city="Seattle",
                    ),
                ),
                details[1]: response(
                    details[1],
                    job_jsonld(
                        title="Senior Data Engineer",
                        identifier="10419274",
                        url=details[1],
                        city="Bellevue",
                    ),
                ),
                details[2]: response(
                    details[2],
                    job_jsonld(
                        title="Security Engineer",
                        identifier="10419275",
                        url=details[2],
                        city="Redmond",
                    ),
                ),
            }
        )
        source = CompanySource(
            id="amazon-public",
            owner_id="owner-1",
            company_name="Amazon",
            careers_url="https://www.amazon.jobs/en/search?country=USA",
            source_type=JobSourceType.AMAZON_JOBS,
            source_identifier="",
            filters={
                "min_request_interval_seconds": 0,
                "max_pages": 3,
                "max_jobs": 10,
                "amazon_page_size": 2,
                "detail_fetch_limit": 3,
            },
        )

        jobs = AmazonJobsJobSource(http).fetch_jobs(source)

        self.assertEqual(3, len(jobs))
        self.assertEqual(
            ["10419273", "10419274", "10419275"],
            [job.external_job_id for job in jobs],
        )
        self.assertEqual(
            ["Seattle, Washington, US", "Bellevue, Washington, US", "Redmond, Washington, US"],
            [job.location for job in jobs],
        )
        self.assertTrue(all(len(job.description) > 500 for job in jobs))
        self.assertTrue(all(job.metadata["portal_platform"] == "Amazon Jobs" for job in jobs))
        self.assertIn(second_page, http.calls)

    def test_equivalent_search_and_job_urls_share_catalog_identity(self) -> None:
        urls = (
            "https://amazon.jobs/en",
            "https://www.amazon.jobs/en/search?country=USA&offset=20",
            "https://www.amazon.jobs/en/jobs/10419273/principal-product-manager?country=USA",
        )
        normalized = [
            parse_amazon_jobs_careers_url(url).listing_url for url in urls
        ]
        self.assertEqual(
            [
                "https://www.amazon.jobs/en/search",
                "https://www.amazon.jobs/en/search?country=USA",
                "https://www.amazon.jobs/en/search?country=USA",
            ],
            normalized,
        )
        filtered_sources = [
            CompanySource(
                id=f"amazon-{index}",
                owner_id="owner-1",
                company_name="Amazon",
                careers_url=url,
                source_type=JobSourceType.AMAZON_JOBS,
                source_identifier="",
            )
            for index, url in enumerate(normalized[1:])
        ]
        self.assertEqual(1, len({public_source_key(source) for source in filtered_sources}))

    def test_default_service_registers_amazon_jobs_adapter(self) -> None:
        self.assertIn(JobSourceType.AMAZON_JOBS, default_adapters())


if __name__ == "__main__":
    unittest.main()
