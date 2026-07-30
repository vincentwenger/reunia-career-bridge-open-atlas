from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping

from job_discovery.models import CompanySource, JobSourceType, WorkplaceType
from job_discovery.sources.ashby import AshbyJobSource
from job_discovery.sources.base import DEFAULT_USER_AGENT, HttpResponse
from job_discovery.sources.generic_jsonld import GenericJsonLdJobSource, HostRateLimiter
from job_discovery.sources.greenhouse import GreenhouseJobSource
from job_discovery.sources.lever import LeverJobSource
from job_discovery.storage import InMemoryTTLCache


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
    ) -> HttpResponse:
        self.calls.append(url)
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
