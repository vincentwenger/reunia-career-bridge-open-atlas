from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from job_discovery.deduplication import deduplicate_jobs
from job_discovery.models import (
    CompanySource,
    DiscoveredJob,
    DiscoveryJobDisposition,
    DiscoveryJobState,
    DiscoverySearchPreferences,
    JobSourceType,
    WorkplaceType,
    discovered_job_id,
)
from job_discovery.ranking import CandidateJobProfile, evaluate_stage_one
from job_discovery.service import (
    JobDiscoveryService,
    PUBLIC_COVERAGE_DESCRIPTION,
    _default_analyzer_factory,
)
from products.resume_taylor.resume_tailor.models import JobAnalysis, JobRequirement
from job_discovery.storage import InMemoryDiscoveryStore, JsonFileDiscoveryStore


class StaticAdapter:
    def __init__(self, jobs):
        self.jobs = jobs

    def fetch_jobs(self, source):
        return list(self.jobs)


class CountingAnalyzer:
    def __init__(self):
        self.calls = []

    def analyze_job(self, job_description, stated_title=""):
        self.calls.append((job_description, stated_title))
        return JobAnalysis(
            target_title=stated_title or "Software Engineer",
            target_company="Acme",
            requirements=[
                JobRequirement(
                    id="python",
                    category="technical_skill",
                    priority="critical",
                    requirement="Python",
                    keywords=["Python"],
                ),
                JobRequirement(
                    id="aws",
                    category="technical_skill",
                    priority="important",
                    requirement="AWS",
                    keywords=["AWS"],
                ),
            ],
        )


class FailingAdapter:
    def fetch_jobs(self, source):
        raise RuntimeError("source temporarily unavailable")


def source(**overrides):
    values = {
        "id": "source-a",
        "owner_id": "owner-1",
        "company_name": "Acme",
        "careers_url": "https://jobs.example.com",
        "source_type": JobSourceType.GREENHOUSE,
        "source_identifier": "acme",
    }
    values.update(overrides)
    return CompanySource(**values)


def job(**overrides):
    values = {
        "id": discovered_job_id("owner-1", "source-a", "1"),
        "owner_id": "owner-1",
        "source_id": "source-a",
        "external_job_id": "1",
        "company": "Acme",
        "title": "Senior Python Engineer",
        "canonical_url": "https://jobs.example.com/1",
        "description": "Build services with Python and AWS.",
        "location": "Portland, OR",
        "locations": ("Portland, OR",),
        "workplace_type": WorkplaceType.HYBRID,
        "employment_type": "Full-time",
        "skills": ("Python", "AWS"),
        "source_type": JobSourceType.GREENHOUSE,
        "posted_at": "2026-07-29T00:00:00+00:00",
        "first_seen_at": "2026-07-30T17:00:00+00:00",
        "last_seen_at": "2026-07-30T17:00:00+00:00",
    }
    values.update(overrides)
    if "external_job_id" in overrides and "id" not in overrides:
        values["id"] = discovered_job_id(values["owner_id"], values["source_id"], values["external_job_id"])
    return DiscoveredJob(**values)


class JobDiscoveryServiceTests(unittest.TestCase):

    def test_default_analyzer_uses_lowest_cost_job_discovery_model(self) -> None:
        module_name = "products.resume_taylor.resume_tailor.ai"
        fake_ai_module = types.ModuleType(module_name)
        resume_ai = Mock()
        fake_ai_module.ResumeAI = resume_ai
        with patch.dict(sys.modules, {module_name: fake_ai_module}):
            with patch.dict(os.environ, {"JOB_DISCOVERY_AI_MODEL": ""}, clear=False):
                analyzer = _default_analyzer_factory("owner-1")

        self.assertIs(analyzer, resume_ai.return_value)
        resume_ai.assert_called_once_with(
            "gpt-5-nano",
            reasoning_effort="minimal",
            user_id="owner-1",
            max_attempts=1,
            request_timeout_seconds=20.0,
            max_output_tokens_by_operation={"analyze_job": 4800},
        )

    def test_job_discovery_model_can_be_explicitly_overridden(self) -> None:
        module_name = "products.resume_taylor.resume_tailor.ai"
        fake_ai_module = types.ModuleType(module_name)
        resume_ai = Mock()
        fake_ai_module.ResumeAI = resume_ai
        with patch.dict(sys.modules, {module_name: fake_ai_module}):
            with patch.dict(
                os.environ,
                {"JOB_DISCOVERY_AI_MODEL": "custom-assessment-model"},
                clear=False,
            ):
                _default_analyzer_factory("owner-2")

        resume_ai.assert_called_once_with(
            "custom-assessment-model",
            reasoning_effort="minimal",
            user_id="owner-2",
            max_attempts=1,
            request_timeout_seconds=20.0,
            max_output_tokens_by_operation={"analyze_job": 4800},
        )

    def test_job_discovery_timeout_is_configurable_but_gateway_bounded(self) -> None:
        module_name = "products.resume_taylor.resume_tailor.ai"
        fake_ai_module = types.ModuleType(module_name)
        resume_ai = Mock()
        fake_ai_module.ResumeAI = resume_ai
        with patch.dict(sys.modules, {module_name: fake_ai_module}):
            with patch.dict(
                os.environ,
                {
                    "JOB_DISCOVERY_AI_MODEL": "gpt-5-nano",
                    "JOB_DISCOVERY_AI_TIMEOUT_SECONDS": "90",
                },
                clear=False,
            ):
                _default_analyzer_factory("owner-timeout")

        resume_ai.assert_called_once_with(
            "gpt-5-nano",
            reasoning_effort="minimal",
            user_id="owner-timeout",
            max_attempts=1,
            request_timeout_seconds=25.0,
            max_output_tokens_by_operation={"analyze_job": 4800},
        )

    def test_job_discovery_reasoning_and_output_budget_are_configurable(self) -> None:
        module_name = "products.resume_taylor.resume_tailor.ai"
        fake_ai_module = types.ModuleType(module_name)
        resume_ai = Mock()
        fake_ai_module.ResumeAI = resume_ai
        with patch.dict(sys.modules, {module_name: fake_ai_module}):
            with patch.dict(
                os.environ,
                {
                    "JOB_DISCOVERY_AI_MODEL": "gpt-5-nano",
                    "JOB_DISCOVERY_AI_REASONING_EFFORT": "low",
                    "JOB_DISCOVERY_AI_MAX_OUTPUT_TOKENS": "6200",
                },
                clear=False,
            ):
                _default_analyzer_factory("owner-budget")

        resume_ai.assert_called_once_with(
            "gpt-5-nano",
            reasoning_effort="low",
            user_id="owner-budget",
            max_attempts=1,
            request_timeout_seconds=20.0,
            max_output_tokens_by_operation={"analyze_job": 6200},
        )

    def test_interactive_refresh_reuses_cache_without_starting_new_analysis(self) -> None:
        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        analyzer = CountingAnalyzer()
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([job()])},
            store=store,
            job_analyzer=analyzer,
        )
        profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python", "AWS"),
        )

        first = service.discover(
            [source()], candidate_profile=profile, analyze_new_jobs=False
        )
        self.assertEqual(1, len(first.jobs))
        self.assertEqual(0, len(first.ranked_jobs))
        self.assertEqual([], analyzer.calls)

        service.discover([source()], candidate_profile=profile)
        cached = service.discover(
            [source()], candidate_profile=profile, analyze_new_jobs=False
        )
        self.assertEqual(1, len(cached.ranked_jobs))
        self.assertEqual(1, len(analyzer.calls))

    def test_assesses_materialized_shared_job_without_fetching_source(self) -> None:
        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        stored_job = store.sync_discovered_jobs(source(), [job()])[0]
        analyzer = CountingAnalyzer()
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: FailingAdapter()},
            store=store,
            job_analyzer=analyzer,
        )
        profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python", "AWS"),
        )

        result = service.assess_existing_jobs([stored_job], profile)

        self.assertEqual(1, len(result.ranked_jobs))
        self.assertEqual(1, len(analyzer.calls))
        self.assertEqual(stored_job.id, result.ranked_jobs[0].job.id)

    def test_collects_deduplicates_ranks_and_persists_discovery_records(self) -> None:
        duplicate = job(
            external_job_id="2",
            canonical_url="https://jobs.example.com/1?utm_source=x",
            description="",
        )
        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        analyzer = CountingAnalyzer()
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([job(), duplicate])},
            store=store,
            job_analyzer=analyzer,
        )
        profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python", "SQL", "AWS"),
            preferred_locations=("Portland",),
        )

        result = service.discover([source()], candidate_profile=profile)

        self.assertEqual(1, len(result.jobs))
        self.assertEqual(1, len(result.ranked_jobs))
        self.assertGreater(result.ranked_jobs[0].score, 50)
        self.assertEqual(1, len(analyzer.calls))
        self.assertEqual(
            1,
            len(store.list_discovered_jobs("owner-1", source_id="source-a")),
        )
        snapshot = result.ranked_jobs[0].fit_snapshot
        self.assertEqual(
            snapshot,
            store.get_fit_snapshot("owner-1", snapshot.job_id, snapshot.profile_fingerprint),
        )
        self.assertEqual("2026-07-30T18:00:00+00:00", result.jobs[0].last_seen_at)
        self.assertEqual("2026-07-30T18:00:00+00:00", result.jobs[0].first_seen_at)

    def test_ignored_job_is_hidden_before_any_ai_call(self) -> None:
        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        stored_job = store.sync_discovered_jobs(source(), [job()])[0]
        store.put_job_state(
            DiscoveryJobState(
                owner_id=stored_job.owner_id,
                source_id=stored_job.source_id,
                job_id=stored_job.id,
                disposition=DiscoveryJobDisposition.IGNORED,
                updated_at="2026-07-30T18:01:00+00:00",
            )
        )
        analyzer = CountingAnalyzer()
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([job()])},
            store=store,
            job_analyzer=analyzer,
        )
        result = service.discover(
            [source()],
            candidate_profile=CandidateJobProfile(
                target_titles=("Python Engineer",),
                verified_skills=("Python", "AWS"),
            ),
        )

        self.assertEqual(0, len(analyzer.calls))
        self.assertEqual(0, len(result.ranked_jobs))
        self.assertEqual(1, len(result.filtered_jobs))
        self.assertIn("Ignored by user", result.filtered_jobs[0].rejection_reasons)

    def test_missing_posting_becomes_inactive_without_becoming_an_application(self) -> None:
        responses = iter([[job()], [], [], []])

        class ChangingAdapter:
            def fetch_jobs(self, configured_source):
                return next(responses)

        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: ChangingAdapter()},
            store=store,
        )

        service.discover([source()])
        service.discover([source()])
        after_one_miss = store.list_discovered_jobs("owner-1", active_only=True)
        self.assertEqual(1, len(after_one_miss))
        self.assertEqual(1, after_one_miss[0].missed_scan_count)

        service.discover([source()])
        self.assertEqual(1, len(store.list_discovered_jobs("owner-1", active_only=True)))

        service.discover([source()])
        self.assertEqual([], store.list_discovered_jobs("owner-1", active_only=True))
        historical = store.list_discovered_jobs("owner-1", active_only=False)
        self.assertEqual(1, len(historical))
        self.assertFalse(historical[0].active)
        self.assertFalse(hasattr(store, "create_application"))
        self.assertFalse(hasattr(store, "create"))

    def test_source_failures_are_isolated(self) -> None:
        service = JobDiscoveryService(adapters={JobSourceType.LEVER: FailingAdapter()})
        configured = source(
            id="bad",
            company_name="Bad Co",
            source_type=JobSourceType.LEVER,
            source_identifier="bad",
        )

        result = service.discover([configured])

        self.assertEqual((), result.jobs)
        self.assertEqual(1, len(result.errors))
        self.assertIn("temporarily unavailable", result.errors[0].message)

    def test_default_posting_age_filter_applies_to_every_source_type(self) -> None:
        source_values = {
            JobSourceType.GREENHOUSE: ("https://boards.greenhouse.io/acme", "acme"),
            JobSourceType.LEVER: ("https://jobs.lever.co/acme", "acme"),
            JobSourceType.ASHBY: ("https://jobs.ashbyhq.com/acme", "acme"),
            JobSourceType.WORKDAY: (
                "https://acme.wd1.myworkdayjobs.com/en-US/External",
                "External",
            ),
            JobSourceType.SUCCESSFACTORS: (
                "https://acme.jobs.hr.cloud.sap/",
                "",
            ),
            JobSourceType.ORACLE_CLOUD_HCM: (
                "https://acme.fa.us2.oraclecloud.com/"
                "hcmUI/CandidateExperience/en/sites/CX_1/jobs",
                "",
            ),
            JobSourceType.ICIMS: (
                "https://careers-acme.icims.com/jobs/search",
                "",
            ),
            JobSourceType.GENERIC_JSONLD: ("https://careers.acme.example/jobs", ""),
        }
        for source_type, (careers_url, identifier) in source_values.items():
            with self.subTest(source_type=source_type.value):
                configured = source(
                    id=f"source-{source_type.value}",
                    careers_url=careers_url,
                    source_type=source_type,
                    source_identifier=identifier,
                )

                def configured_job(external_id, **values):
                    return job(
                        id=discovered_job_id(
                            "owner-1", configured.id, external_id
                        ),
                        source_id=configured.id,
                        external_job_id=external_id,
                        source_type=source_type,
                        canonical_url=f"https://jobs.example.com/{source_type.value}/{external_id}",
                        description=f"Unique {source_type.value} description {external_id}",
                        **values,
                    )

                postings = [
                    configured_job(
                        "fresh", posted_at="2026-07-20T18:00:00+00:00"
                    ),
                    configured_job(
                        "boundary", posted_at="2026-06-30T18:00:00+00:00"
                    ),
                    configured_job(
                        "old", posted_at="2026-06-29T17:59:59+00:00"
                    ),
                    configured_job("unknown", posted_at=""),
                    configured_job(
                        "future-close",
                        posted_at="2026-05-01T18:00:00+00:00",
                        valid_through="2026-08-15T23:59:59+00:00",
                    ),
                ]
                result = JobDiscoveryService(
                    adapters={source_type: StaticAdapter(postings)},
                    store=InMemoryDiscoveryStore(
                        clock=lambda: "2026-07-30T18:00:00+00:00"
                    ),
                    ranking_clock=lambda: "2026-07-30T18:00:00+00:00",
                ).discover([configured])

                self.assertEqual(4, len(result.jobs))
                self.assertEqual(1, len(result.age_filtered_jobs))
                self.assertEqual(
                    "old", result.age_filtered_jobs[0].external_job_id
                )

    def test_posting_age_filter_can_be_disabled_per_owner(self) -> None:
        store = InMemoryDiscoveryStore(
            clock=lambda: "2026-07-30T18:00:00+00:00"
        )
        store.put_search_preferences(
            DiscoverySearchPreferences(
                owner_id="owner-1", maximum_posting_age_days=None
            )
        )
        result = JobDiscoveryService(
            adapters={
                JobSourceType.GREENHOUSE: StaticAdapter(
                    [job(posted_at="2025-01-01T00:00:00+00:00")]
                )
            },
            store=store,
            ranking_clock=lambda: "2026-07-30T18:00:00+00:00",
        ).discover([source()])

        self.assertEqual(1, len(result.jobs))
        self.assertEqual(0, len(result.age_filtered_jobs))

    def test_metadata_update_date_is_used_when_posted_date_is_missing(self) -> None:
        old_greenhouse_job = job(
            posted_at="",
            metadata={"updated_at": "2026-05-01T00:00:00+00:00"},
        )
        result = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([old_greenhouse_job])},
            store=InMemoryDiscoveryStore(
                clock=lambda: "2026-07-30T18:00:00+00:00"
            ),
            ranking_clock=lambda: "2026-07-30T18:00:00+00:00",
        ).discover([source()])

        self.assertEqual(0, len(result.jobs))
        self.assertEqual(1, len(result.age_filtered_jobs))

    def test_public_coverage_wording_is_bounded(self) -> None:
        self.assertIn("publicly accessible", PUBLIC_COVERAGE_DESCRIPTION)
        self.assertIn("cannot be guaranteed", PUBLIC_COVERAGE_DESCRIPTION)
        self.assertNotIn("every job", PUBLIC_COVERAGE_DESCRIPTION.casefold())

    def test_stage_one_hides_mandatory_failures_before_ai(self) -> None:
        blocked = job(
            title="Sales Manager",
            workplace_type=WorkplaceType.ONSITE,
            location="New York, NY",
            locations=("New York, NY",),
            employment_type="Contract",
            salary_max=90000,
            salary_interval="year",
            description="Sales role. No visa sponsorship is available.",
        )
        analyzer = CountingAnalyzer()
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([blocked])},
            job_analyzer=analyzer,
        )
        profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python",),
            preferred_locations=("Portland",),
            accepted_workplace_types=(WorkplaceType.HYBRID,),
            preferred_employment_types=("Full-time",),
            minimum_salary=120000,
            excluded_terms=("sales",),
            require_title_match=True,
            require_location_match=True,
            require_workplace_match=True,
            require_employment_type_match=True,
            requires_sponsorship=True,
        )

        result = service.discover([source()], candidate_profile=profile)

        self.assertEqual(0, len(result.ranked_jobs))
        self.assertEqual(1, len(result.filtered_jobs))
        self.assertEqual([], analyzer.calls)
        rejection_text = " ".join(result.filtered_jobs[0].rejection_reasons).casefold()
        self.assertIn("excluded term", rejection_text)
        self.assertIn("sponsorship", rejection_text)
        self.assertIn("salary", rejection_text)

    def test_title_exclusions_only_match_the_job_title(self) -> None:
        profile = CandidateJobProfile(excluded_title_terms=("sales",))

        title_match = evaluate_stage_one(
            job(title="Regional Sales Manager", description="Lead a customer team."),
            profile,
        )
        description_only = evaluate_stage_one(
            job(
                title="Senior Python Engineer",
                description="Partner closely with the sales organization.",
            ),
            profile,
        )

        self.assertFalse(title_match.passed)
        self.assertIn(
            "excluded job-title term",
            " ".join(title_match.rejection_reasons).casefold(),
        )
        self.assertTrue(description_only.passed)
        self.assertEqual((), description_only.rejection_reasons)

        duplicate_configuration = evaluate_stage_one(
            job(title="Regional Sales Manager"),
            CandidateJobProfile(
                excluded_title_terms=("sales",),
                excluded_terms=("sales",),
            ),
        )
        self.assertEqual(1, len(duplicate_configuration.rejection_reasons))

    def test_two_stage_cache_uses_description_and_profile_fingerprints(self) -> None:
        analyzer = CountingAnalyzer()
        store = InMemoryDiscoveryStore(clock=lambda: "2026-07-30T18:00:00+00:00")
        adapter = StaticAdapter([job()])
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: adapter},
            store=store,
            job_analyzer=analyzer,
        )
        first_profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python", "AWS"),
        )

        first = service.discover([source()], candidate_profile=first_profile)
        second = service.discover([source()], candidate_profile=first_profile)

        self.assertEqual(1, len(analyzer.calls))
        self.assertFalse(first.ranked_jobs[0].cache_hit)
        self.assertTrue(second.ranked_jobs[0].cache_hit)
        snapshot = store.get_fit_snapshot(
            "owner-1",
            first.jobs[0].id,
            first_profile.fingerprint,
            first.jobs[0].description_fingerprint,
        )
        self.assertIsNotNone(snapshot)

        changed_profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python",),
        )
        third = service.discover([source()], candidate_profile=changed_profile)
        self.assertEqual(1, len(analyzer.calls), "profile changes must reuse cached JobAnalysis")
        self.assertFalse(third.ranked_jobs[0].cache_hit)

        adapter.jobs = [job(description="Build services with Python, AWS, and Kubernetes.")]
        fourth = service.discover([source()], candidate_profile=changed_profile)
        self.assertEqual(2, len(analyzer.calls), "changed descriptions must be reanalyzed")
        self.assertNotEqual(
            third.jobs[0].description_fingerprint,
            fourth.jobs[0].description_fingerprint,
        )

    def test_job_fit_preference_fit_and_search_priority_are_separate(self) -> None:
        preferred = job(
            external_job_id="preferred",
            canonical_url="https://jobs.example.com/preferred",
            posted_at="2026-07-30T12:00:00+00:00",
        )
        less_preferred = job(
            external_job_id="less-preferred",
            canonical_url="https://jobs.example.com/less-preferred",
            title="Platform Developer",
            location="Miami, FL",
            locations=("Miami, FL",),
            posted_at="2026-05-01T12:00:00+00:00",
        )
        store = InMemoryDiscoveryStore(
            clock=lambda: "2026-07-30T18:00:00+00:00"
        )
        store.put_search_preferences(
            DiscoverySearchPreferences(
                owner_id="owner-1", maximum_posting_age_days=None
            )
        )
        service = JobDiscoveryService(
            adapters={
                JobSourceType.GREENHOUSE: StaticAdapter([less_preferred, preferred])
            },
            store=store,
            job_analyzer=CountingAnalyzer(),
            ranking_clock=lambda: "2026-07-30T18:00:00+00:00",
        )
        profile = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python", "AWS"),
            preferred_locations=("Portland",),
            accepted_workplace_types=(WorkplaceType.HYBRID,),
        )

        result = service.discover([source()], candidate_profile=profile)

        self.assertEqual(2, len(result.ranked_jobs))
        first, second = result.ranked_jobs
        self.assertEqual(first.fit_score, second.fit_score)
        self.assertGreater(first.preference_score, second.preference_score)
        self.assertGreater(first.freshness_score, second.freshness_score)
        self.assertGreater(first.search_priority, second.search_priority)
        self.assertEqual("preferred", first.job.external_job_id)
        self.assertEqual(first.fit_score, first.fit_snapshot.fit_score)
        self.assertFalse(hasattr(first.fit_snapshot, "preference_score"))
        self.assertFalse(hasattr(first.fit_snapshot, "search_priority"))
        self.assertEqual(
            round(
                first.fit_score * 0.70
                + first.preference_score * 0.20
                + first.freshness_score * 0.10,
                2,
            ),
            first.search_priority,
        )
        self.assertEqual(
            "70% Job Fit + 20% Preference Fit + 10% Posting Freshness",
            first.priority_formula,
        )

    def test_preference_changes_reuse_evidence_fit_snapshot(self) -> None:
        analyzer = CountingAnalyzer()
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([job()])},
            store=InMemoryDiscoveryStore(
                clock=lambda: "2026-07-30T18:00:00+00:00"
            ),
            job_analyzer=analyzer,
            ranking_clock=lambda: "2026-07-30T18:00:00+00:00",
        )
        portland = CandidateJobProfile(
            target_titles=("Python Engineer",),
            verified_skills=("Python", "AWS"),
            preferred_locations=("Portland",),
        )
        seattle = CandidateJobProfile(
            target_titles=("Data Engineer",),
            verified_skills=("Python", "AWS"),
            preferred_locations=("Seattle",),
        )

        first = service.discover([source()], candidate_profile=portland)
        second = service.discover([source()], candidate_profile=seattle)

        self.assertEqual(portland.evidence_fingerprint, seattle.evidence_fingerprint)
        self.assertNotEqual(portland.preference_fingerprint, seattle.preference_fingerprint)
        self.assertEqual(1, len(analyzer.calls))
        self.assertFalse(first.ranked_jobs[0].cache_hit)
        self.assertTrue(second.ranked_jobs[0].cache_hit)
        self.assertEqual(first.ranked_jobs[0].fit_score, second.ranked_jobs[0].fit_score)
        self.assertNotEqual(
            first.ranked_jobs[0].preference_score,
            second.ranked_jobs[0].preference_score,
        )

    def test_verified_skills_do_not_change_preference_fit(self) -> None:
        configured_job = job()
        preferences = {
            "target_titles": ("Python Engineer",),
            "preferred_locations": ("Portland",),
            "accepted_workplace_types": (WorkplaceType.HYBRID,),
        }
        strong = CandidateJobProfile(
            verified_skills=("Python", "AWS"),
            **preferences,
        )
        weak = CandidateJobProfile(
            verified_skills=("COBOL",),
            **preferences,
        )

        from job_discovery.ranking import evaluate_stage_one

        strong_stage_one = evaluate_stage_one(
            configured_job,
            strong,
            evaluated_at="2026-07-30T18:00:00+00:00",
        )
        weak_stage_one = evaluate_stage_one(
            configured_job,
            weak,
            evaluated_at="2026-07-30T18:00:00+00:00",
        )

        self.assertEqual(
            strong_stage_one.preference_score,
            weak_stage_one.preference_score,
        )
        self.assertNotEqual(strong.evidence_fingerprint, weak.evidence_fingerprint)

    def test_json_file_store_survives_second_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            first = JsonFileDiscoveryStore(path, clock=lambda: "2026-07-30T18:00:00+00:00")
            first.put_company_source(source())
            stored_job = first.sync_discovered_jobs(source(), [job()])[0]
            first.put_job_state(
                DiscoveryJobState(
                    owner_id=stored_job.owner_id,
                    source_id=stored_job.source_id,
                    job_id=stored_job.id,
                    disposition=DiscoveryJobDisposition.SAVED,
                    updated_at="2026-07-30T18:01:00+00:00",
                )
            )

            second = JsonFileDiscoveryStore(path)

            loaded = second.list_discovered_jobs("owner-1", source_id="source-a")
            self.assertEqual(1, len(loaded))
            self.assertEqual("Senior Python Engineer", loaded[0].title)
            self.assertEqual("Acme", second.get_company_source("owner-1", "source-a").company_name)
            self.assertEqual(
                DiscoveryJobDisposition.SAVED,
                second.get_job_state(
                    "owner-1", "source-a", stored_job.id
                ).disposition,
            )

    def test_cross_source_signature_deduplication_keeps_richer_record(self) -> None:
        sparse = job(
            id=discovered_job_id("owner-1", "lever", "l1"),
            source_id="lever",
            source_type=JobSourceType.LEVER,
            external_job_id="l1",
            description="",
        )
        rich = job(
            id=discovered_job_id("owner-1", "greenhouse", "g1"),
            source_id="greenhouse",
            external_job_id="g1",
        )

        result = deduplicate_jobs([sparse, rich])

        self.assertEqual(1, len(result))
        self.assertEqual("greenhouse", result[0].source_id)

    def test_positive_keywords_affect_preference_fit_without_becoming_evidence(self) -> None:
        profile = CandidateJobProfile(
            verified_skills=("SQL",),
            preferred_keywords=("Snowflake", "regulatory reporting"),
            required_keywords=("SQL",),
        )
        evaluation = evaluate_stage_one(
            job(description="Build SQL and Snowflake regulatory reporting platforms."),
            profile,
            evaluated_at="2026-07-30T18:00:00+00:00",
        )
        self.assertTrue(evaluation.passed)
        component = next(
            item for item in evaluation.preference_components
            if item.name == "Preferred keywords"
        )
        self.assertEqual(100.0, component.score)
        self.assertNotIn("Snowflake", profile.verified_skills)
        self.assertNotIn("regulatory reporting", profile.evidence_statements)

    def test_missing_required_keyword_is_filtered_before_ai(self) -> None:
        store = InMemoryDiscoveryStore()
        analyzer = CountingAnalyzer()
        service = JobDiscoveryService(
            adapters={JobSourceType.GREENHOUSE: StaticAdapter([job()])},
            store=store,
            job_analyzer=analyzer,
        )
        result = service.discover(
            [source()],
            candidate_profile=CandidateJobProfile(
                verified_skills=("Python",),
                required_keywords=("Kubernetes",),
            ),
        )
        self.assertEqual([], analyzer.calls)
        self.assertEqual(1, len(result.filtered_jobs))
        self.assertIn(
            "Required keyword not found: Kubernetes",
            result.filtered_jobs[0].rejection_reasons,
        )


if __name__ == "__main__":
    unittest.main()
