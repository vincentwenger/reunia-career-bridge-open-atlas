from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType
from job_discovery.public_catalog import public_source_key
from job_discovery.service import default_adapters
from job_discovery.sources.base import HttpResponse, RobotsDeniedError, SourceFetchError
from job_discovery.sources.branded_requisition import (
    BrandedRequisitionJobSource,
    _is_transient_fetch_error,
    parse_branded_requisition_careers_url,
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
        raise AssertionError("Branded Requisition Portal discovery should not POST")


def response(url: str, payload: str, *, content_type: str = "text/html") -> HttpResponse:
    return HttpResponse(200, {"content-type": content_type}, payload.encode("utf-8"), url)


def detail_jsonld(*, title: str, identifier: str, url: str, city: str) -> str:
    description = " ".join(
        [
            "Serve customers, manage regulated banking operations, collaborate with "
            "branch partners, protect confidential information, and deliver accurate service."
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
                "addressRegion": "WA",
                "addressCountry": "US",
            }
        },
        "url": url,
    }
    return '<html><body><script type="application/ld+json">' + json.dumps(payload) + '</script></body></html>'


class BrandedRequisitionPortalTests(unittest.TestCase):
    listing_url = "https://careers.heritagebanknw.com/search-jobs"

    def test_normalizes_root_search_feed_page_and_detail_urls(self) -> None:
        values = (
            "https://careers.heritagebanknw.com/",
            "https://careers.heritagebanknw.com/search-jobs",
            "https://careers.heritagebanknw.com/api/requisitions/search?page=3",
            "https://careers.heritagebanknw.com/job/422/bank_teller_part_time?change_lang=en",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(
                    self.listing_url,
                    parse_branded_requisition_careers_url(value).listing_url,
                )

    def test_preserves_non_pagination_public_search_selectors(self) -> None:
        target = parse_branded_requisition_careers_url(
            self.listing_url + "?page=2&city=Tacoma&category=Banking"
        )
        self.assertEqual(
            self.listing_url + "?category=Banking&city=Tacoma",
            target.listing_url,
        )

    def test_fetches_paginated_feed_and_full_public_details(self) -> None:
        page_two = self.listing_url + "?page=2"
        first_detail = "https://careers.heritagebanknw.com/job/422/bank_teller_part_time?change_lang=en"
        second_detail = "https://careers.heritagebanknw.com/job/425/branch_banking_manager?change_lang=en"
        robots_url = "https://careers.heritagebanknw.com/robots.txt"
        page_one_html = f'''<html><body><table><tbody>
          <tr><td>Bank Teller - Part Time #4484</td><td>Retail Banking</td>
              <td>Mill Creek, WA, US</td><td><a href="{first_detail}">Learn More</a></td></tr>
        </tbody></table><a rel="next" href="/search-jobs?page=2">Next</a></body></html>'''
        page_two_html = f'''<html><body><table><tbody>
          <tr><td>Branch Banking Manager #4486</td><td>Retail Banking</td>
              <td>Portland, OR, US</td><td><a href="{second_detail}">Learn More</a></td></tr>
        </tbody></table></body></html>'''
        http = StubHttpClient(
            {
                robots_url: response(robots_url, "User-agent: *\nAllow: /\n", content_type="text/plain"),
                self.listing_url: response(self.listing_url, page_one_html),
                page_two: response(page_two, page_two_html),
                first_detail: response(
                    first_detail,
                    detail_jsonld(
                        title="Bank Teller - Part Time",
                        identifier="4484",
                        url=first_detail,
                        city="Mill Creek",
                    ),
                ),
                second_detail: response(
                    second_detail,
                    detail_jsonld(
                        title="Branch Banking Manager",
                        identifier="4486",
                        url=second_detail,
                        city="Portland",
                    ),
                ),
            }
        )
        source = CompanySource(
            id="heritage",
            owner_id="owner-1",
            company_name="Heritage Bank",
            careers_url="https://careers.heritagebanknw.com/search-jobs",
            source_type=JobSourceType.BRANDED_REQUISITION,
            source_identifier="",
        )

        jobs = BrandedRequisitionJobSource(http).fetch_jobs(source)

        self.assertEqual(2, len(jobs))
        self.assertEqual(
            ["Bank Teller - Part Time", "Branch Banking Manager"],
            [job.title for job in jobs],
        )
        self.assertEqual(["4484", "4486"], [job.external_job_id for job in jobs])
        self.assertEqual(["Mill Creek, WA, US", "Portland, WA, US"], [job.location for job in jobs])
        self.assertTrue(all(len(job.description) > 500 for job in jobs))
        self.assertTrue(
            all(job.metadata["portal_platform"] == "Branded Requisition Portal" for job in jobs)
        )
        self.assertIn(page_two, http.calls)

    def test_listing_context_supports_deferred_detail_rows(self) -> None:
        detail = "https://careers.example.com/job/422/bank_teller"
        robots_url = "https://careers.example.com/robots.txt"
        listing_url = "https://careers.example.com/search-jobs"
        listing = f'''<html><body><table><tr>
          <td>Bank Teller #4484</td><td>Retail Banking</td><td>Mill Creek, WA, US</td>
          <td><a href="{detail}">Learn More</a></td>
        </tr></table></body></html>'''
        source = CompanySource(
            id="deferred",
            owner_id="owner-1",
            company_name="Example Bank",
            careers_url=listing_url,
            source_type=JobSourceType.BRANDED_REQUISITION,
            source_identifier="",
            filters={"detail_fetch_limit": 0},
        )
        jobs = BrandedRequisitionJobSource(
            StubHttpClient(
                {
                    robots_url: response(robots_url, "User-agent: *\nAllow: /\n", content_type="text/plain"),
                    listing_url: response(listing_url, listing),
                }
            )
        ).fetch_jobs(source)
        self.assertEqual("Bank Teller #4484", jobs[0].title)
        self.assertEqual("Mill Creek, WA, US", jobs[0].location)
        self.assertEqual("deferred", jobs[0].metadata["detail_status"])

    def test_shared_catalog_identity_deduplicates_listing_and_detail_urls(self) -> None:
        listing = CompanySource(
            id="one",
            owner_id="owner-1",
            company_name="Heritage Bank",
            careers_url=self.listing_url + "?page=2",
            source_type=JobSourceType.BRANDED_REQUISITION,
            source_identifier="",
        )
        detail = CompanySource(
            id="two",
            owner_id="owner-2",
            company_name="Heritage Bank",
            careers_url="https://careers.heritagebanknw.com/job/422/bank_teller_part_time",
            source_type=JobSourceType.BRANDED_REQUISITION,
            source_identifier="",
        )
        self.assertEqual(public_source_key(listing), public_source_key(detail))


    def test_retry_classifier_skips_policy_and_permanent_client_errors(self) -> None:
        self.assertFalse(
            _is_transient_fetch_error(
                RobotsDeniedError("robots.txt disallows crawling https://example.test/jobs")
            )
        )
        self.assertFalse(
            _is_transient_fetch_error(SourceFetchError("GET https://example.test returned HTTP 403"))
        )
        self.assertTrue(
            _is_transient_fetch_error(SourceFetchError("Unable to fetch URL: The read operation timed out"))
        )
        self.assertTrue(
            _is_transient_fetch_error(SourceFetchError("GET https://example.test returned HTTP 503"))
        )

    def test_does_not_repeat_403_for_each_retry_attempt(self) -> None:
        robots_url = "https://careers.example.com/robots.txt"
        listing_url = "https://careers.example.com/search-jobs"
        api_url = "https://careers.example.com/api/requisitions/search"
        forbidden_listing = HttpResponse(403, {"content-type": "text/html"}, b"", listing_url)
        forbidden_api = HttpResponse(403, {"content-type": "text/html"}, b"", api_url)
        client = StubHttpClient({
            robots_url: response(robots_url, "User-agent: *\nAllow: /", content_type="text/plain"),
            listing_url: forbidden_listing,
            api_url: forbidden_api,
        })
        source = CompanySource(
            id="heritage", owner_id="owner", company_name="Heritage Bank",
            careers_url=listing_url, source_type=JobSourceType.BRANDED_REQUISITION,
            source_identifier="",
            filters={
                "retry_attempts": 3,
                "retry_backoff_seconds": 0,
                "min_request_interval_seconds": 0,
            },
        )

        with self.assertRaises(SourceFetchError):
            BrandedRequisitionJobSource(client).fetch_jobs(source)

        self.assertEqual(1, client.calls.count(listing_url))
        self.assertEqual(1, client.calls.count(api_url))

    def test_adapter_is_registered(self) -> None:
        self.assertIn(JobSourceType.BRANDED_REQUISITION, default_adapters())


if __name__ == "__main__":
    unittest.main()
