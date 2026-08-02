from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Mapping, Sequence

from job_discovery.models import CompanySource, JobSourceType, WorkplaceType
from job_discovery.public_catalog import public_source_key
from job_discovery.service import default_adapters
from job_discovery.sources.base import HttpResponse
from job_discovery.sources.ukg_pro import UkgProJobSource, parse_ukg_pro_careers_url


@dataclass
class StubHttpClient:
    responses: dict[str, HttpResponse]

    def __post_init__(self) -> None:
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, bytes, Mapping[str, str]]] = []

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
        self.get_calls.append(url)
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
        self.post_calls.append((url, body, dict(headers or {})))
        response = self.responses[url]
        if max_bytes is not None and len(response.body) > max_bytes:
            raise AssertionError("stub response exceeds max_bytes")
        return response


def response(url: str, payload, *, content_type: str = "application/json") -> HttpResponse:
    if isinstance(payload, bytes):
        body = payload
    elif isinstance(payload, str):
        body = payload.encode("utf-8")
    else:
        body = json.dumps(payload).encode("utf-8")
    return HttpResponse(200, {"content-type": content_type}, body, url)


class UkgProSourceTests(unittest.TestCase):
    board_url = (
        "https://recruiting2.ultipro.com/WAS1000WTB/JobBoard/"
        "cb002c76-8419-4941-9c78-d28ae4e9c89e"
    )
    opportunity_id = "5ef7c5d2-1534-4fec-842d-dbd2b4e6c5ce"

    def test_normalizes_board_and_detail_urls(self) -> None:
        board = parse_ukg_pro_careers_url(self.board_url + "/?o=postedDateDesc")
        detail = parse_ukg_pro_careers_url(
            self.board_url + "/OpportunityDetail?opportunityId=" + self.opportunity_id
        )

        self.assertEqual(self.board_url, board.listing_url)
        self.assertEqual(board, detail)
        self.assertEqual("WAS1000WTB", board.tenant)
        self.assertEqual(
            self.board_url + "/JobBoardView/LoadSearchResults", board.search_url
        )

    def test_rejects_non_ukg_and_malformed_board_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "ultipro"):
            parse_ukg_pro_careers_url("https://example.com/jobs")
        with self.assertRaisesRegex(ValueError, "JobBoard.*UUID"):
            parse_ukg_pro_careers_url(
                "https://recruiting.ultipro.com/TENANT/JobBoard/not-a-uuid"
            )
        with self.assertRaisesRegex(ValueError, "ukg_pro careers_url"):
            CompanySource(
                id="bad-ukg",
                owner_id="owner-1",
                company_name="Example",
                careers_url="https://example.com/jobs",
                source_type=JobSourceType.UKG_PRO,
                source_identifier="",
            )

    def test_fetches_public_search_results_and_full_detail(self) -> None:
        target = parse_ukg_pro_careers_url(self.board_url)
        robots_url = "https://recruiting2.ultipro.com/robots.txt"
        detail_url = target.detail_url(self.opportunity_id)
        description = " ".join(
            ["Build secure banking data platforms with Python SQL and governance."] * 20
        )
        http = StubHttpClient(
            {
                robots_url: response(
                    robots_url,
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                ),
                target.search_url: response(
                    target.search_url,
                    {
                        "opportunities": [
                            {
                                "Id": self.opportunity_id,
                                "Title": "Senior Data Platform Engineer",
                                "RequisitionNumber": "SENIO006248",
                                "FullTime": True,
                                "JobCategoryName": "Technology",
                                "Locations": [
                                    {
                                        "City": "Spokane",
                                        "State": "Washington",
                                        "Country": "USA",
                                    }
                                ],
                                "PostedDate": "2026-07-30",
                                "BriefDescription": "Build banking data systems.",
                                "WorkplaceType": "Hybrid",
                            }
                        ],
                        "totalCount": 1,
                    },
                ),
                detail_url: response(
                    detail_url,
                    f'''<html><body><script type="application/ld+json">{{
                      "@context":"https://schema.org",
                      "@type":"JobPosting",
                      "identifier":{{"value":"SENIO006248"}},
                      "title":"Senior Data Platform Engineer",
                      "description":"<p>{description}</p>",
                      "employmentType":"FULL_TIME",
                      "jobLocationType":"HYBRID",
                      "jobLocation":{{"address":{{"addressLocality":"Spokane","addressRegion":"Washington","addressCountry":"US"}}}},
                      "datePosted":"2026-07-30",
                      "url":"{detail_url}"
                    }}</script></body></html>''',
                    content_type="text/html",
                ),
            }
        )
        source = CompanySource(
            id="washington-trust-ukg",
            owner_id="owner-1",
            company_name="Washington Trust Bank",
            careers_url=self.board_url,
            source_type=JobSourceType.UKG_PRO,
            source_identifier="",
            filters={
                "min_request_interval_seconds": 0,
                "detail_fetch_limit": 1,
            },
        )

        jobs = UkgProJobSource(http).fetch_jobs(source)

        self.assertEqual(1, len(jobs))
        job = jobs[0]
        self.assertEqual("Senior Data Platform Engineer", job.title)
        self.assertEqual("Full-time", job.employment_type)
        self.assertEqual(WorkplaceType.HYBRID, job.workplace_type)
        self.assertEqual("Spokane, Washington, USA", job.location)
        self.assertIn("Python SQL", job.description)
        self.assertEqual("complete", job.metadata["detail_status"])
        self.assertEqual("SENIO006248", job.metadata["requisition_number"])
        self.assertEqual(1, len(http.post_calls))
        post_url, post_body, post_headers = http.post_calls[0]
        self.assertEqual(target.search_url, post_url)
        self.assertEqual(
            {"opportunitySearch": {"Top": 50, "Skip": 0}},
            json.loads(post_body.decode("utf-8")),
        )
        self.assertEqual("XMLHttpRequest", post_headers["X-Requested-With"])

    def test_equivalent_board_and_detail_urls_share_catalog_identity(self) -> None:
        board = CompanySource(
            id="ukg-board",
            owner_id="owner-1",
            company_name="Washington Trust Bank",
            careers_url=self.board_url,
            source_type=JobSourceType.UKG_PRO,
            source_identifier="",
        )
        detail = CompanySource(
            id="ukg-detail",
            owner_id="owner-1",
            company_name="Washington Trust Bank",
            careers_url=(
                self.board_url
                + "/OpportunityDetail?opportunityId="
                + self.opportunity_id
            ),
            source_type=JobSourceType.UKG_PRO,
            source_identifier="",
        )
        self.assertEqual(public_source_key(board), public_source_key(detail))

    def test_default_service_registers_ukg_adapter(self) -> None:
        self.assertIn(JobSourceType.UKG_PRO, default_adapters())


if __name__ == "__main__":
    unittest.main()
