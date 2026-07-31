from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
CONFIG = ROOT / "products" / "reunia" / "meeting_assistant" / "config.py"
AUTH = ROOT / "products" / "reunia" / "meeting_assistant" / "blueprints" / "auth" / "routes.py"
SETTINGS = ROOT / "products" / "resume_taylor" / "templates" / "application_builder" / "_discovery_settings.html"
RESULTS = ROOT / "products" / "resume_taylor" / "templates" / "application_builder" / "_discovery_results.html"
SCHEDULING = ROOT / "job_discovery" / "scheduling.py"


class SharedJobCatalogAccessContractTests(unittest.TestCase):
    def test_catalog_mutations_and_refresh_require_manager_access(self) -> None:
        source = APP.read_text(encoding="utf-8")
        for function_name in (
            "create_discovery_source",
            "update_discovery_source",
            "toggle_discovery_source",
            "delete_discovery_source",
            "update_discovery_schedule",
            "refresh_discovered_jobs",
        ):
            start = source.index(f"def {function_name}")
            segment = source[start : start + 500]
            self.assertIn("_require_job_catalog_manager()", segment)
        self.assertIn("SHARED_CATALOG_SOURCE_OWNER_ID", source)
        self.assertIn("hydrate_owner_from_shared_catalog", source)

    def test_regular_users_only_receive_personal_preference_controls(self) -> None:
        settings = SETTINGS.read_text(encoding="utf-8")
        results = RESULTS.read_text(encoding="utf-8")
        self.assertIn("{% if can_manage_job_catalog %}", settings)
        self.assertIn("Companies are managed centrally", settings)
        self.assertIn("Save search preferences", settings)
        self.assertIn("{% if can_manage_job_catalog %}", results)
        self.assertIn("Refresh jobs for everyone", results)
        self.assertIn("managed by administrators and job curators", results)

    def test_manager_groups_and_users_are_configurable(self) -> None:
        config = CONFIG.read_text(encoding="utf-8")
        auth = AUTH.read_text(encoding="utf-8")
        self.assertIn("JOB_CATALOG_MANAGER_GROUPS", config)
        self.assertIn("job_curators,career_coaches", config)
        self.assertIn("JOB_CATALOG_MANAGER_USER_IDS", config)
        self.assertIn('session["groups"]', auth)

    def test_external_runner_defaults_to_shared_catalog_owner(self) -> None:
        scheduling = SCHEDULING.read_text(encoding="utf-8")
        self.assertIn("SHARED_CATALOG_SOURCE_OWNER_ID", scheduling)
        self.assertIn("configured or [SHARED_CATALOG_SOURCE_OWNER_ID]", scheduling)


if __name__ == "__main__":
    unittest.main()
