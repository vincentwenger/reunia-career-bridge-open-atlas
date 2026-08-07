from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType
from job_discovery.public_catalog import public_source_key
from job_discovery.service import default_adapters
from job_discovery.sources.base import HttpResponse
from job_discovery.sources.jobvite import JobviteJobSource, parse_jobvite_careers_url


@dataclass
class StubHttpClient:
    responses: dict[str, HttpResponse]

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.allowed_domains_by_url: dict[str, tuple[str, ...]] = {}

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
        self.allowed_domains_by_url[url] = tuple(allowed_domains)
        return self.responses.get(
            url,
            HttpResponse(404, {"content-type": "text/html"}, b"", url),
        )

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
        raise AssertionError("Jobvite discovery should not POST")


def response(
    url: str, payload: str, content_type: str = "text/html"
) -> HttpResponse:
    return HttpResponse(
        200,
        {"content-type": content_type},
        payload.encode("utf-8"),
        url,
    )


def jobposting_html(*, title: str, job_id: str, url: str) -> str:
    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {"value": job_id},
        "title": title,
        "description": " ".join(
            ["Review operational risk controls and banking processes."] * 50
        ),
        "jobLocation": {
            "address": {"addressLocality": "Hillsboro", "addressRegion": "OR"}
        },
        "url": url,
    }
    return '<script type="application/ld+json">' + json.dumps(payload) + "</script>"


class JobviteSourceTests(unittest.TestCase):
    listing = "https://jobs.jobvite.com/firsttechfed/search"

    def test_normalizes_board_search_and_detail_urls(self) -> None:
        for value in (
            "https://jobs.jobvite.com/firsttechfed",
            self.listing + "?p=2",
            "https://jobs.jobvite.com/firsttechfed/job/oJkrAfwO",
            "https://search.jobvite.com/firsttechfed/job/oJkrAfwO",
            "https://www.jobvite.com/firsttechfed/search",
        ):
            with self.subTest(value=value):
                target = parse_jobvite_careers_url(value)
                self.assertEqual(self.listing, target.listing_url)
                self.assertEqual(
                    (
                        "jobs.jobvite.com",
                        "search.jobvite.com",
                        "www.jobvite.com",
                    ),
                    target.allowed_domains,
                )

    def test_rejects_unrelated_jobvite_subdomains(self) -> None:
        with self.assertRaisesRegex(ValueError, "Jobvite URL must use"):
            parse_jobvite_careers_url(
                "https://customer-controlled.jobvite.com/firsttechfed/search"
            )

    def test_accepts_jobvite_owned_redirect_host_and_keeps_stable_urls(self) -> None:
        robots = "https://jobs.jobvite.com/robots.txt"
        redirected_robots = "https://search.jobvite.com/robots.txt"
        redirected_listing = "https://search.jobvite.com/firsttechfed/search"
        redirected_detail = "https://search.jobvite.com/firsttechfed/job/oJkrAfwO"
        public_detail = "https://jobs.jobvite.com/firsttechfed/job/oJkrAfwO"
        listing_html = (
            f'<html><body><a href="{redirected_detail}">Risk Analyst</a></body></html>'
        )
        source = CompanySource(
            id="first-tech",
            owner_id="owner",
            company_name="First Tech Federal Credit Union",
            careers_url=self.listing,
            source_type=JobSourceType.JOBVITE,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )
        client = StubHttpClient(
            {
                robots: response(redirected_robots, "User-agent: *\nAllow: /", "text/plain"),
                redirected_robots: response(
                    redirected_robots, "User-agent: *\nAllow: /", "text/plain"
                ),
                self.listing: response(redirected_listing, listing_html),
                redirected_detail: response(
                    redirected_detail,
                    jobposting_html(
                        title="Risk Analyst",
                        job_id="oJkrAfwO",
                        url=redirected_detail,
                    ),
                ),
            }
        )

        jobs = JobviteJobSource(client).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual(public_detail, jobs[0].canonical_url)
        self.assertEqual(public_detail, jobs[0].apply_url)
        self.assertIn(redirected_detail, client.calls)

    def test_accepts_jobvite_shared_host_for_robots_redirect(self) -> None:
        robots = "https://jobs.jobvite.com/robots.txt"
        shared_robots = "https://www.jobvite.com/robots.txt"
        detail = "https://jobs.jobvite.com/firsttechfed/job/oJkrAfwO"
        listing_html = f'<html><body><a href="{detail}">Risk Analyst</a></body></html>'
        source = CompanySource(
            id="first-tech",
            owner_id="owner",
            company_name="First Tech Federal Credit Union",
            careers_url=self.listing,
            source_type=JobSourceType.JOBVITE,
            source_identifier="firsttechfed",
            filters={"min_request_interval_seconds": 0},
        )
        client = StubHttpClient(
            {
                robots: response(
                    shared_robots,
                    "User-agent: *\nAllow: /",
                    "text/plain",
                ),
                self.listing: response(self.listing, listing_html),
                detail: response(
                    detail,
                    jobposting_html(
                        title="Risk Analyst",
                        job_id="oJkrAfwO",
                        url=detail,
                    ),
                ),
            }
        )

        jobs = JobviteJobSource(client).fetch_jobs(source)

        self.assertEqual(["Risk Analyst"], [job.title for job in jobs])
        self.assertEqual(detail, jobs[0].canonical_url)
        self.assertIn(
            "www.jobvite.com", client.allowed_domains_by_url[robots]
        )

    def test_fetches_public_listing_and_detail(self) -> None:
        robots = "https://jobs.jobvite.com/robots.txt"
        detail = "https://jobs.jobvite.com/firsttechfed/job/oJkrAfwO"
        listing_html = f'''<html><body><div class="job-listing">
          <a href="{detail}">Payment Services Associate II</a>
          <span>Hillsboro, Oregon</span></div></body></html>'''
        source = CompanySource(
            id="first-tech",
            owner_id="owner",
            company_name="First Tech Federal Credit Union",
            careers_url=self.listing,
            source_type=JobSourceType.JOBVITE,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )
        jobs = JobviteJobSource(
            StubHttpClient(
                {
                    robots: response(
                        robots,
                        "User-agent: *\nAllow: /",
                        "text/plain",
                    ),
                    self.listing: response(self.listing, listing_html),
                    detail: response(
                        detail,
                        jobposting_html(
                            title="Payment Services Associate II",
                            job_id="oJkrAfwO",
                            url=detail,
                        ),
                    ),
                }
            )
        ).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual("oJkrAfwO", jobs[0].external_job_id)
        self.assertEqual("Jobvite", jobs[0].metadata["portal_platform"])
        self.assertIn("operational risk", jobs[0].description)


    def test_prefers_current_jobs_route_before_legacy_search_variants(self) -> None:
        robots = "https://jobs.jobvite.com/robots.txt"
        jobs_route = "https://jobs.jobvite.com/firsttechfed/jobs"
        detail = "https://jobs.jobvite.com/firsttechfed/job/oJkrAfwO"
        listing_html = f'<html><body><a href="{detail}">Risk Analyst</a></body></html>'
        client = StubHttpClient(
            {
                robots: response(robots, "User-agent: *\nAllow: /", "text/plain"),
                jobs_route: response(jobs_route, listing_html),
                detail: response(
                    detail,
                    jobposting_html(
                        title="Risk Analyst",
                        job_id="oJkrAfwO",
                        url=detail,
                    ),
                ),
            }
        )
        source = CompanySource(
            id="first-tech",
            owner_id="owner",
            company_name="First Tech Federal Credit Union",
            careers_url=self.listing,
            source_type=JobSourceType.JOBVITE,
            source_identifier="firsttechfed",
            filters={"min_request_interval_seconds": 0},
        )

        jobs = JobviteJobSource(client).fetch_jobs(source)

        self.assertEqual(["Risk Analyst"], [job.title for job in jobs])
        self.assertEqual(jobs_route, client.calls[1])
        self.assertNotIn(self.listing, client.calls)

    def test_falls_back_to_board_root_when_search_route_is_unavailable(self) -> None:
        robots = "https://jobs.jobvite.com/robots.txt"
        board_root = "https://jobs.jobvite.com/firsttechfed"
        page_zero = self.listing + "?p=0"
        detail = "https://jobs.jobvite.com/firsttechfed/job/oJkrAfwO"
        listing_html = f'<html><body><a href="{detail}">Risk Analyst</a></body></html>'
        client = StubHttpClient(
            {
                robots: response(robots, "User-agent: *\nAllow: /", "text/plain"),
                self.listing: HttpResponse(
                    404, {"content-type": "text/html"}, b"", self.listing
                ),
                page_zero: HttpResponse(
                    404, {"content-type": "text/html"}, b"", page_zero
                ),
                board_root: response(board_root, listing_html),
                detail: response(
                    detail,
                    jobposting_html(title="Risk Analyst", job_id="oJkrAfwO", url=detail),
                ),
            }
        )
        source = CompanySource(
            id="first-tech",
            owner_id="owner",
            company_name="First Tech Federal Credit Union",
            careers_url=self.listing,
            source_type=JobSourceType.JOBVITE,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )

        jobs = JobviteJobSource(client).fetch_jobs(source)

        self.assertEqual(["Risk Analyst"], [job.title for job in jobs])
        self.assertIn(board_root, client.calls)

    def test_normalizes_apply_suffix_and_deduplicates_job_id(self) -> None:
        robots = "https://jobs.jobvite.com/robots.txt"
        detail = "https://jobs.jobvite.com/firsttechfed/job/oJkrAfwO"
        apply = detail + "/apply"
        listing_html = f'''<html><body><div class="job-listing">
          <a href="{detail}">Risk Analyst</a><a href="{apply}">Apply</a>
        </div></body></html>'''
        source = CompanySource(
            id="first-tech",
            owner_id="owner",
            company_name="First Tech",
            careers_url=self.listing,
            source_type=JobSourceType.JOBVITE,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )
        detail_html = jobposting_html(
            title="Risk Analyst", job_id="oJkrAfwO", url=detail
        )
        jobs = JobviteJobSource(
            StubHttpClient(
                {
                    robots: response(robots, "User-agent: *\nAllow: /", "text/plain"),
                    self.listing: response(self.listing, listing_html),
                    detail: response(detail, detail_html),
                    apply: response(apply, detail_html),
                }
            )
        ).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual(detail, jobs[0].canonical_url)
        self.assertEqual("oJkrAfwO", jobs[0].external_job_id)

    def test_equivalent_urls_share_catalog_identity_and_adapter_is_registered(self) -> None:
        sources = [
            CompanySource(
                id=str(index),
                owner_id="owner",
                company_name="First Tech",
                careers_url=url,
                source_type=JobSourceType.JOBVITE,
                source_identifier="",
            )
            for index, url in enumerate(
                (
                    "https://jobs.jobvite.com/firsttechfed",
                    self.listing,
                    "https://jobs.jobvite.com/firsttechfed/job/oJkrAfwO",
                )
            )
        ]
        self.assertEqual(1, len({public_source_key(source) for source in sources}))
        self.assertIn(JobSourceType.JOBVITE, default_adapters())


if __name__ == "__main__":
    unittest.main()
