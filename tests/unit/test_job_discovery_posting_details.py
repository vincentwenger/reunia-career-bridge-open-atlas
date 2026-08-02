from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import DiscoveredJob, JobSourceType, discovered_job_id
from job_discovery.posting_details import PostingDescriptionFetcher
from job_discovery.sources.base import HttpResponse
from job_discovery.sources.oracle_cloud_hcm import (
    _api_detail_url,
    parse_oracle_cloud_hcm_careers_url,
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
        del headers, timeout, max_redirects, allowed_domains
        self.calls.append(url)
        response = self.responses[url]
        if max_bytes is not None and len(response.body) > max_bytes:
            raise AssertionError("stub response exceeds max_bytes")
        return response

    def post(self, *args, **kwargs):
        raise AssertionError("posting detail enrichment should not make POST requests")


def response(
    url: str,
    payload,
    *,
    status: int = 200,
    content_type: str = "application/json",
) -> HttpResponse:
    body = (
        payload.encode("utf-8")
        if isinstance(payload, str)
        else json.dumps(payload).encode("utf-8")
    )
    return HttpResponse(
        status=status,
        headers={"content-type": content_type},
        body=body,
        url=url,
    )


def workday_job(description: str, *, detail_status: str = "deferred") -> DiscoveredJob:
    source_id = "itc-workday"
    external_id = "R-76407"
    return DiscoveredJob(
        id=discovered_job_id("owner-1", source_id, external_id),
        owner_id="owner-1",
        source_id=source_id,
        external_job_id=external_id,
        company="ITC",
        title="Data Engineer I",
        location="Karnataka, India",
        employment_type="Full-time",
        description=description,
        canonical_url=(
            "https://itc.wd3.myworkdayjobs.com/en-US/External/"
            "job/Karnataka-India/Data-Engineer-I_R-76407"
        ),
        source_type=JobSourceType.WORKDAY,
        metadata={
            "workday_tenant": "itc",
            "workday_site": "External",
            "workday_locale": "en-US",
            "workday_external_path": (
                "/job/Karnataka-India/Data-Engineer-I_R-76407"
            ),
            "detail_status": detail_status,
        },
    )


class PostingDescriptionFetcherTests(unittest.TestCase):
    def test_fetches_full_workday_description_on_demand(self) -> None:
        detail_url = (
            "https://itc.wd3.myworkdayjobs.com/wday/cxs/itc/External/"
            "job/Karnataka-India/Data-Engineer-I_R-76407"
        )
        full_description = " ".join(
            [
                "Design and maintain scalable data pipelines using Python SQL and cloud services."
            ]
            * 12
        )
        http = StubHttpClient(
            {
                detail_url: response(
                    detail_url,
                    {
                        "jobPostingInfo": {
                            "title": "Data Engineer I",
                            "jobReqId": "R-76407",
                            "jobDescription": f"<div><p>{full_description}</p></div>",
                            "canApply": True,
                        }
                    },
                )
            }
        )

        result = PostingDescriptionFetcher(http).fetch(
            workday_job(
                "Data Engineer I, ITC. Karnataka, India. Full time. R-76407"
            )
        )

        self.assertTrue(result.attempted)
        self.assertTrue(result.refreshed)
        self.assertEqual("workday_detail", result.method)
        self.assertIn("scalable data pipelines", result.description)
        self.assertEqual([detail_url], http.calls)

    def test_fetches_full_successfactors_description_on_demand(self) -> None:
        page_url = (
            "https://example.jobs.hr.cloud.sap/job/Portland/"
            "Data-Engineer/123-en_US"
        )
        robots_url = "https://example.jobs.hr.cloud.sap/robots.txt"
        full_description = " ".join(
            ["Build governed data platforms using Python SQL and cloud services."] * 15
        )
        html = f"""<script type=\"application/ld+json\">{{
          "@type": "JobPosting",
          "identifier": {{"value": "123"}},
          "title": "Data Engineer",
          "description": "<p>{full_description}</p>",
          "url": "{page_url}"
        }}</script>"""
        http = StubHttpClient({
            robots_url: response(robots_url, "User-agent: *\nAllow: /\n", content_type="text/plain"),
            page_url: response(page_url, html, content_type="text/html"),
        })
        job = DiscoveredJob(
            id=discovered_job_id("owner-1", "sf-source", "123"),
            owner_id="owner-1", source_id="sf-source", external_job_id="123",
            company="Example", title="Data Engineer",
            description="Data Engineer. Portland, Oregon.", canonical_url=page_url,
            source_type=JobSourceType.SUCCESSFACTORS,
            metadata={"detail_status": "deferred"},
        )

        result = PostingDescriptionFetcher(http).fetch(job)

        self.assertTrue(result.refreshed)
        self.assertEqual("successfactors_detail", result.method)
        self.assertIn("governed data platforms", result.description)
        self.assertEqual([robots_url, page_url], http.calls)

    def test_fetches_full_oracle_cloud_hcm_description_on_demand(self) -> None:
        page_url = (
            "https://example.fa.us2.oraclecloud.com/"
            "hcmUI/CandidateExperience/en/sites/CX_1/job/REQ-42"
        )
        robots_url = "https://example.fa.us2.oraclecloud.com/robots.txt"
        target = parse_oracle_cloud_hcm_careers_url(page_url)
        api_detail = _api_detail_url(target, "REQ-42")
        full_description = " ".join(
            [
                "Build governed data platforms using Python SQL and cloud services."
            ]
            * 15
        )
        http = StubHttpClient({
            robots_url: response(
                robots_url,
                "User-agent: *\nAllow: /\n",
                content_type="text/plain",
            ),
            api_detail: response(
                api_detail,
                {
                    "items": [
                        {
                            "Id": "REQ-42",
                            "Title": "Data Engineer",
                            "ExternalDescriptionStr": f"<p>{full_description}</p>",
                            "PrimaryLocation": "Portland, Oregon",
                        }
                    ]
                },
                content_type="application/vnd.oracle.adf.resourcecollection+json",
            ),
        })
        job = DiscoveredJob(
            id=discovered_job_id("owner-1", "oracle-source", "REQ-42"),
            owner_id="owner-1",
            source_id="oracle-source",
            external_job_id="REQ-42",
            company="Example",
            title="Data Engineer",
            description="Data Engineer. Portland, Oregon.",
            canonical_url=page_url,
            source_type=JobSourceType.ORACLE_CLOUD_HCM,
            metadata={"detail_status": "deferred"},
        )

        result = PostingDescriptionFetcher(http).fetch(job)

        self.assertTrue(result.refreshed)
        self.assertEqual("oracle_cloud_hcm_detail", result.method)
        self.assertIn("governed data platforms", result.description)
        self.assertEqual([robots_url, api_detail], http.calls)

    def test_fetches_full_icims_description_on_demand(self) -> None:
        page_url = "https://careers-example.icims.com/jobs/47190/data-engineer/job"
        robots_url = "https://careers-example.icims.com/robots.txt"
        full_description = " ".join(
            ["Build governed data platforms using Python SQL and cloud services."] * 15
        )
        html = f"""<script type="application/ld+json">{{
          "@type": "JobPosting",
          "identifier": {{"value": "REQ-47190"}},
          "title": "Data Engineer",
          "description": "<p>{full_description}</p>",
          "url": "{page_url}"
        }}</script>"""
        http = StubHttpClient({
            robots_url: response(
                robots_url,
                "User-agent: *\nAllow: /\n",
                content_type="text/plain",
            ),
            page_url: response(page_url, html, content_type="text/html"),
        })
        job = DiscoveredJob(
            id=discovered_job_id("owner-1", "icims-source", "REQ-47190"),
            owner_id="owner-1",
            source_id="icims-source",
            external_job_id="REQ-47190",
            company="Example",
            title="Data Engineer",
            description="Data Engineer. Portland, Oregon.",
            canonical_url=page_url,
            source_type=JobSourceType.ICIMS,
            metadata={"detail_status": "deferred"},
        )

        result = PostingDescriptionFetcher(http).fetch(job)

        self.assertTrue(result.refreshed)
        self.assertEqual("icims_detail", result.method)
        self.assertIn("governed data platforms", result.description)
        self.assertEqual([robots_url, page_url], http.calls)

    def test_skips_network_when_stored_description_is_already_complete(self) -> None:
        full_description = " ".join(["Complete verified responsibility text"] * 100)
        http = StubHttpClient({})

        result = PostingDescriptionFetcher(http).fetch(
            workday_job(full_description, detail_status="complete")
        )

        self.assertFalse(result.attempted)
        self.assertFalse(result.refreshed)
        self.assertEqual(full_description, result.description)
        self.assertEqual([], http.calls)

    def test_uses_same_posting_page_jsonld_for_non_workday_summary(self) -> None:
        page_url = "https://careers.example.com/jobs/data-engineer"
        robots_url = "https://careers.example.com/robots.txt"
        full_description = " ".join(
            ["Build reliable data platforms and governed analytics products."] * 15
        )
        html = f"""
        <html><head><script type="application/ld+json">
        {{
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "identifier": {{"value": "job-1"}},
          "title": "Data Engineer",
          "description": "<p>{full_description}</p>",
          "hiringOrganization": {{"name": "Example"}},
          "url": "{page_url}"
        }}
        </script></head></html>
        """
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: ReuniaJobBot\nAllow: /jobs\n",
                    content_type="text/plain",
                ),
                page_url: response(page_url, html, content_type="text/html"),
            }
        )
        job = DiscoveredJob(
            id=discovered_job_id("owner-1", "source-1", "job-1"),
            owner_id="owner-1",
            source_id="source-1",
            external_job_id="job-1",
            company="Example",
            title="Data Engineer",
            description="Data Engineer. Portland, Oregon. Full time.",
            canonical_url=page_url,
            source_type=JobSourceType.GENERIC_JSONLD,
        )

        result = PostingDescriptionFetcher(http).fetch(job)

        self.assertTrue(result.refreshed)
        self.assertEqual("posting_page_jsonld", result.method)
        self.assertIn("governed analytics", result.description)
        self.assertEqual([robots_url, page_url], http.calls)


if __name__ == "__main__":
    unittest.main()
