"""Runtime regression tests for the unified Réunia/Application Builder app.

Run from the repository root with:

    python -m unittest -v tests.integration.test_application_builder
"""

from __future__ import annotations

import importlib.util
import os
import re
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
    from flask import url_for

    from app import create_application
    from job_discovery.models import (
        CompanySource,
        DiscoveredJob,
        DiscoveryJobDisposition,
        DiscoveryJobState,
        JobSourceType,
        WorkplaceType,
        discovered_job_id,
    )
    from job_discovery.public_catalog import SHARED_CATALOG_SOURCE_OWNER_ID
    from products.resume_taylor.app import (
        DEFAULT_JOB_DESCRIPTION,
        init_application_builder,
    )
else:
    url_for = None
    create_application = None
    init_application_builder = None


@unittest.skipUnless(_RUNTIME_AVAILABLE, _SKIP_REASON)
class ApplicationBuilderIntegrationTests(unittest.TestCase):
    """Validate that the Builder behaves as a Blueprint inside Réunia."""

    def setUp(self) -> None:
        self.app = create_application("testing")
        self.app.config.update(
            CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            SERVER_NAME="localhost",
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "architecture-validation-user"
            session["email"] = "validation@example.test"
            session["full_name"] = "Architecture Validation"

    def _seed_discovered_job(self) -> DiscoveredJob:
        owner_id = "architecture-validation-user"
        source = CompanySource(
            id="regression-source",
            owner_id=owner_id,
            company_name="Regression Bank",
            careers_url="https://jobs.example.test",
            source_type=JobSourceType.GREENHOUSE,
            source_identifier="regression-bank",
        )
        job = DiscoveredJob(
            id=discovered_job_id(owner_id, source.id, "regression-job"),
            owner_id=owner_id,
            source_id=source.id,
            external_job_id="regression-job",
            company="Regression Bank",
            title="Data Engineer",
            location="Portland, OR",
            workplace_type=WorkplaceType.HYBRID,
            description="Build data platforms with Python and SQL.",
            canonical_url="https://jobs.example.test/regression-job",
            source_type=JobSourceType.GREENHOUSE,
        )
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        return discovery_store.sync_discovered_jobs(source, [job])[0]

    def test_new_user_career_translation_is_one_time_and_job_independent(self) -> None:
        response = self.client.get("/applications/career-translation")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Career Foundation · One-time setup", body)
        self.assertIn("A job description is not needed here.", body)
        self.assertIn("Import and generate baseline", body)
        self.assertNotIn("Hackathon journey · Step 2 of 10", body)
        self.assertNotIn("Vincent Wenger", body)
        self.assertNotIn("Barclays Services Corp.", body)
        self.assertNotIn('name="job_description"', body)
        self.assertNotIn('id="job-description"', body)

    def test_resume_workflow_keeps_job_description_in_application_setup(self) -> None:
        response = self.client.get("/applications/?tab=tailoring&stage=setup")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Resume Workflow · Imported resume", body)
        self.assertIn("Application and Job Setup", body)
        self.assertRegex(
            body,
            r'<textarea id="job-description"[^>]*>\s*</textarea>',
        )

    def test_builder_exception_uses_reunia_recovery_page(self) -> None:
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]
        with patch.object(
            application_store,
            "list_for_owner",
            side_effect=RuntimeError("forced validation failure"),
        ):
            response = self.client.get("/applications/")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 500)
        self.assertIn("System Error", body)
        self.assertIn("Réunia is temporarily unavailable", body)
        self.assertIn("Open Help &amp; Support", body)
        reference_match = re.search(r"REQ-[A-F0-9]{12}", body)
        self.assertIsNotNone(reference_match)
        reference_id = reference_match.group(0)

        support_reports = self.app.extensions["support_repository"].list_all()
        matching_reports = [
            item for item in support_reports
            if item.get("source") == "automatic_server_error"
            and reference_id in str(item.get("message") or "")
        ]
        self.assertEqual(len(matching_reports), 1)
        self.assertEqual(matching_reports[0].get("area"), "job_applications")
        self.assertIn("RuntimeError: forced validation failure", matching_reports[0]["message"])

        incidents = self.app.extensions["analytics_repository"].list_usage_events(
            metric="server_error"
        )
        matching_incidents = [
            item for item in incidents if item.get("reference_id") == reference_id
        ]
        self.assertEqual(len(matching_incidents), 1)
        self.assertEqual(matching_incidents[0].get("http_status"), "500")
        self.assertEqual(matching_incidents[0].get("request_path"), "/applications/")
        self.assertIn("Sanitized traceback", matching_incidents[0]["technical_details"])

    def test_job_discovery_500_is_sent_to_incidents_and_support(self) -> None:
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        with patch.object(
            discovery_store,
            "list_company_sources",
            side_effect=RuntimeError("forced job discovery failure"),
        ):
            response = self.client.get("/applications/job-discovery")

        self.assertEqual(response.status_code, 500)
        body = response.get_data(as_text=True)
        reference_match = re.search(r"REQ-[A-F0-9]{12}", body)
        self.assertIsNotNone(reference_match)
        reference_id = reference_match.group(0)

        reports = self.app.extensions["support_repository"].list_all()
        report = next(
            item for item in reports
            if item.get("source") == "automatic_server_error"
            and item.get("reference_id") == reference_id
        )
        self.assertEqual(report.get("area"), "job_discovery")
        self.assertIn("GET /applications/job-discovery", report["message"])
        self.assertIn("forced job discovery failure", report["message"])

        incidents = self.app.extensions["analytics_repository"].list_usage_events(
            metric="server_error"
        )
        incident = next(
            item for item in incidents if item.get("reference_id") == reference_id
        )
        self.assertEqual(incident.get("feature"), "job_discovery")
        self.assertEqual(incident.get("request_path"), "/applications/job-discovery")
        self.assertEqual(incident.get("support_request_id"), report.get("request_id"))

    def test_job_discovery_skips_active_application_and_document_hydration(self) -> None:
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]
        workflow_store = self.app.extensions[
            "career_bridge_workflow_store"
        ]
        document_store = self.app.extensions[
            "career_bridge_document_store"
        ]
        owner_id = "architecture-validation-user"
        application = application_store.create(
            owner_id,
            company="Active Application Co",
            role="Senior Engineer",
            job_url="https://jobs.example.test/active",
            job_description="This application must not be loaded by Job Discovery.",
        )
        foundation_key = f"{owner_id}:career-foundation:translation"
        loaded = workflow_store.load(foundation_key)
        loaded.state.final_resume_docx_key = "documents/final-resume.docx"
        loaded.state.final_resume_pdf_key = "documents/final-resume.pdf"
        workflow_store.save(
            foundation_key,
            loaded.state,
            expected_version=loaded.version,
            updated_by_request="job-discovery-no-hydration-test",
        )
        with self.client.session_transaction() as session:
            session["active_application_id"] = application.id

        with (
            patch.object(
                application_store,
                "get",
                side_effect=AssertionError(
                    "Job Discovery loaded the active application"
                ),
            ) as application_get,
            patch.object(
                document_store,
                "get",
                side_effect=AssertionError(
                    "Job Discovery hydrated workflow documents"
                ),
            ) as document_get,
        ):
            response = self.client.get("/applications/job-discovery")

        self.assertEqual(response.status_code, 200)
        application_get.assert_not_called()
        document_get.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertEqual(session.get("active_application_id"), application.id)
            self.assertEqual(session.get("active_workflow_key"), foundation_key)

    def test_job_discovery_exposes_phase_timings_in_header_and_logs(self) -> None:
        with self.assertLogs(self.app.logger.name, level="INFO") as captured:
            response = self.client.get("/applications/job-discovery")

        self.assertEqual(response.status_code, 200)
        server_timing = response.headers.get("Server-Timing", "")
        for metric in (
            "jd_context",
            "jd_workflow",
            "jd_profile",
            "jd_sources",
            "jd_preferences",
            "jd_template",
            "jd_persist",
            "jd_total",
        ):
            self.assertRegex(server_timing, rf"(?:^|, )\s*{metric};dur=\d+\.\d{{2}}")
        self.assertNotIn("jd_result_profile", server_timing)
        self.assertNotIn("jd_result_index", server_timing)

        timing_logs = [
            message for message in captured.output
            if "Job Discovery timing" in message
        ]
        self.assertEqual(1, len(timing_logs))
        self.assertIn("view=results", timing_logs[0])
        self.assertIn("status=200", timing_logs[0])
        self.assertIn("index_state=deferred_json", timing_logs[0])
        self.assertIn("total_ms=", timing_logs[0])

        results = self.client.get("/applications/job-discovery/results.json")
        self.assertEqual(results.status_code, 200)
        self.assertEqual("private, no-store", results.headers.get("Cache-Control"))
        result_timing = results.headers.get("Server-Timing", "")
        for metric in (
            "jd_json_sources",
            "jd_json_preferences",
            "jd_json_profile",
            "jd_json_index",
            "jd_json_template",
            "jd_json_total",
        ):
            self.assertRegex(result_timing, rf"(?:^|, )\s*{metric};dur=\d+\.\d{{2}}")
        payload = results.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn("html", payload)
        self.assertIn("summary", payload)


    def test_job_discovery_defers_catalog_hydration_to_separate_request(self) -> None:
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        catalog_source = discovery_store.put_company_source(
            CompanySource(
                id="deferred-hydration-source",
                owner_id=SHARED_CATALOG_SOURCE_OWNER_ID,
                company_name="Deferred Hydration Company",
                careers_url="https://deferred.example.test/careers",
                source_type=JobSourceType.GREENHOUSE,
                source_identifier="deferred-company",
            )
        )
        self.app.config["CSRF_ENABLED"] = False

        with patch(
            "products.resume_taylor.app.JobDiscoveryService."
            "hydrate_owner_from_shared_catalog",
            return_value=0,
        ) as hydrate:
            page = self.client.get("/applications/job-discovery")
            self.assertEqual(page.status_code, 200)
            self.assertIn(
                "/applications/discovery/catalog/hydrate",
                page.get_data(as_text=True),
            )
            hydrate.assert_not_called()

            deferred = self.client.post(
                "/applications/discovery/catalog/hydrate"
            )

        self.assertEqual(deferred.status_code, 200)
        self.assertEqual(
            {
                "ok": True,
                "changed": False,
                "hydrated_job_count": 0,
            },
            deferred.get_json(),
        )
        hydrate.assert_called_once_with(
            "architecture-validation-user", [catalog_source]
        )

    def test_job_discovery_tolerates_orphaned_application_created_state(self) -> None:
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        job = self._seed_discovered_job()
        discovery_store.put_job_state(
            DiscoveryJobState(
                owner_id=job.owner_id,
                source_id=job.source_id,
                job_id=job.id,
                disposition=DiscoveryJobDisposition.APPLICATION_CREATED,
                application_id="deleted-application",
            )
        )

        response = self.client.get("/applications/job-discovery")

        self.assertEqual(response.status_code, 200)
        reports = self.app.extensions["support_repository"].list_all()
        self.assertFalse(
            any(
                item.get("source") == "automatic_server_error"
                and "application_id is required for an application-created result"
                in str(item.get("message") or "")
                for item in reports
            )
        )

    def test_job_discovery_directly_resolves_recent_application_state(self) -> None:
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]
        job = self._seed_discovered_job()
        application = application_store.create(
            job.owner_id,
            company=job.company,
            role=job.title,
            job_url=job.canonical_url,
            job_description=job.description,
            source_job_id=job.id,
        )
        discovery_store.put_job_state(
            DiscoveryJobState(
                owner_id=job.owner_id,
                source_id=job.source_id,
                job_id=job.id,
                disposition=DiscoveryJobDisposition.APPLICATION_CREATED,
                application_id=application.id,
            )
        )

        with patch.object(application_store, "list_for_owner", return_value=[]):
            response = self.client.get("/applications/job-discovery")

        self.assertEqual(response.status_code, 200)

    def test_deleting_application_downgrades_discovery_state_to_saved(self) -> None:
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]
        job = self._seed_discovered_job()
        application = application_store.create(
            job.owner_id,
            company=job.company,
            role=job.title,
            job_url=job.canonical_url,
            job_description=job.description,
            source_job_id=job.id,
        )
        discovery_store.put_job_state(
            DiscoveryJobState(
                owner_id=job.owner_id,
                source_id=job.source_id,
                job_id=job.id,
                disposition=DiscoveryJobDisposition.APPLICATION_CREATED,
                application_id=application.id,
            )
        )
        self.app.config["CSRF_ENABLED"] = False

        response = self.client.post(
            f"/applications/applications/{application.id}/delete"
        )

        self.assertEqual(response.status_code, 302)
        state = discovery_store.get_job_state(job.owner_id, job.source_id, job.id)
        self.assertIsNotNone(state)
        self.assertEqual(DiscoveryJobDisposition.SAVED, state.disposition)
        self.assertEqual("", state.application_id)

    def test_manager_can_remove_all_shared_company_sources(self) -> None:
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        shared_sources = (
            CompanySource(
                id="shared-source-one",
                owner_id=SHARED_CATALOG_SOURCE_OWNER_ID,
                company_name="Shared Company One",
                careers_url="https://one.example.test/careers",
                source_type=JobSourceType.GREENHOUSE,
                source_identifier="shared-one",
            ),
            CompanySource(
                id="shared-source-two",
                owner_id=SHARED_CATALOG_SOURCE_OWNER_ID,
                company_name="Shared Company Two",
                careers_url="https://two.example.test/careers",
                source_type=JobSourceType.LEVER,
                source_identifier="shared-two",
            ),
        )
        for source in shared_sources:
            discovery_store.put_company_source(source)
        private_source = CompanySource(
            id="private-source",
            owner_id="another-owner",
            company_name="Private Company",
            careers_url="https://private.example.test/careers",
            source_type=JobSourceType.ASHBY,
            source_identifier="private-company",
        )
        discovery_store.put_company_source(private_source)
        with self.client.session_transaction() as session:
            session["is_admin"] = True
        self.app.config["CSRF_ENABLED"] = False

        response = self.client.post(
            "/applications/discovery/sources/delete-all",
            data={"expected_source_count": "2"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [],
            discovery_store.list_company_sources(
                SHARED_CATALOG_SOURCE_OWNER_ID
            ),
        )
        self.assertIsNotNone(
            discovery_store.get_company_source("another-owner", "private-source")
        )
        self.assertIn(
            "Removed all 2 company sources",
            response.get_data(as_text=True),
        )

    def test_remove_all_sources_requires_count_confirmation(self) -> None:
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        source = CompanySource(
            id="missing-confirmation-source",
            owner_id=SHARED_CATALOG_SOURCE_OWNER_ID,
            company_name="Confirmation Company",
            careers_url="https://confirmation.example.test/careers",
            source_type=JobSourceType.GREENHOUSE,
            source_identifier="confirmation-company",
        )
        discovery_store.put_company_source(source)
        with self.client.session_transaction() as session:
            session["is_admin"] = True
        self.app.config["CSRF_ENABLED"] = False

        response = self.client.post(
            "/applications/discovery/sources/delete-all",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(
            discovery_store.get_company_source(
                SHARED_CATALOG_SOURCE_OWNER_ID,
                source.id,
            )
        )
        self.assertIn(
            "confirmation was missing or invalid",
            response.get_data(as_text=True),
        )

    def test_remove_all_sources_rejects_a_stale_source_count(self) -> None:
        discovery_store = self.app.extensions[
            "career_bridge_job_discovery_store"
        ]
        source = CompanySource(
            id="stale-count-source",
            owner_id=SHARED_CATALOG_SOURCE_OWNER_ID,
            company_name="Stale Count Company",
            careers_url="https://stale.example.test/careers",
            source_type=JobSourceType.GREENHOUSE,
            source_identifier="stale-count",
        )
        discovery_store.put_company_source(source)
        with self.client.session_transaction() as session:
            session["is_admin"] = True
        self.app.config["CSRF_ENABLED"] = False

        response = self.client.post(
            "/applications/discovery/sources/delete-all",
            data={"expected_source_count": "0"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(
            discovery_store.get_company_source(
                SHARED_CATALOG_SOURCE_OWNER_ID,
                source.id,
            )
        )
        self.assertIn(
            "No sources were removed",
            response.get_data(as_text=True),
        )

    def test_missing_builder_url_uses_reunia_404_page(self) -> None:
        response = self.client.get("/applications/route-that-does-not-exist")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 404)
        self.assertIn("Page Not Found", body)
        self.assertIn("The page may have moved", body)
        self.assertIn("Open Help &amp; Support", body)

    def test_builder_forms_use_shared_reunia_csrf_token(self) -> None:
        page = self.client.get("/applications/")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)

        meta_match = re.search(
            r'<meta name="csrf-token" content="([^"]+)">', body
        )
        self.assertIsNotNone(meta_match)
        token = meta_match.group(1)
        self.assertIn(
            f'name="csrf_token" value="{token}"',
            body,
        )

        with self.client.session_transaction() as session:
            self.assertEqual(session.get("_csrf_token"), token)
            self.assertNotIn("csrf_token", session)

        rejected = self.client.post("/applications/reset")
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("Request Expired", rejected.get_data(as_text=True))

        accepted = self.client.post(
            "/applications/reset",
            data={"csrf_token": token},
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertIn("/applications/", accepted.headers["Location"])

    def test_authentication_session_is_shared_across_shell_and_builder(self) -> None:
        shell_response = self.client.get("/app")
        builder_response = self.client.get("/applications/")

        self.assertEqual(shell_response.status_code, 200)
        self.assertEqual(builder_response.status_code, 200)
        self.assertNotIn("/login.html", shell_response.headers.get("Location", ""))
        self.assertNotIn("/login.html", builder_response.headers.get("Location", ""))

        with self.client.session_transaction() as session:
            self.assertEqual(
                session.get("application_owner_id"),
                "architecture-validation-user",
            )
            self.assertEqual(
                session.get("workflow_sid"),
                "architecture-validation-user",
            )

    def test_builder_and_shared_static_assets_resolve(self) -> None:
        builder_css = self.client.get("/applications/static/styles.css")
        builder_js = self.client.get("/applications/static/app.js")
        shared_css = self.client.get("/static/css/base.css")

        self.assertEqual(builder_css.status_code, 200)
        self.assertEqual(builder_js.status_code, 200)
        self.assertEqual(shared_css.status_code, 200)
        self.assertIn("text/css", builder_css.content_type)
        self.assertIn("javascript", builder_js.content_type)

        with self.app.test_request_context():
            self.assertEqual(
                url_for("application_builder.static", filename="styles.css"),
                "/applications/static/styles.css",
            )

    def test_health_exposes_application_builder_storage_limitations(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "status": "ok",
                "services": ["reunia", "application-builder"],
                "application_builder": {
                    "workflow_storage": "memory",
                    "application_storage": "dynamodb",
                    "document_storage": "local",
                    "durability": "mixed",
                    "multi_worker_safe": False,
                    "multi_node_safe": False,
                },
            },
        )

    def test_startup_logs_application_builder_persistence_warning(self) -> None:
        with self.assertLogs(level="WARNING") as captured:
            create_application("testing")

        warning = "\n".join(captured.output)
        self.assertIn("Application Builder storage configured", warning)
        self.assertIn("workflow=memory", warning)
        self.assertIn("applications=dynamodb", warning)
        self.assertIn("documents=local", warning)
        self.assertIn("not fully durable", warning)

    def test_builder_stores_are_initialized_once_per_app(self) -> None:
        workflow_store = self.app.extensions["career_bridge_workflow_store"]
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]

        init_application_builder(self.app)
        self.assertIs(
            workflow_store,
            self.app.extensions["career_bridge_workflow_store"],
        )
        self.assertIs(
            application_store,
            self.app.extensions["career_bridge_application_store"],
        )

        second_app = create_application("testing")
        self.assertIsNot(
            workflow_store,
            second_app.extensions["career_bridge_workflow_store"],
        )
        self.assertIsNot(
            application_store,
            second_app.extensions["career_bridge_application_store"],
        )

    def test_new_discovery_application_workspace_uses_selected_job_description(self) -> None:
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]
        selected_description = (
            "Selected public job description: build secure payment APIs and "
            "operate them on AWS."
        )
        application = application_store.create(
            "architecture-validation-user",
            company="Selected Public Employer",
            role="Senior Platform Engineer",
            job_url="https://example.test/jobs/platform-engineer",
            job_description=selected_description,
            status="considering",
            workflow_step="setup",
            source_job_id="selected-public-job-123",
        )

        response = self.client.get(
            f"/applications/applications/{application.id}/builder",
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(selected_description, body)
        self.assertRegex(
            body,
            rf'<textarea id="job-description"[^>]*>\s*{re.escape(selected_description)}\s*</textarea>',
        )

        workflow_store = self.app.extensions["career_bridge_workflow_store"]
        saved_workflow = workflow_store.peek(
            f"architecture-validation-user:application:{application.id}"
        )
        self.assertIsNotNone(saved_workflow)
        self.assertEqual(saved_workflow.job_description, selected_description)
        self.assertEqual(saved_workflow.target_title, "Senior Platform Engineer")

    def test_existing_discovery_workspace_replaces_only_demo_description(self) -> None:
        application_store = self.app.extensions[
            "career_bridge_application_store"
        ]
        workflow_store = self.app.extensions["career_bridge_workflow_store"]
        selected_description = "Actual posting requirements from Job Discovery."
        application = application_store.create(
            "architecture-validation-user",
            company="Existing Public Employer",
            role="Cloud Engineer",
            job_description=selected_description,
            status="considering",
            workflow_step="setup",
            source_job_id="existing-public-job-456",
        )
        workflow_key = (
            f"architecture-validation-user:application:{application.id}"
        )
        loaded = workflow_store.load(workflow_key)
        loaded.state.target_title = "Cloud Engineer"
        loaded.state.job_description = DEFAULT_JOB_DESCRIPTION
        workflow_store.save(
            workflow_key,
            loaded.state,
            expected_version=loaded.version,
            updated_by_request="TEST-LEGACY-DEMO",
        )

        response = self.client.get(
            f"/applications/applications/{application.id}/builder",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(selected_description, response.get_data(as_text=True))
        self.assertEqual(
            workflow_store.peek(workflow_key).job_description,
            selected_description,
        )

        user_description = "My edited and expanded version of the posting."
        saved = workflow_store.load(workflow_key)
        saved.state.job_description = user_description
        workflow_store.save(
            workflow_key,
            saved.state,
            expected_version=saved.version,
            updated_by_request="TEST-USER-EDIT",
        )

        reopened = self.client.get(
            f"/applications/applications/{application.id}/builder",
            follow_redirects=True,
        )

        self.assertEqual(reopened.status_code, 200)
        self.assertIn(user_description, reopened.get_data(as_text=True))
        self.assertEqual(
            workflow_store.peek(workflow_key).job_description,
            user_description,
        )

    def test_builder_routes_and_urls_are_namespaced(self) -> None:
        builder_rules = [
            rule
            for rule in self.app.url_map.iter_rules()
            if rule.rule.startswith("/applications")
        ]
        self.assertGreater(len(builder_rules), 1)
        self.assertTrue(
            all(
                rule.endpoint.startswith("application_builder.")
                for rule in builder_rules
            )
        )

        with self.app.test_request_context():
            self.assertEqual(
                url_for("application_builder.index"),
                "/applications/",
            )
            self.assertEqual(
                url_for("application_builder.interview_preparation_workspace"),
                "/applications/interview-preparation",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
