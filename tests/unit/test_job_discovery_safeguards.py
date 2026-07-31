from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.application_conversion import (
    AUTOMATIC_APPLICATION_SUBMISSION_SUPPORTED,
    MVP_APPLICATION_SUBMISSION_MODE,
    DiscoveredJobApplicationService,
)
from job_discovery.deduplication import deduplicate_jobs
from job_discovery.models import CompanySource, DiscoveredJob, JobSourceType
from job_discovery.normalization import canonicalize_url, html_to_text
from job_discovery.sources.ashby import AshbyJobSource
from job_discovery.sources.base import (
    CompanyRateLimiter,
    HttpResponse,
    SourcePolicyError,
    UnsafeUrlError,
    validate_fetch_url,
)
from job_discovery.sources.greenhouse import GreenhouseJobSource
from job_discovery.storage import InMemoryDiscoveryStore


@dataclass
class RecordingHttpClient:
    response: HttpResponse

    def __post_init__(self) -> None:
        self.options: list[dict[str, object]] = []

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
        self.options.append(
            {
                "url": url,
                "timeout": timeout,
                "max_bytes": max_bytes,
                "max_redirects": max_redirects,
                "allowed_domains": tuple(allowed_domains),
            }
        )
        if max_bytes is not None and len(self.response.body) > max_bytes:
            raise RuntimeError("response exceeded configured test limit")
        return self.response


def source(**overrides) -> CompanySource:
    values = {
        "id": "acme-gh",
        "owner_id": "owner-1",
        "company_name": "Acme",
        "careers_url": "https://boards.greenhouse.io/acme",
        "source_type": JobSourceType.GREENHOUSE,
        "source_identifier": "acme",
        "filters": {"min_request_interval_seconds": 0},
    }
    values.update(overrides)
    return CompanySource(**values)


def job(**overrides) -> DiscoveredJob:
    values = {
        "id": "job-1",
        "owner_id": "owner-1",
        "source_id": "source-1",
        "external_job_id": "external-1",
        "company": "Acme",
        "title": "Senior Data Engineer",
        "description": "Build and operate reliable financial data platforms using SQL and Python.",
        "canonical_url": "https://jobs.example.com/jobs/1",
        "first_seen_at": "2026-07-30T18:00:00+00:00",
        "last_seen_at": "2026-07-30T18:00:00+00:00",
    }
    values.update(overrides)
    return DiscoveredJob(**values)


class FetchSafeguardTests(unittest.TestCase):
    def test_private_and_credentialed_fetch_urls_are_blocked(self) -> None:
        for url in (
            "http://127.0.0.1/jobs",
            "http://169.254.169.254/latest/meta-data",
            "http://user:pass@example.com/jobs",
            "http://localhost/jobs",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrlError):
                    validate_fetch_url(url)

    def test_generic_source_rejects_private_ip_at_configuration_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "private or reserved"):
            source(
                id="private",
                source_type=JobSourceType.GENERIC_JSONLD,
                source_identifier="",
                careers_url="http://10.0.0.5/jobs",
            )

    def test_api_response_cannot_redirect_outside_allowed_domain(self) -> None:
        request_url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
        body = json.dumps({"jobs": []}).encode("utf-8")
        http = RecordingHttpClient(
            HttpResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=body,
                url="https://attacker.example/jobs",
            )
        )
        with self.assertRaisesRegex(UnsafeUrlError, "outside the configured allowed domains"):
            GreenhouseJobSource(http).fetch_jobs(source())
        self.assertEqual(request_url, http.options[0]["url"])

    def test_api_fetch_passes_timeout_size_redirect_and_domain_limits(self) -> None:
        url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
        http = RecordingHttpClient(
            HttpResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=b'{"jobs": []}',
                url=url,
            )
        )
        GreenhouseJobSource(http).fetch_jobs(
            source(
                filters={
                    "timeout_seconds": 7,
                    "max_response_bytes": 250_000,
                    "max_redirects": 2,
                    "min_request_interval_seconds": 0,
                }
            )
        )
        options = http.options[0]
        self.assertEqual(7.0, options["timeout"])
        self.assertEqual(250_000, options["max_bytes"])
        self.assertEqual(2, options["max_redirects"])
        self.assertEqual(("boards-api.greenhouse.io",), options["allowed_domains"])

    def test_ashby_policy_rejects_unlisted_collection(self) -> None:
        http = RecordingHttpClient(
            HttpResponse(200, {"content-type": "application/json"}, b'{"jobs": []}', "https://api.ashbyhq.com/")
        )
        configured = source(
            id="ashby",
            source_type=JobSourceType.ASHBY,
            source_identifier="acme",
            careers_url="https://jobs.ashbyhq.com/acme",
            filters={"include_unlisted": True},
        )
        with self.assertRaisesRegex(SourcePolicyError, "publicly listed"):
            AshbyJobSource(http).fetch_jobs(configured)
        self.assertEqual([], http.options)

    def test_rate_limit_is_scoped_per_company_source(self) -> None:
        times = iter([0.0, 0.1, 0.9, 1.0])
        sleeps: list[float] = []
        limiter = CompanyRateLimiter(clock=lambda: next(times), sleeper=sleeps.append)
        limiter.wait("owner:company-a", 1.0)
        limiter.wait("owner:company-b", 1.0)
        limiter.wait("owner:company-a", 1.0)
        self.assertEqual(1, len(sleeps))
        self.assertAlmostEqual(0.1, sleeps[0])


class NormalizationAndDeduplicationTests(unittest.TestCase):
    def test_html_sanitization_discards_executable_and_hidden_content(self) -> None:
        value = "<p>Build systems</p><script>alert('x')</script><style>.x{}</style><img src=x onerror=evil()>"
        text = html_to_text(value)
        self.assertEqual("Build systems", text)
        self.assertNotIn("script", text.casefold())
        self.assertNotIn("alert", text.casefold())
        self.assertNotIn("onerror", text.casefold())

    def test_canonical_url_normalization_is_deterministic(self) -> None:
        value = "HTTPS://Example.COM:443/jobs/../jobs/1/?b=2&utm_source=x&a=1#details"
        self.assertEqual(
            "https://example.com/jobs/1?a=1&b=2",
            canonicalize_url(value),
        )

    def test_content_fingerprint_deduplicates_cross_source_postings(self) -> None:
        first = job()
        second = job(
            id="job-2",
            source_id="source-2",
            external_job_id="external-2",
            canonical_url="https://careers.example.net/openings/abc",
            apply_url="https://careers.example.net/openings/abc/apply",
        )
        results = deduplicate_jobs([first, second])
        self.assertEqual(1, len(results))
        self.assertEqual(first.description_fingerprint, second.description_fingerprint)

    def test_reappearing_posting_resets_missing_scan_counter(self) -> None:
        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        configured = source(id="source-1")
        original = job(source_id="source-1")
        store.sync_discovered_jobs(configured, [original])
        store.sync_discovered_jobs(configured, [])
        store.sync_discovered_jobs(configured, [])
        before = store.get_discovered_job("owner-1", "source-1", "job-1")
        self.assertIsNotNone(before)
        self.assertTrue(before.active)
        self.assertEqual(2, before.missed_scan_count)

        refreshed = store.sync_discovered_jobs(configured, [original])[0]
        self.assertTrue(refreshed.active)
        self.assertEqual(0, refreshed.missed_scan_count)

    def test_deactivation_threshold_is_bounded_and_configurable(self) -> None:
        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        configured = source(
            id="source-1",
            filters={"deactivate_after_missed_scans": 2},
        )
        original = job(source_id="source-1")
        store.sync_discovered_jobs(configured, [original])
        store.sync_discovered_jobs(configured, [])
        self.assertTrue(store.get_discovered_job("owner-1", "source-1", "job-1").active)
        store.sync_discovered_jobs(configured, [])
        self.assertFalse(store.get_discovered_job("owner-1", "source-1", "job-1").active)


class MvpBoundaryTests(unittest.TestCase):
    def test_mvp_has_no_automatic_application_submission(self) -> None:
        self.assertFalse(AUTOMATIC_APPLICATION_SUBMISSION_SUPPORTED)
        self.assertEqual("manual_workspace_only", MVP_APPLICATION_SUBMISSION_MODE)
        self.assertFalse(hasattr(DiscoveredJobApplicationService, "submit_application"))
        self.assertFalse(hasattr(DiscoveredJobApplicationService, "auto_apply"))


if __name__ == "__main__":
    unittest.main()
