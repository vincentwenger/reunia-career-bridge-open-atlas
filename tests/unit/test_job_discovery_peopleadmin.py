from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType
from job_discovery.public_catalog import public_source_key
from job_discovery.service import default_adapters
from job_discovery.sources.base import HttpResponse, RobotsDeniedError
from job_discovery.sources.indexed_search import IndexedPostingHit
from job_discovery.sources.peopleadmin import (
    PeopleAdminJobSource,
    parse_peopleadmin_careers_url,
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
        raise AssertionError("PeopleAdmin discovery should not POST")


@dataclass
class StubIndexedSearch:
    hits: list[IndexedPostingHit]

    def __post_init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def find_postings(
        self,
        *,
        company_name: str,
        host: str,
        path_pattern,
        max_results: int,
        index_page_url: str = "",
    ) -> list[IndexedPostingHit]:
        self.calls.append(
            {
                "company_name": company_name,
                "host": host,
                "path_pattern": path_pattern,
                "max_results": max_results,
                "index_page_url": index_page_url,
            }
        )
        return self.hits[:max_results]


def response(url: str, payload: str, *, content_type: str = "text/html") -> HttpResponse:
    return HttpResponse(
        200,
        {"content-type": content_type},
        payload.encode("utf-8"),
        url,
    )


class PeopleAdminSourceTests(unittest.TestCase):
    listing_url = "https://jobs.hrc.pdx.edu/postings/search"

    def test_normalizes_vendor_vanity_root_search_and_detail_urls(self) -> None:
        self.assertEqual(
            self.listing_url,
            parse_peopleadmin_careers_url("https://jobs.hrc.pdx.edu/").listing_url,
        )
        self.assertEqual(
            self.listing_url,
            parse_peopleadmin_careers_url(
                "https://jobs.hrc.pdx.edu/postings/50379?locale=en"
            ).listing_url,
        )
        self.assertEqual(
            "https://unc.peopleadmin.com/postings/search",
            parse_peopleadmin_careers_url(
                "https://unc.peopleadmin.com/postings/search?page=2"
            ).listing_url,
        )

    def test_accepts_public_peopleadmin_and_institution_branded_hosts(self) -> None:
        for url in (
            "https://unc.peopleadmin.com/postings/search",
            "https://jobs.hrc.pdx.edu/postings/search",
        ):
            with self.subTest(url=url):
                source = CompanySource(
                    id="peopleadmin-source",
                    owner_id="owner-1",
                    company_name="University",
                    careers_url=url,
                    source_type=JobSourceType.PEOPLEADMIN,
                    source_identifier="",
                )
                self.assertEqual(JobSourceType.PEOPLEADMIN, source.source_type)

    def test_fetches_paginated_listings_and_html_detail_pages(self) -> None:
        page_two = self.listing_url + "?page=2"
        detail_one = "https://jobs.hrc.pdx.edu/postings/50379"
        detail_two = "https://jobs.hrc.pdx.edu/postings/50380"
        robots_url = "https://jobs.hrc.pdx.edu/robots.txt"
        description_one = " ".join(
            [
                "Manage research integrity programs, regulatory reviews, policy, "
                "training, and cross-functional university compliance operations."
            ]
            * 18
        )
        description_two = " ".join(
            [
                "Lead technology operations, data governance, service delivery, "
                "security controls, and continuous improvement for the university."
            ]
            * 18
        )
        listing_one = f'''<html><body>
          <div class="job-posting-result">
            <h3><a href="/postings/50379">Research Integrity Program Manager</a></h3>
            <span>Location: Portland, Oregon | Department: Research Administration</span>
            <a href="/postings/50379">View Details</a>
          </div>
          <a rel="next" href="/postings/search?page=2">Next</a>
        </body></html>'''
        listing_two = f'''<html><body>
          <div class="job-posting-result">
            <h3><a href="{detail_two}">Director of Technology Operations</a></h3>
            <span>Location: Portland, Oregon | Department: Information Technology</span>
          </div>
        </body></html>'''
        detail_one_html = f'''<html><body>
          <h1>Research Integrity Program Manager</h1>
          <section class="position-summary"><h2>Position Summary</h2>
          <p>{description_one}</p></section>
        </body></html>'''
        detail_two_html = f'''<html><body>
          <h1>Director of Technology Operations</h1>
          <section class="position-summary"><h2>Position Summary</h2>
          <p>{description_two}</p></section>
        </body></html>'''
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                self.listing_url: response(self.listing_url, listing_one),
                page_two: response(page_two, listing_two),
                detail_one: response(detail_one, detail_one_html),
                detail_two: response(detail_two, detail_two_html),
            }
        )
        source = CompanySource(
            id="pdx-peopleadmin",
            owner_id="owner-1",
            company_name="Portland State University",
            careers_url="https://jobs.hrc.pdx.edu/",
            source_type=JobSourceType.PEOPLEADMIN,
            source_identifier="",
            filters={
                "min_request_interval_seconds": 0,
                "max_pages": 2,
                "detail_fetch_limit": 2,
            },
        )

        jobs = PeopleAdminJobSource(http).fetch_jobs(source)

        self.assertEqual(2, len(jobs))
        self.assertEqual(
            ["Research Integrity Program Manager", "Director of Technology Operations"],
            [job.title for job in jobs],
        )
        self.assertIn("regulatory reviews", jobs[0].description)
        self.assertIn("data governance", jobs[1].description)
        self.assertEqual("50379", jobs[0].external_job_id)
        self.assertEqual("PeopleAdmin", jobs[0].metadata["portal_platform"])
        self.assertIn(page_two, http.calls)

    def test_uses_domain_restricted_index_when_listing_is_blocked(self) -> None:
        detail_one = "https://jobs.hrc.pdx.edu/postings/50379"
        detail_two = "https://jobs.hrc.pdx.edu/postings/50380"
        robots_url = "https://jobs.hrc.pdx.edu/robots.txt"
        description = " ".join(
            [
                "Lead data, technology, governance, compliance, and service operations "
                "for Portland State University with measurable delivery outcomes."
            ]
            * 18
        )
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: *\nDisallow: /postings/search\nAllow: /postings/\n",
                    content_type="text/plain",
                ),
                detail_one: response(
                    detail_one,
                    f"<html><body><h1>Research Integrity Program Manager</h1>"
                    f"<section><p>{description}</p></section></body></html>",
                ),
                detail_two: response(
                    detail_two,
                    f"<html><body><h1>Director of Technology Operations</h1>"
                    f"<section><p>{description}</p></section></body></html>",
                ),
            }
        )
        indexed = StubIndexedSearch(
            [
                IndexedPostingHit(detail_one),
                IndexedPostingHit(detail_two),
                IndexedPostingHit("https://third-party.example/jobs/1"),
            ]
        )
        source = CompanySource(
            id="pdx-indexed",
            owner_id="owner-1",
            company_name="Portland State University",
            careers_url="https://jobs.hrc.pdx.edu/",
            source_type=JobSourceType.PEOPLEADMIN,
            source_identifier="",
            filters={
                "min_request_interval_seconds": 0,
                "max_jobs": 2,
                "detail_fetch_limit": 2,
                "indexed_search_fallback": True,
            },
        )

        jobs = PeopleAdminJobSource(http, indexed_search=indexed).fetch_jobs(source)

        self.assertEqual(2, len(jobs))
        self.assertEqual(
            ["Research Integrity Program Manager", "Director of Technology Operations"],
            [job.title for job in jobs],
        )
        self.assertTrue(
            all(
                job.metadata["discovery_mode"] == "indexed_metadata_fallback"
                for job in jobs
            )
        )
        self.assertTrue(
            all(job.metadata["scan_completeness"] == "partial" for job in jobs)
        )
        self.assertEqual([robots_url, detail_one, detail_two], http.calls)
        self.assertEqual("jobs.hrc.pdx.edu", indexed.calls[0]["host"])
        self.assertEqual(2, indexed.calls[0]["max_results"])
        self.assertEqual(self.listing_url, indexed.calls[0]["index_page_url"])

    def test_preserves_indexed_metadata_when_detail_page_is_unreadable(self) -> None:
        detail = "https://jobs.hrc.pdx.edu/postings/50379"
        robots_url = "https://jobs.hrc.pdx.edu/robots.txt"
        indexed_description = (
            "Manage research integrity programs, policy, training, compliance, "
            "case review, data analysis, and cross-functional university operations."
        )
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: *\nDisallow: /postings/search\nAllow: /postings/\n",
                    content_type="text/plain",
                ),
                detail: HttpResponse(
                    503,
                    {"content-type": "text/html"},
                    b"Service unavailable",
                    detail,
                ),
            }
        )
        indexed = StubIndexedSearch(
            [
                IndexedPostingHit(
                    detail,
                    "Research Integrity Program Manager",
                    "Portland, OR",
                    "2026-07-31",
                    indexed_description,
                    True,
                )
            ]
        )
        source = CompanySource(
            id="pdx-indexed-metadata",
            owner_id="owner-1",
            company_name="Portland State University",
            careers_url="https://jobs.hrc.pdx.edu/",
            source_type=JobSourceType.PEOPLEADMIN,
            source_identifier="",
            filters={
                "min_request_interval_seconds": 0,
                "indexed_search_fallback": True,
                "indexed_search_max_results": 1,
                "detail_fetch_limit": 1,
            },
        )

        jobs = PeopleAdminJobSource(http, indexed_search=indexed).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual("Research Integrity Program Manager", jobs[0].title)
        self.assertIn("research integrity", jobs[0].description.casefold())
        self.assertEqual(
            "indexed_metadata_fallback", jobs[0].metadata["discovery_mode"]
        )
        self.assertEqual("hosted_search_index", jobs[0].metadata["listing_source"])
        self.assertIn("HTTP 503", jobs[0].metadata["detail_error"])
        self.assertFalse(PeopleAdminJobSource.scan_is_complete(source, jobs))

    def test_keeps_permission_error_when_indexed_fallback_finds_nothing(self) -> None:
        robots_url = "https://jobs.hrc.pdx.edu/robots.txt"
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: *\nDisallow: /postings/search\n",
                    content_type="text/plain",
                )
            }
        )
        source = CompanySource(
            id="pdx-empty-index",
            owner_id="owner-1",
            company_name="Portland State University",
            careers_url="https://jobs.hrc.pdx.edu/",
            source_type=JobSourceType.PEOPLEADMIN,
            source_identifier="",
            filters={"indexed_search_fallback": True},
        )

        with self.assertRaisesRegex(RobotsDeniedError, "found no current official"):
            PeopleAdminJobSource(
                http, indexed_search=StubIndexedSearch([])
            ).fetch_jobs(source)

        self.assertEqual([robots_url], http.calls)

    def test_equivalent_root_search_and_detail_urls_share_catalog_identity(self) -> None:
        sources = [
            CompanySource(
                id=f"peopleadmin-{index}",
                owner_id="owner-1",
                company_name="Portland State University",
                careers_url=url,
                source_type=JobSourceType.PEOPLEADMIN,
                source_identifier="",
            )
            for index, url in enumerate(
                (
                    "https://jobs.hrc.pdx.edu/",
                    self.listing_url,
                    "https://jobs.hrc.pdx.edu/postings/50379",
                )
            )
        ]
        self.assertEqual(1, len({public_source_key(source) for source in sources}))

    def test_default_service_registers_peopleadmin_adapter(self) -> None:
        self.assertIn(JobSourceType.PEOPLEADMIN, default_adapters())


if __name__ == "__main__":
    unittest.main()
