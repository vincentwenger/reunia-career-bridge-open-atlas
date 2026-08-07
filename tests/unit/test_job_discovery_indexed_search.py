from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from job_discovery.sources.indexed_search import OpenAIIndexedPostingSearch


class FakeResponse:
    output_text = """[
      {
        "url":"https://jobs.hrc.pdx.edu/postings/50379?utm_source=index",
        "title":"Research Integrity Program Manager",
        "location":"Portland, OR",
        "posted_at":"2026-07-30",
        "description":"Administers research integrity programs, policy, training, and compliance reviews.",
        "is_active":true
      },
      {
        "url":"https://jobs.hrc.pdx.edu/postings/50378",
        "title":"Closed Role",
        "location":"Portland, OR",
        "posted_at":"2026-07-01",
        "description":"Old posting.",
        "is_active":false
      },
      {"url":"https://jobs.hrc.pdx.edu/postings/search","title":"Search Jobs","is_active":true},
      {"url":"https://evil.example/postings/99999","title":"Not official","is_active":true}
    ]"""

    def model_dump(self, mode="json"):
        del mode
        return {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "type": "url",
                                "url": "https://jobs.hrc.pdx.edu/postings/50380",
                            },
                            {
                                "type": "url",
                                "url": "https://sub.jobs.hrc.pdx.edu/postings/50381",
                            },
                        ]
                    },
                }
            ]
        }


class SourceOnlyResponse:
    output_text = "No JSON was produced."

    def model_dump(self, mode="json"):
        del mode
        return {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "type": "url",
                                "url": (
                                    "https://firsttechfedcareers.ttcportals.com/"
                                    "jobs/17999422-manager-enterprise-risk-management-erm"
                                ),
                            }
                        ]
                    },
                }
            ]
        }


class FakeResponses:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or FakeResponse()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FlakyResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise TimeoutError("Request timed out")
        return FakeResponse()


class EmptyThenSuccessResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(output_text="[]", model_dump=lambda mode="json": {})
        return FakeResponse()


class IndexedSearchTests(unittest.TestCase):
    def test_restricts_structured_results_and_keeps_indexed_metadata(self) -> None:
        responses = FakeResponses()
        search = OpenAIIndexedPostingSearch(
            client=SimpleNamespace(responses=responses)
        )

        hits = search.find_postings(
            company_name="Portland State University",
            host="jobs.hrc.pdx.edu",
            path_pattern=re.compile(r"/postings/\d+/?(?:\?.*)?$", re.I),
            max_results=10,
        )

        self.assertEqual(
            ["https://jobs.hrc.pdx.edu/postings/50379"],
            [hit.url for hit in hits],
        )
        self.assertEqual("Research Integrity Program Manager", hits[0].title)
        self.assertEqual("Portland, OR", hits[0].location)
        self.assertIn("research integrity", hits[0].description.casefold())
        self.assertTrue(hits[0].is_active)
        tool = responses.calls[0]["tools"][0]
        self.assertEqual(
            ["jobs.hrc.pdx.edu"], tool["filters"]["allowed_domains"]
        )
        self.assertEqual("web_search", tool["type"])
        self.assertIn("is_active", responses.calls[0]["input"])

    def test_retries_one_transient_web_search_timeout(self) -> None:
        responses = FlakyResponses()
        search = OpenAIIndexedPostingSearch(
            client=SimpleNamespace(responses=responses)
        )

        hits = search.find_postings(
            company_name="Portland State University",
            host="jobs.hrc.pdx.edu",
            path_pattern=re.compile(r"/postings/\d+/?(?:\?.*)?$", re.I),
            max_results=10,
        )

        self.assertEqual(1, len(hits))
        self.assertEqual(2, len(responses.calls))
        self.assertEqual(
            "low",
            responses.calls[0]["tools"][0]["search_context_size"],
        )
        self.assertEqual(
            "low",
            responses.calls[1]["tools"][0]["search_context_size"],
        )
        self.assertEqual("required", responses.calls[0]["tool_choice"])
        self.assertFalse(responses.calls[0]["parallel_tool_calls"])
        self.assertEqual({"effort": "minimal"}, responses.calls[0]["reasoning"])
        self.assertEqual(2, responses.calls[0]["max_tool_calls"])
        self.assertEqual(1, responses.calls[1]["max_tool_calls"])


    def test_uses_exact_indexed_listing_page_before_site_search(self) -> None:
        responses = FakeResponses()
        search = OpenAIIndexedPostingSearch(
            client=SimpleNamespace(responses=responses)
        )

        hits = search.find_postings(
            company_name="Portland State University",
            host="jobs.hrc.pdx.edu",
            path_pattern=re.compile(r"/postings/\d+/?(?:\?.*)?$", re.I),
            max_results=25,
            index_page_url="https://jobs.hrc.pdx.edu/postings/search",
        )

        self.assertEqual(1, len(hits))
        self.assertEqual(1, len(responses.calls))
        self.assertIn(
            "https://jobs.hrc.pdx.edu/postings/search",
            responses.calls[0]["input"],
        )
        self.assertIn("exact official", responses.calls[0]["input"])
        self.assertLessEqual(responses.calls[0]["timeout"], 35.0)

    def test_falls_back_to_compact_site_search_when_listing_index_is_thin(self) -> None:
        responses = EmptyThenSuccessResponses()
        search = OpenAIIndexedPostingSearch(
            client=SimpleNamespace(responses=responses)
        )

        hits = search.find_postings(
            company_name="Portland State University",
            host="jobs.hrc.pdx.edu",
            path_pattern=re.compile(r"/postings/\d+/?(?:\?.*)?$", re.I),
            max_results=10,
            index_page_url="https://jobs.hrc.pdx.edu/postings/search",
        )

        self.assertEqual(1, len(hits))
        self.assertEqual(2, len(responses.calls))
        self.assertIn("exact official", responses.calls[0]["input"])
        self.assertIn("site:jobs.hrc.pdx.edu", responses.calls[1]["input"])

    def test_old_twenty_second_setting_is_raised_to_browser_safe_budget(self) -> None:
        responses = FlakyResponses()
        search = OpenAIIndexedPostingSearch(
            client=SimpleNamespace(responses=responses)
        )

        with patch.dict(
            "os.environ",
            {
                "JOB_DISCOVERY_WEB_SEARCH_TIMEOUT_SECONDS": "20",
                "JOB_DISCOVERY_WEB_SEARCH_ATTEMPTS": "2",
            },
            clear=False,
        ):
            hits = search.find_postings(
                company_name="Portland State University",
                host="jobs.hrc.pdx.edu",
                path_pattern=re.compile(r"/postings/\d+/?(?:\?.*)?$", re.I),
                max_results=10,
                index_page_url="https://jobs.hrc.pdx.edu/postings/search",
            )

        self.assertEqual(1, len(hits))
        self.assertAlmostEqual(31.5, responses.calls[0]["timeout"])
        self.assertAlmostEqual(13.5, responses.calls[1]["timeout"])

    def test_source_annotation_fallback_derives_title_from_official_url(self) -> None:
        responses = FakeResponses(SourceOnlyResponse())
        search = OpenAIIndexedPostingSearch(
            client=SimpleNamespace(responses=responses)
        )

        hits = search.find_postings(
            company_name="First Tech Federal Credit Union",
            host="firsttechfedcareers.ttcportals.com",
            path_pattern=re.compile(r"/jobs/\d+[-/].*|/jobs/\d+$", re.I),
            max_results=10,
        )

        self.assertEqual(1, len(hits))
        self.assertEqual(
            "Manager Enterprise Risk Management ERM", hits[0].title
        )
        self.assertIsNone(hits[0].is_active)


if __name__ == "__main__":
    unittest.main()
