from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_discovery.deduplication import deduplicate_jobs
from job_discovery.models import (
    CompanySource,
    DiscoveredJob,
    JobSourceType,
    WorkplaceType,
    discovered_job_id,
)
from job_discovery.ranking import CandidateJobProfile
from job_discovery.service import JobDiscoveryService
from job_discovery.sources.base import RobotsDeniedError
from job_discovery.storage import InMemoryDiscoveryStore, JsonFileDiscoveryStore
from products.resume_taylor.resume_tailor.models import JobAnalysis, JobRequirement


class StaticAdapter:
    def __init__(self, jobs: list[DiscoveredJob]) -> None:
        self.jobs = jobs

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        return list(self.jobs)


class FailedAdapter:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        raise self.error


class RequirementAnalyzer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def analyze_job(self, job_description: str, stated_title: str = "") -> JobAnalysis:
        self.calls.append((job_description, stated_title))
        requirements = [
            JobRequirement(
                id="sql",
                category="technical_skill",
                priority="critical",
                requirement="SQL",
                keywords=["SQL"],
            )
        ]
        if "snowflake" in job_description.casefold():
            requirements.append(
                JobRequirement(
                    id="snowflake",
                    category="technical_skill",
                    priority="important",
                    requirement="Direct Snowflake experience",
                    keywords=["Snowflake"],
                )
            )
        return JobAnalysis(
            target_title=stated_title,
            target_company="Example Bank",
            requirements=requirements,
        )


def source(
    source_id: str = "source-greenhouse",
    source_type: JobSourceType = JobSourceType.GREENHOUSE,
) -> CompanySource:
    return CompanySource(
        id=source_id,
        owner_id="owner-1",
        company_name="Example Bank",
        careers_url="https://jobs.example.com",
        source_type=source_type,
        source_identifier="examplebank",
        filters={"min_request_interval_seconds": 0},
    )


def job(
    external_id: str,
    *,
    source_id: str = "source-greenhouse",
    source_type: JobSourceType = JobSourceType.GREENHOUSE,
    description: str = "Build regulated data platforms with SQL.",
    canonical_url: str | None = None,
) -> DiscoveredJob:
    return DiscoveredJob(
        id=discovered_job_id("owner-1", source_id, external_id),
        owner_id="owner-1",
        source_id=source_id,
        external_job_id=external_id,
        company="Example Bank",
        title="Senior Data Platform Engineer",
        location="Portland, OR",
        workplace_type=WorkplaceType.HYBRID,
        employment_type="Full-time",
        description=description,
        canonical_url=canonical_url or f"https://jobs.example.com/{external_id}",
        posted_at="2026-07-29T00:00:00+00:00",
        first_seen_at="2026-07-30T17:00:00+00:00",
        last_seen_at="2026-07-30T17:00:00+00:00",
        source_type=source_type,
        skills=("SQL",),
    )


class RequiredJobDiscoveryBehaviorTests(unittest.TestCase):
    def test_duplicate_postings_merge_by_source_url_and_content_fingerprint(self) -> None:
        exact_duplicate = job(
            "one",
            description="Build regulated data platforms with SQL and Python.",
        )
        same_url = job(
            "lever-one",
            source_id="source-lever",
            source_type=JobSourceType.LEVER,
            description="Build regulated data platforms with SQL and Python.",
            canonical_url="https://jobs.example.com/one?utm_source=lever",
        )
        same_content = job(
            "ashby-one",
            source_id="source-ashby",
            source_type=JobSourceType.ASHBY,
            description="Build regulated data platforms with SQL and Python.",
            canonical_url="https://jobs.example.com/alternate-one",
        )

        merged = deduplicate_jobs([job("one"), exact_duplicate, same_url, same_content])

        self.assertEqual(1, len(merged))
        self.assertIn("Python", merged[0].description)

    def test_changed_descriptions_rescore_while_unchanged_jobs_reuse_cache(self) -> None:
        analyzer = RequirementAnalyzer()
        adapter = StaticAdapter([job("one")])
        store = InMemoryDiscoveryStore(
            clock=lambda: "2026-07-30T18:00:00+00:00"
        )
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: adapter},
            store=store,
            job_analyzer=analyzer,
        )
        profile = CandidateJobProfile(
            target_titles=("Data Platform Engineer",),
            verified_skills=("SQL",),
        )

        first = service.discover([source()], candidate_profile=profile)
        unchanged = service.discover([source()], candidate_profile=profile)
        adapter.jobs = [
            job(
                "one",
                description="Build regulated data platforms with SQL and Snowflake.",
            )
        ]
        changed = service.discover([source()], candidate_profile=profile)

        self.assertEqual(2, len(analyzer.calls))
        self.assertFalse(first.ranked_jobs[0].cache_hit)
        self.assertTrue(unchanged.ranked_jobs[0].cache_hit)
        self.assertFalse(changed.ranked_jobs[0].cache_hit)
        self.assertNotEqual(
            first.jobs[0].description_fingerprint,
            changed.jobs[0].description_fingerprint,
        )
        self.assertIn(
            "Direct Snowflake experience",
            changed.ranked_jobs[0].fit_snapshot.unsupported_requirements,
        )

    def test_hard_eligibility_requirements_remove_blocked_jobs_before_ranking(self) -> None:
        analyzer = RequirementAnalyzer()
        blocked = job(
            "blocked",
            description=(
                "Build regulated data platforms with SQL. "
                "Visa sponsorship is not available."
            ),
        )
        eligible = job("eligible")
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([blocked, eligible])},
            job_analyzer=analyzer,
        )
        profile = CandidateJobProfile(
            target_titles=("Data Platform Engineer",),
            verified_skills=("SQL",),
            requires_sponsorship=True,
        )

        result = service.discover([source()], candidate_profile=profile)

        self.assertEqual(["eligible"], [item.job.external_job_id for item in result.ranked_jobs])
        self.assertEqual(["blocked"], [item.job.external_job_id for item in result.filtered_jobs])
        self.assertIn("sponsorship", " ".join(result.filtered_jobs[0].rejection_reasons).casefold())
        self.assertEqual(1, len(analyzer.calls))

    def test_unsupported_candidate_experience_never_becomes_a_strength(self) -> None:
        analyzer = RequirementAnalyzer()
        service = JobDiscoveryService(
            adapters={
                JobSourceType.GREENHOUSE: StaticAdapter(
                    [job("snowflake", description="SQL and Snowflake are required.")]
                )
            },
            job_analyzer=analyzer,
        )
        profile = CandidateJobProfile(
            target_titles=("Data Platform Engineer",),
            verified_skills=("SQL",),
        )

        ranked = service.discover([source()], candidate_profile=profile).ranked_jobs[0]

        supported = {match.requirement_id for match in ranked.fit_snapshot.evidence_matches}
        self.assertNotIn("snowflake", supported)
        self.assertIn(
            "Direct Snowflake experience",
            ranked.fit_snapshot.unsupported_requirements,
        )
        self.assertFalse(
            any("Snowflake" in reason and "Career Profile" in reason for reason in ranked.reasons)
        )

    def test_owner_scoping_blocks_cross_user_source_and_job_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stores = (
                InMemoryDiscoveryStore(),
                JsonFileDiscoveryStore(Path(directory) / "discovery.json"),
            )
            for store in stores:
                with self.subTest(store=type(store).__name__):
                    configured = store.put_company_source(source())
                    stored_job = store.sync_discovered_jobs(
                        configured, [job("owner-isolated")]
                    )[0]
                    self.assertIsNone(
                        store.get_company_source("owner-2", configured.id)
                    )
                    self.assertIsNone(
                        store.get_discovered_job(
                            "owner-2", configured.id, stored_job.id
                        )
                    )
                    self.assertEqual([], store.list_company_sources("owner-2"))
                    self.assertEqual([], store.list_discovered_jobs("owner-2"))

    def test_failed_or_robots_blocked_sources_do_not_fail_the_whole_scan(self) -> None:
        working_source = source("source-lever", JobSourceType.LEVER)
        failed_source = source("source-greenhouse", JobSourceType.GREENHOUSE)
        blocked_source = source("source-ashby", JobSourceType.ASHBY)
        working_job = job(
            "working",
            source_id=working_source.id,
            source_type=JobSourceType.LEVER,
        )
        service = JobDiscoveryService(
            adapters={
                JobSourceType.LEVER: StaticAdapter([working_job]),
                JobSourceType.GREENHOUSE: FailedAdapter(
                    RuntimeError("company site unavailable")
                ),
                JobSourceType.ASHBY: FailedAdapter(
                    RobotsDeniedError("robots.txt disallows this path")
                ),
            },
            job_analyzer=RequirementAnalyzer(),
        )

        result = service.discover(
            [failed_source, blocked_source, working_source],
            candidate_profile=CandidateJobProfile(verified_skills=("SQL",)),
        )

        self.assertEqual(["working"], [item.external_job_id for item in result.jobs])
        self.assertEqual(1, len(result.ranked_jobs))
        self.assertEqual(2, len(result.errors))
        messages = " ".join(error.message for error in result.errors).casefold()
        self.assertIn("unavailable", messages)
        self.assertIn("robots.txt", messages)


if __name__ == "__main__":
    unittest.main()
