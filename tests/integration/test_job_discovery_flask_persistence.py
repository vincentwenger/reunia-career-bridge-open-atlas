"""Discovery persistence must survive creation of a second Flask application."""

from __future__ import annotations

import importlib.util
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("APP_ENV", "testing")

_REQUIRED_MODULES = (
    "flask",
    "dotenv",
    "redis",
    "openai",
    "docx",
    "reportlab",
    "openpyxl",
    "pypdf",
    "xlrd",
)
_MISSING_MODULES = tuple(
    name for name in _REQUIRED_MODULES if importlib.util.find_spec(name) is None
)
_RUNTIME_AVAILABLE = not _MISSING_MODULES
_SKIP_REASON = "Missing runtime dependencies: " + ", ".join(_MISSING_MODULES)

if _RUNTIME_AVAILABLE:
    from app import create_application
    from job_discovery.models import (
        CompanySource,
        DiscoveredJob,
        DiscoveryJobDisposition,
        DiscoveryJobState,
        DiscoverySearchPreferences,
        JobSourceType,
        discovered_job_id,
    )
    from job_discovery.storage import DISCOVERY_TABLE_CONFIG_KEY, DynamoDBDiscoveryStore
    from meeting_assistant import extensions as extension_module
    from meeting_assistant.config import TestingConfig
    from tests.contracts.test_job_discovery_dynamodb import FakeDynamoTable
else:
    create_application = None


@unittest.skipUnless(_RUNTIME_AVAILABLE, _SKIP_REASON)
class JobDiscoveryFlaskPersistenceTests(unittest.TestCase):
    def test_discovery_state_survives_second_flask_instance(self) -> None:
        table = FakeDynamoTable()

        def discovery_store_factory(config):
            return DynamoDBDiscoveryStore(config, table=table)

        with (
            patch.object(
                TestingConfig,
                "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND",
                "dynamodb",
            ),
            patch.object(
                TestingConfig,
                DISCOVERY_TABLE_CONFIG_KEY,
                "careerbridge_job_discovery_test",
            ),
            patch.object(
                extension_module,
                "DynamoDBDiscoveryStore",
                side_effect=discovery_store_factory,
            ),
        ):
            first_app = create_application("testing")
            first_store = first_app.extensions["career_bridge_job_discovery_store"]
            configured = first_store.put_company_source(
                CompanySource(
                    id="source-one",
                    owner_id="owner-one",
                    company_name="Example Bank",
                    careers_url="https://boards.greenhouse.io/examplebank",
                    source_type=JobSourceType.GREENHOUSE,
                    source_identifier="examplebank",
                )
            )
            discovered = first_store.sync_discovered_jobs(
                configured,
                [
                    DiscoveredJob(
                        id=discovered_job_id("owner-one", "source-one", "job-one"),
                        owner_id="owner-one",
                        source_id="source-one",
                        external_job_id="job-one",
                        company="Example Bank",
                        title="Senior Data Engineer",
                        description="Build data platforms with SQL.",
                        canonical_url="https://boards.greenhouse.io/examplebank/jobs/job-one",
                        source_type=JobSourceType.GREENHOUSE,
                    )
                ],
            )[0]
            first_store.put_search_preferences(
                DiscoverySearchPreferences(
                    owner_id="owner-one",
                    target_titles=("Senior Data Engineer",),
                    preferred_locations=("Portland, OR",),
                    updated_at="2026-07-30T20:00:00+00:00",
                )
            )
            first_store.put_job_state(
                DiscoveryJobState(
                    owner_id="owner-one",
                    source_id="source-one",
                    job_id=discovered.id,
                    disposition=DiscoveryJobDisposition.SAVED,
                )
            )

            second_app = create_application("testing")
            second_store = second_app.extensions["career_bridge_job_discovery_store"]

        self.assertIsNot(first_app, second_app)
        self.assertIsNot(first_store, second_store)
        self.assertEqual(
            "Example Bank",
            second_store.get_company_source("owner-one", "source-one").company_name,
        )
        self.assertEqual(
            "Senior Data Engineer",
            second_store.get_discovered_job(
                "owner-one", "source-one", discovered.id
            ).title,
        )
        self.assertEqual(
            ("Senior Data Engineer",),
            second_store.get_search_preferences("owner-one").target_titles,
        )
        self.assertEqual(
            DiscoveryJobDisposition.SAVED,
            second_store.get_job_state(
                "owner-one", "source-one", discovered.id
            ).disposition,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
