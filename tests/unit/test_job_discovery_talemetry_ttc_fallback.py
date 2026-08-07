from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType
from job_discovery.sources.base import HttpResponse
from job_discovery.sources.indexed_search import IndexedPostingHit
from job_discovery.sources.talemetry_ttc import TalemetryTtcJobSource


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
        del headers, timeout, max_redirects, allowed_domains
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
        raise AssertionError("Talemetry/TTC discovery should not POST")


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


def http_response(
    url: str,
    body: str,
    *,
    status: int = 200,
    content_type: str = "text/html",
) -> HttpResponse:
    return HttpResponse(
        status,
        {"content-type": content_type},
        body.encode("utf-8"),
        url,
    )


class TalemetryTtcFallbackTests(unittest.TestCase):
    listing = "https://firsttechfedcareers.ttcportals.com/search/jobs"
    feed = "https://firsttechfedcareers.ttcportals.com/search/jobs.json?page=1"
    robots = "https://firsttechfedcareers.ttcportals.com/robots.txt"

    def source(self, **filters) -> CompanySource:
        return CompanySource(
            id="first-tech-ttc",
            owner_id="owner-1",
            company_name="First Tech Federal Credit Union",
            careers_url=self.listing,
            source_type=JobSourceType.TALEMETRY_TTC,
            source_identifier="",
            filters={
                "min_request_interval_seconds": 0,
                "indexed_search_fallback": True,
                "verified_syndication_fallback": False,
                "max_jobs": 10,
                "detail_fetch_limit": 10,
                **filters,
            },
        )


    def test_first_tech_uses_verified_syndication_before_indexed_search(self) -> None:
        partner_listing = (
            "https://jobs.partnersindiversity.org/employerjobs/ydcr/"
            "first-tech-federal-credit-union"
        )
        partner_robots = "https://jobs.partnersindiversity.org/robots.txt"
        detail = (
            "https://jobs.partnersindiversity.org/job/bawp6k/"
            "principal-quantitative-risk-management-analyst/hillsboro/or"
        )
        description = " ".join(
            [
                "Develop quantitative risk models, scenario forecasting, stress testing, "
                "Python and SQL analytics, governance, and regulatory reporting."
            ]
            * 18
        )
        listing_html = f"""
        <html><body>
          <section class="job-result">
            <a href="{detail}">Principal Quantitative Risk Management Analyst</a>
          </section>
        </body></html>
        """
        detail_html = f"""
        <html><head>
          <script type="application/ld+json">
          {{
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Principal Quantitative Risk Management Analyst",
            "description": "{description}",
            "datePosted": "2026-08-01",
            "employmentType": "FULL_TIME",
            "jobLocation": {{
              "@type": "Place",
              "address": {{
                "@type": "PostalAddress",
                "addressLocality": "Hillsboro",
                "addressRegion": "OR",
                "addressCountry": "US"
              }}
            }},
            "url": "{detail}"
          }}
          </script>
        </head><body><h1>Principal Quantitative Risk Management Analyst</h1></body></html>
        """
        http = StubHttpClient(
            {
                self.robots: http_response(
                    self.robots,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                self.feed: http_response(self.feed, "Forbidden", status=403),
                partner_robots: http_response(
                    partner_robots,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                partner_listing: http_response(partner_listing, listing_html),
                detail: http_response(detail, detail_html),
            }
        )
        indexed = StubIndexedSearch([])

        adapter = TalemetryTtcJobSource(http, indexed_search=indexed)
        jobs = adapter.fetch_jobs(
            self.source(
                verified_syndication_fallback=True,
                syndicated_min_request_interval_seconds=0,
            )
        )

        self.assertEqual(1, len(jobs))
        self.assertEqual(
            "Principal Quantitative Risk Management Analyst", jobs[0].title
        )
        self.assertEqual("Hillsboro, OR, US", jobs[0].location)
        self.assertIn("stress testing", jobs[0].description)
        self.assertEqual(detail, jobs[0].canonical_url)
        self.assertEqual(
            "verified_employer_syndication",
            jobs[0].metadata["discovery_mode"],
        )
        self.assertEqual("partial", jobs[0].metadata["scan_completeness"])
        self.assertFalse(adapter.scan_is_complete(self.source(), jobs))
        self.assertEqual([], indexed.calls)
        self.assertIn(partner_listing, http.calls)
        self.assertIn(detail, http.calls)

    def test_syndicated_job_description_uses_allow_listed_partner_page(self) -> None:
        partner_listing = (
            "https://jobs.partnersindiversity.org/employerjobs/ydcr/"
            "first-tech-federal-credit-union"
        )
        partner_robots = "https://jobs.partnersindiversity.org/robots.txt"
        detail = (
            "https://jobs.partnersindiversity.org/job/pbc4jt/"
            "senior-database-administrator-dba/hillsboro/or"
        )
        description = " ".join(
            [
                "Maintain SQL Server databases, high availability, security, backup, "
                "recovery, automation, performance tuning, and production support."
            ]
            * 18
        )
        listing_html = f'<section><a href="{detail}">Senior Database Administrator - DBA</a></section>'
        detail_html = f'<html><body><h1>Senior Database Administrator - DBA</h1><p>{description}</p></body></html>'
        http = StubHttpClient(
            {
                self.robots: http_response(
                    self.robots,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                self.feed: http_response(self.feed, "Forbidden", status=403),
                partner_robots: http_response(
                    partner_robots,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                partner_listing: http_response(partner_listing, listing_html),
                detail: http_response(detail, detail_html),
            }
        )
        adapter = TalemetryTtcJobSource(http, indexed_search=StubIndexedSearch([]))
        jobs = adapter.fetch_jobs(
            self.source(
                verified_syndication_fallback=True,
                syndicated_detail_fetch_limit=0,
                syndicated_min_request_interval_seconds=0,
            )
        )

        hydrated = adapter.fetch_job_description(jobs[0])

        self.assertIn("high availability", hydrated)
        self.assertEqual(1, http.calls.count(detail))

    def test_uses_exact_domain_indexed_fallback_when_listing_returns_403(self) -> None:
        detail = (
            "https://firsttechfedcareers.ttcportals.com/"
            "jobs/17599619-program-manager-iii-insurance-services"
        )
        description = " ".join(
            [
                "Lead insurance operations, process improvement, systems, compliance, "
                "and cross-functional program delivery for members and employees."
            ]
            * 16
        )
        http = StubHttpClient(
            {
                self.robots: http_response(
                    self.robots,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                self.feed: http_response(
                    self.feed,
                    "Forbidden",
                    status=403,
                ),
                detail: http_response(
                    detail,
                    f"<html><body><h1>Program Manager III, Insurance Services</h1>"
                    f"<p>{description}</p></body></html>",
                ),
            }
        )
        indexed = StubIndexedSearch(
            [
                IndexedPostingHit(
                    detail,
                    "Program Manager III, Insurance Services",
                    "Hillsboro, OR, United States",
                    "Jul 27, 2026",
                    description,
                    True,
                )
            ]
        )

        adapter = TalemetryTtcJobSource(http, indexed_search=indexed)
        jobs = adapter.fetch_jobs(self.source())

        self.assertEqual(1, len(jobs))
        self.assertEqual("Program Manager III, Insurance Services", jobs[0].title)
        self.assertIn("process improvement", jobs[0].description)
        self.assertEqual(
            "indexed_metadata_fallback", jobs[0].metadata["discovery_mode"]
        )
        self.assertEqual(403, jobs[0].metadata["listing_route_http_status"])
        self.assertEqual("partial", jobs[0].metadata["scan_completeness"])
        self.assertFalse(adapter.scan_is_complete(self.source(), jobs))
        self.assertEqual("firsttechfedcareers.ttcportals.com", indexed.calls[0]["host"])
        self.assertEqual(1, http.calls.count(self.feed))
        self.assertNotIn(self.listing, http.calls)
        self.assertNotIn(detail, http.calls)

    def test_does_not_reopen_detail_page_when_indexed_metadata_is_available(self) -> None:
        detail = (
            "https://firsttechfedcareers.ttcportals.com/"
            "jobs/17941960-staff-contracts-manager"
        )
        http = StubHttpClient(
            {
                self.robots: http_response(
                    self.robots,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                self.feed: http_response(self.feed, "Forbidden", status=403),
                detail: http_response(detail, "Forbidden", status=403),
            }
        )
        indexed = StubIndexedSearch(
            [
                IndexedPostingHit(
                    detail,
                    "Staff Contract Manager",
                    "Hillsboro, OR",
                    "Jul 9, 2026",
                    "Leads complex contract development, negotiation, execution, "
                    "risk review, and cross-functional contract governance.",
                    True,
                )
            ]
        )

        jobs = TalemetryTtcJobSource(http, indexed_search=indexed).fetch_jobs(
            self.source()
        )

        self.assertEqual(1, len(jobs))
        self.assertEqual("Staff Contract Manager", jobs[0].title)
        self.assertEqual("indexed", jobs[0].metadata["detail_status"])
        self.assertNotIn(detail, http.calls)

    def test_does_not_use_indexed_fallback_when_disabled(self) -> None:
        http = StubHttpClient(
            {
                self.robots: http_response(
                    self.robots,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                self.feed: http_response(self.feed, "Forbidden", status=403),
            }
        )
        indexed = StubIndexedSearch([])

        with self.assertRaisesRegex(Exception, "HTTP 403"):
            TalemetryTtcJobSource(http, indexed_search=indexed).fetch_jobs(
                self.source(indexed_search_fallback=False)
            )

        self.assertEqual([], indexed.calls)


if __name__ == "__main__":
    unittest.main()
