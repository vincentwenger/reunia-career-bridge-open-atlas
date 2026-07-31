from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType, WorkplaceType
from job_discovery.sources.ashby import AshbyJobSource
from job_discovery.sources.base import DEFAULT_USER_AGENT, HttpResponse
from job_discovery.sources.generic_jsonld import GenericJsonLdJobSource, HostRateLimiter
from job_discovery.sources.greenhouse import GreenhouseJobSource
from job_discovery.sources.lever import LeverJobSource
from job_discovery.sources.workday import WorkdayJobSource, parse_workday_careers_url
from job_discovery.storage import InMemoryTTLCache


@dataclass
class StubHttpClient:
    responses: dict[str, HttpResponse]

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.post_calls: list[tuple[str, bytes]] = []
        self.request_options: list[dict[str, object]] = []

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
        self.request_options.append({
            "timeout": timeout,
            "max_bytes": max_bytes,
            "max_redirects": max_redirects,
            "allowed_domains": tuple(allowed_domains),
        })
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
        self.calls.append(url)
        self.post_calls.append((url, body))
        self.request_options.append({
            "timeout": timeout,
            "max_bytes": max_bytes,
            "max_redirects": max_redirects,
            "allowed_domains": tuple(allowed_domains),
        })
        response = self.responses[url]
        if max_bytes is not None and len(response.body) > max_bytes:
            raise AssertionError("stub response exceeds max_bytes")
        return response


def response(url: str, payload, *, status: int = 200, content_type: str = "application/json") -> HttpResponse:
    body = payload if isinstance(payload, bytes) else (
        payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
    )
    return HttpResponse(status=status, headers={"content-type": content_type}, body=body, url=url)


class GreenhouseSourceTests(unittest.TestCase):
    def test_maps_public_board_jobs(self) -> None:
        url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
        http = StubHttpClient(
            {
                url: response(
                    url,
                    {
                        "jobs": [
                            {
                                "id": 42,
                                "title": "Senior Data Engineer",
                                "absolute_url": "https://boards.greenhouse.io/acme/jobs/42?utm_source=test",
                                "location": {"name": "Portland, OR"},
                                "content": "<p>Build data systems.</p>",
                                "updated_at": "2026-07-29T10:00:00Z",
                                "departments": [{"name": "Engineering"}],
                                "metadata": [{"name": "Employment Type", "value": "Full Time"}],
                            }
                        ]
                    },
                )
            }
        )
        source = CompanySource(id="acme-gh", owner_id="owner-1", company_name="Acme", careers_url="https://boards.greenhouse.io/acme", source_type=JobSourceType.GREENHOUSE, source_identifier="acme")

        jobs = GreenhouseJobSource(http).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        self.assertEqual("Senior Data Engineer", jobs[0].title)
        self.assertEqual("Build data systems.", jobs[0].description)
        self.assertEqual("Engineering", jobs[0].department)
        self.assertNotIn("utm_source", jobs[0].job_url)


class LeverSourceTests(unittest.TestCase):
    def test_maps_postings_and_salary(self) -> None:
        url = "https://api.lever.co/v0/postings/acme?mode=json"
        http = StubHttpClient(
            {
                url: response(
                    url,
                    [
                        {
                            "id": "lever-1",
                            "text": "AI Engineer",
                            "hostedUrl": "https://jobs.lever.co/acme/lever-1",
                            "applyUrl": "https://jobs.lever.co/acme/lever-1/apply",
                            "descriptionPlain": "Build production AI systems.",
                            "createdAt": 1785326400000,
                            "workplaceType": "hybrid",
                            "categories": {
                                "location": "Portland, Oregon",
                                "allLocations": ["Portland, Oregon"],
                                "department": "Technology",
                                "team": "AI",
                                "commitment": "Full-time",
                            },
                            "salaryRange": {
                                "min": 150000,
                                "max": 190000,
                                "currency": "USD",
                                "interval": "year",
                            },
                        }
                    ],
                )
            }
        )
        source = CompanySource(id="acme-lever", owner_id="owner-1", company_name="Acme", careers_url="https://jobs.lever.co/acme", source_type=JobSourceType.LEVER, source_identifier="acme")

        job = LeverJobSource(http).fetch_jobs(source)[0]

        self.assertEqual(WorkplaceType.HYBRID, job.workplace_type)
        self.assertEqual(150000, job.salary_min)
        self.assertEqual("Technology", job.department)


class AshbySourceTests(unittest.TestCase):
    def test_excludes_unlisted_by_default_and_includes_compensation(self) -> None:
        url = "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
        http = StubHttpClient(
            {
                url: response(
                    url,
                    {
                        "jobs": [
                            {
                                "id": "listed",
                                "title": "Platform Engineer",
                                "location": "Remote - US",
                                "isRemote": True,
                                "isListed": True,
                                "jobUrl": "https://jobs.ashbyhq.com/acme/listed",
                                "applyUrl": "https://jobs.ashbyhq.com/acme/listed/application",
                                "descriptionPlain": "Build the platform.",
                                "publishedAt": "2026-07-28T00:00:00Z",
                                "employmentType": "FullTime",
                                "compensation": {
                                    "scrapeableCompensationSalarySummary": "$160k-$200k",
                                    "compensationTiers": [],
                                },
                            },
                            {
                                "id": "unlisted",
                                "title": "Hidden Listing",
                                "location": "Remote",
                                "isListed": False,
                                "jobUrl": "https://jobs.ashbyhq.com/acme/unlisted",
                            },
                        ]
                    },
                )
            }
        )
        source = CompanySource(id="acme-ashby", owner_id="owner-1", company_name="Acme", careers_url="https://jobs.ashbyhq.com/acme", source_type=JobSourceType.ASHBY, source_identifier="acme")

        jobs = AshbyJobSource(http).fetch_jobs(source)

        self.assertEqual(["listed"], [job.external_id for job in jobs])
        self.assertEqual(WorkplaceType.REMOTE, jobs[0].workplace_type)
        self.assertEqual("$160k-$200k", jobs[0].salary_summary)


class WorkdaySourceTests(unittest.TestCase):
    def test_parses_intel_board_url_and_maps_public_jobs(self) -> None:
        board_url = "https://intel.wd1.myworkdayjobs.com/en-US/External/page/search"
        listing_url = "https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External/jobs"
        external_path = "/job/US-Oregon-Hillsboro/Data-Engineer_JR0299999"
        detail_url = (
            "https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External"
            + external_path
        )
        http = StubHttpClient(
            {
                listing_url: response(
                    listing_url,
                    {
                        "total": 1,
                        "jobPostings": [
                            {
                                "title": "Data Engineer",
                                "externalPath": external_path,
                                "locationsText": "US, Oregon, Hillsboro",
                                "postedOn": "Posted 2 Days Ago",
                                "bulletFields": ["JR0299999"],
                            }
                        ],
                    },
                ),
                detail_url: response(
                    detail_url,
                    {
                        "jobPostingInfo": {
                            "title": "Data Engineer",
                            "jobReqId": "JR0299999",
                            "jobPostingId": "Data-Engineer_JR0299999",
                            "jobDescription": "<p>Build governed data platforms with Python and SQL.</p>",
                            "location": "US, Oregon, Hillsboro",
                            "additionalLocations": ["US, California, Folsom"],
                            "timeType": "Full time",
                            "remoteType": "Hybrid",
                            "startDate": "2026-07-28",
                            "canApply": True,
                        },
                        "hiringOrganization": "Intel Corporation",
                    },
                ),
            }
        )
        source = CompanySource(
            id="intel-workday",
            owner_id="owner-1",
            company_name="Intel",
            careers_url=board_url,
            source_type=JobSourceType.WORKDAY,
            source_identifier="",
            filters={"min_request_interval_seconds": 0},
        )

        jobs = WorkdayJobSource(http).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        job = jobs[0]
        self.assertEqual("JR0299999", job.external_job_id)
        self.assertEqual("Data Engineer", job.title)
        self.assertEqual("hybrid", job.workplace_type.value)
        self.assertEqual("Full-time", job.employment_type)
        self.assertIn("Python and SQL", job.description)
        self.assertEqual(2, len(job.locations))
        self.assertEqual(
            "https://intel.wd1.myworkdayjobs.com/en-US/External/job/US-Oregon-Hillsboro/Data-Engineer_JR0299999",
            job.canonical_url,
        )
        self.assertEqual("intel", job.metadata["workday_tenant"])
        self.assertEqual("External", job.metadata["workday_site"])
        posted_payload = json.loads(http.post_calls[0][1].decode("utf-8"))
        self.assertEqual(
            {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
            posted_payload,
        )

    def test_paginates_and_stops_at_total(self) -> None:
        board_url = "https://example.wd5.myworkdayjobs.com/en-US/Careers"
        listing_url = "https://example.wd5.myworkdayjobs.com/wday/cxs/example/Careers/jobs"
        first_path = "/job/Portland/Engineer_REQ-1"
        second_path = "/job/Seattle/Engineer_REQ-2"
        responses = {
            listing_url: response(
                listing_url,
                {
                    "total": 2,
                    "jobPostings": [
                        {"title": "Engineer 1", "externalPath": first_path},
                        {"title": "Engineer 2", "externalPath": second_path},
                    ],
                },
            ),
            "https://example.wd5.myworkdayjobs.com/wday/cxs/example/Careers" + first_path: response(
                "https://example.wd5.myworkdayjobs.com/wday/cxs/example/Careers" + first_path,
                {"jobPostingInfo": {"title": "Engineer 1", "jobReqId": "REQ-1", "location": "Portland", "canApply": True}},
            ),
            "https://example.wd5.myworkdayjobs.com/wday/cxs/example/Careers" + second_path: response(
                "https://example.wd5.myworkdayjobs.com/wday/cxs/example/Careers" + second_path,
                {"jobPostingInfo": {"title": "Engineer 2", "jobReqId": "REQ-2", "location": "Seattle", "canApply": True}},
            ),
        }
        http = StubHttpClient(responses)
        source = CompanySource(
            id="example-workday",
            owner_id="owner-1",
            company_name="Example",
            careers_url=board_url,
            source_type=JobSourceType.WORKDAY,
            source_identifier="Careers",
            filters={"page_size": 2, "min_request_interval_seconds": 0},
        )

        jobs = WorkdayJobSource(http).fetch_jobs(source)

        self.assertEqual(2, len(jobs))
        self.assertEqual(1, len(http.post_calls))


    def test_interactive_detail_limit_keeps_listing_only_jobs(self) -> None:
        board_url = "https://intel.wd1.myworkdayjobs.com/en-US/External"
        listing_url = "https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External/jobs"
        paths = [
            "/job/Hillsboro/Engineer-1_JR1",
            "/job/Folsom/Engineer-2_JR2",
            "/job/Phoenix/Engineer-3_JR3",
        ]
        first_detail_url = (
            "https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External" + paths[0]
        )
        http = StubHttpClient(
            {
                listing_url: response(
                    listing_url,
                    {
                        "total": 3,
                        "jobPostings": [
                            {
                                "title": f"Engineer {index}",
                                "externalPath": path,
                                "locationsText": "US, Oregon, Hillsboro",
                                "postedOn": "Posted Today",
                                "bulletFields": [f"JR{index}"],
                            }
                            for index, path in enumerate(paths, start=1)
                        ],
                    },
                ),
                first_detail_url: response(
                    first_detail_url,
                    {
                        "jobPostingInfo": {
                            "title": "Engineer 1",
                            "jobReqId": "JR1",
                            "jobDescription": "Full detail description",
                            "location": "US, Oregon, Hillsboro",
                            "canApply": True,
                        }
                    },
                ),
            }
        )
        source = CompanySource(
            id="intel-workday-budgeted",
            owner_id="owner-1",
            company_name="Intel",
            careers_url=board_url,
            source_type=JobSourceType.WORKDAY,
            source_identifier="External",
            filters={
                "detail_fetch_limit": 1,
                "min_request_interval_seconds": 0,
            },
        )

        jobs = WorkdayJobSource(http).fetch_jobs(source)

        self.assertEqual(3, len(jobs))
        self.assertEqual("complete", jobs[0].metadata["detail_status"])
        self.assertEqual("deferred", jobs[1].metadata["detail_status"])
        self.assertIn("Engineer 2", jobs[1].description)
        self.assertEqual(1, len(http.calls) - len(http.post_calls))

    def test_parser_accepts_public_board_and_cxs_urls(self) -> None:
        board = parse_workday_careers_url(
            "https://intel.wd1.myworkdayjobs.com/en-US/External/page/jobs"
        )
        endpoint = parse_workday_careers_url(
            "https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External/jobs"
        )

        self.assertEqual(("intel", "External", "en-US"), (board.tenant, board.site, board.locale))
        self.assertEqual(("intel", "External"), (endpoint.tenant, endpoint.site))


class GenericJsonLdSourceTests(unittest.TestCase):
    def test_obeys_robots_parses_jobposting_and_uses_page_cache(self) -> None:
        robots_url = "https://careers.example.com/robots.txt"
        page_url = "https://careers.example.com/jobs"
        detail_url = "https://careers.example.com/jobs/data-engineer"
        page_html = f"""
        <html><body>
          <a href="{detail_url}">Data Engineer</a>
        </body></html>
        """
        detail_html = """
        <html><head>
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "identifier": {"value": "job-123"},
            "title": "Data Engineer",
            "description": "<p>Build reliable pipelines.</p>",
            "hiringOrganization": {"name": "Example Corp"},
            "datePosted": "2026-07-29",
            "employmentType": ["FULL_TIME"],
            "jobLocationType": "TELECOMMUTE",
            "jobLocation": {
              "@type": "Place",
              "address": {"addressCountry": "US"}
            },
            "skills": "Python, SQL, AWS",
            "baseSalary": {
              "@type": "MonetaryAmount",
              "currency": "USD",
              "value": {"minValue": 140000, "maxValue": 180000, "unitText": "YEAR"}
            },
            "url": "https://careers.example.com/jobs/data-engineer?utm_campaign=test"
          }
          </script>
        </head></html>
        """
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: ReuniaJobBot\nDisallow: /private\nAllow: /jobs\n",
                    content_type="text/plain; charset=utf-8",
                ),
                page_url: response(page_url, page_html, content_type="text/html"),
                detail_url: response(detail_url, detail_html, content_type="text/html"),
            }
        )
        source = CompanySource(
            id="example-jsonld",
            owner_id="owner-1",
            company_name="Example Corp",
            careers_url=page_url,
            source_type=JobSourceType.GENERIC_JSONLD,
            source_identifier="",
            filters={"max_pages": 2, "min_request_interval_seconds": 0},
        )
        adapter = GenericJsonLdJobSource(http, cache=InMemoryTTLCache())

        first = adapter.fetch_jobs(source)
        second = adapter.fetch_jobs(source)

        self.assertEqual(1, len(first))
        self.assertEqual("job-123", first[0].external_id)
        self.assertEqual(("Python", "SQL", "AWS"), first[0].skills)
        self.assertEqual(140000, first[0].salary_min)
        self.assertEqual(WorkplaceType.REMOTE, first[0].workplace_type)
        self.assertNotIn("utm_campaign", first[0].job_url)
        self.assertEqual(first[0].external_id, second[0].external_id)
        self.assertEqual(1, http.calls.count(robots_url))
        self.assertEqual(1, http.calls.count(page_url))
        self.assertEqual(1, http.calls.count(detail_url))

    def test_denies_disallowed_start_page(self) -> None:
        robots_url = "https://careers.example.com/robots.txt"
        page_url = "https://careers.example.com/private/jobs"
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: *\nDisallow: /private\n",
                    content_type="text/plain",
                )
            }
        )
        source = CompanySource(
            id="blocked",
            owner_id="owner-1",
            company_name="Example",
            careers_url=page_url,
            source_type=JobSourceType.GENERIC_JSONLD,
            source_identifier="",
        )

        with self.assertRaisesRegex(RuntimeError, "robots.txt disallows"):
            GenericJsonLdJobSource(http).fetch_jobs(source)
        self.assertNotIn(page_url, http.calls)


    def test_default_user_agent_is_descriptive(self) -> None:
        self.assertIn("ReuniaJobBot", DEFAULT_USER_AGENT)
        self.assertIn("reunia.app/job-discovery", DEFAULT_USER_AGENT)

    def test_rfc_status_handling_allows_404_and_denies_500(self) -> None:
        page_url = "https://careers.example.com/jobs"
        robots_url = "https://careers.example.com/robots.txt"
        html = """<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer","url":"https://careers.example.com/jobs/1"}</script>"""

        allow_http = StubHttpClient({
            robots_url: response(robots_url, "missing", status=404, content_type="text/plain"),
            page_url: response(page_url, html, content_type="text/html"),
        })
        source = CompanySource(id="status", owner_id="owner-1", company_name="Example", careers_url=page_url, source_type=JobSourceType.GENERIC_JSONLD, source_identifier="", filters={"min_request_interval_seconds": 0})
        self.assertEqual(1, len(GenericJsonLdJobSource(allow_http).fetch_jobs(source)))

        deny_http = StubHttpClient({
            robots_url: response(robots_url, "error", status=503, content_type="text/plain"),
        })
        with self.assertRaisesRegex(RuntimeError, "robots.txt disallows"):
            GenericJsonLdJobSource(deny_http).fetch_jobs(source)

    def test_rate_limiter_waits_per_host(self) -> None:
        times = iter([0.0, 0.2, 1.0])
        sleeps: list[float] = []
        limiter = HostRateLimiter(clock=lambda: next(times), sleeper=sleeps.append)

        limiter.wait("example.com", 1.0)
        limiter.wait("example.com", 1.0)

        self.assertEqual(1, len(sleeps))
        self.assertAlmostEqual(0.8, sleeps[0])


if __name__ == "__main__":
    unittest.main()
