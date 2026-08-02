from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "products" / "resume_taylor" / "app.py"
SETTINGS_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_settings.html"
)
TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_results.html"
)
DISCOVERY_PAGE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "job_discovery.html"
)
ANALYSIS_TEMPLATE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "_discovery_job_analysis.html"
)
APPLICATIONS_PAGE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "templates"
    / "application_builder"
    / "applications.html"
)
APPLICATION_TRACKER = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "application_tracker.py"
)
STYLES = ROOT / "products" / "resume_taylor" / "static" / "styles.css"
DYNAMODB_STORAGE = (
    ROOT
    / "products"
    / "resume_taylor"
    / "resume_tailor"
    / "dynamodb_storage.py"
)


class JobDiscoveryActionContractTests(unittest.TestCase):
    def test_every_required_result_action_is_present(self) -> None:
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )
        analysis = ANALYSIS_TEMPLATE.read_text(encoding="utf-8")
        for label in (
            "View posting",
            "View analysis",
            "Ignore",
            "Save",
            "Create Application Workspace",
        ):
            self.assertIn(label, template)
        self.assertIn("csrf_token()", template)
        self.assertIn("target=\"_blank\"", template)
        self.assertIn("rel=\"noopener noreferrer\"", template)
        self.assertIn("data-discovery-analysis-url", template)
        self.assertIn("Strongest matches", analysis)
        self.assertIn("Important gaps", analysis)
        self.assertIn("Why this matches", analysis)
        self.assertIn("data-record-id", analysis)
        self.assertIn("Career Evidence Library", analysis)
        self.assertNotIn("{% for item in fit.supported_requirements %}", analysis)

    def test_user_ready_source_and_preference_controls_are_present(self) -> None:
        settings = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        for label in (
            "Company sources",
            "Remove all company sources",
            "Scan this source",
            "Add a company source",
            "Import company sources from CSV or JSON",
            "Download CSV example",
            "Download JSON example",
            "Greenhouse",
            "Lever",
            "Ashby",
            "Workday",
            "SAP SuccessFactors",
            "Oracle Cloud HCM",
            "iCIMS",
            "SmartRecruiters",
            "Avature",
            "Eightfold",
            "Taleo",
            "Dayforce",
            "Talemetry / TTC Portals",
            "Jobvite",
            "UKG Pro / UltiPro",
            "PeopleAdmin",
            "Amazon Jobs",
            "Branded Requisition Portal",
            "Manual career-page URL",
            "Desired job titles",
            "Preferred locations",
            "Accepted workplace types",
            "Preferred keywords",
            "Required keywords",
            "Save search preferences",
            "Save refresh schedule",
        ):
            self.assertIn(label, settings)
        source = APP.read_text(encoding="utf-8")
        self.assertIn('(JobSourceType.ORACLE_CLOUD_HCM.value, "Oracle Cloud HCM")', source)
        self.assertIn('(JobSourceType.ICIMS.value, "iCIMS")', source)
        self.assertIn('(JobSourceType.SMARTRECRUITERS.value, "SmartRecruiters")', source)
        self.assertIn('(JobSourceType.AVATURE.value, "Avature")', source)
        self.assertIn('(JobSourceType.EIGHTFOLD.value, "Eightfold")', source)
        self.assertIn('(JobSourceType.TALEO.value, "Taleo")', source)
        self.assertIn('(JobSourceType.DAYFORCE.value, "Dayforce")', source)
        self.assertIn('(JobSourceType.TALEMETRY_TTC.value, "Talemetry / TTC Portals")', source)
        self.assertIn('(JobSourceType.JOBVITE.value, "Jobvite")', source)
        self.assertIn('(JobSourceType.UKG_PRO.value, "UKG Pro / UltiPro")', source)
        self.assertIn('(JobSourceType.PEOPLEADMIN.value, "PeopleAdmin")', source)
        self.assertIn('(JobSourceType.AMAZON_JOBS.value, "Amazon Jobs")', source)
        self.assertIn('JobSourceType.BRANDED_REQUISITION.value', source)
        self.assertIn('"Branded Requisition Portal"', source)
        self.assertIn("parse_talemetry_ttc_careers_url", source)
        self.assertIn("parse_jobvite_careers_url", source)
        self.assertIn("parse_ukg_pro_careers_url", source)
        self.assertIn("parse_peopleadmin_careers_url", source)
        self.assertIn("parse_amazon_jobs_careers_url", source)
        self.assertIn("parse_branded_requisition_careers_url", source)
        self.assertIn("parse_oracle_cloud_hcm_careers_url", source)
        self.assertIn("parse_icims_careers_url", source)
        for route in (
            "/discovery/sources",
            "/discovery/sources/import",
            "/discovery/sources/import-template.csv",
            "/discovery/sources/import-template.json",
            "/discovery/sources/<source_id>/update",
            "/discovery/sources/<source_id>/toggle",
            "/discovery/sources/<source_id>/delete",
            "/discovery/sources/delete-all",
            "/discovery/preferences",
        ):
            self.assertIn(route, source)


    def test_discovery_has_a_dedicated_page_and_navigation_boundary(self) -> None:
        app = APP.read_text(encoding="utf-8")
        discovery_page = DISCOVERY_PAGE.read_text(encoding="utf-8")
        applications_page = APPLICATIONS_PAGE.read_text(encoding="utf-8")
        self.assertIn('@application_builder_bp.get("/job-discovery")', app)
        self.assertIn('_discovery_settings.html', discovery_page)
        self.assertIn('_discovery_results.html', discovery_page)
        self.assertNotIn('_discovery_settings.html', applications_page)
        self.assertNotIn('_discovery_results.html', applications_page)
        self.assertNotIn('Open Job Discovery', applications_page)
        navbar = (ROOT / 'products/reunia/templates/navbar.html').read_text(encoding='utf-8')
        self.assertIn("{% set job_discovery_url = '/applications/job-discovery' %}", navbar)
        self.assertIn('<strong>Job Discovery</strong>', navbar)

    def test_results_use_server_side_pagination_tabs_and_compact_cards(self) -> None:
        source = APP.read_text(encoding="utf-8")
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )
        page = DISCOVERY_PAGE.read_text(encoding="utf-8")
        self.assertIn('_DISCOVERY_PAGE_SIZES = (10, 20, 50)', source)
        self.assertIn('_DISCOVERY_DEFAULT_PAGE_SIZE = 20', source)
        self.assertIn('per_page: int = _DISCOVERY_DEFAULT_PAGE_SIZE', source)
        self.assertIn('discovery_store.list_result_records_page(', source)
        self.assertIn('_discovery_paginate(', source)
        self.assertIn('DiscoveryResultIndexSummary(', source)
        self.assertIn('result_tab=result_tab', source)
        for result_group in (
            '"recommended"',
            '"possible"',
            '"pending"',
            '"low_match"',
            '"saved"',
            '"ignored"',
        ):
            self.assertIn(result_group, source)
        self.assertIn('/analysis"', source)
        self.assertIn("Results per page", template)
        self.assertIn("Minimum Job Fit", template)
        self.assertIn("High and Medium", source)
        self.assertIn("Possible matches", source)
        self.assertIn("Low matches", source)
        self.assertIn("Recommended", source)
        self.assertIn("Awaiting assessment", template)
        self.assertIn("data-discovery-analysis-url", template)
        self.assertIn("Manage catalog &amp; preferences", page)
        self.assertIn("Search Priority", source)
        styles = STYLES.read_text(encoding="utf-8")
        self.assertIn(".discovery-card-list { display: grid; gap: .7rem; }", styles)
        self.assertIn(".discovery-job-card.compact { border-radius: .8rem; padding: .7rem .8rem; }", styles)


    def test_workday_browser_refresh_is_bounded_and_defers_new_ai_analysis(self) -> None:
        source = APP.read_text(encoding="utf-8")
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )
        self.assertIn("_interactive_discovery_source", source)
        self.assertIn('"detail_fetch_limit": capped_int("detail_fetch_limit", 10, 10)', source)
        self.assertIn('"fetch_budget_seconds": capped_float(', source)
        self.assertIn("analyze_new_jobs=False", source)
        self.assertIn("Awaiting assessment", template)

    def test_discovery_request_skips_active_application_and_document_hydration(self) -> None:
        source = APP.read_text(encoding="utf-8")
        helper_start = source.index("def _job_discovery_account_request()")
        helper_end = source.index("def _career_translation_workflow_key", helper_start)
        helper = source[helper_start:helper_end]
        before_start = source.index("@application_builder_bp.before_request")
        before_end = source.index("@application_builder_bp.after_request", before_start)
        before_request = source[before_start:before_end]
        state_start = source.index("def state(*, hydrate_documents: bool = True)")
        state_end = source.index("def update_job_fields()", state_start)
        state_helper = source[state_start:state_end]

        self.assertIn("discovery", helper)
        self.assertIn("discovered", helper)
        self.assertIn(
            "requested_application_id = \"\" if (foundation_request or discovery_request)",
            before_request,
        )
        self.assertIn(
            "if foundation_request or discovery_request", before_request
        )
        self.assertIn(
            "g.skip_workflow_document_hydration = discovery_request",
            before_request,
        )
        self.assertIn("skip_workflow_document_hydration", state_helper)
        self.assertIn("_hydrate_workflow_documents(workflow_state)", state_helper)

    def test_catalog_hydration_is_deferred_until_after_page_render(self) -> None:
        source = APP.read_text(encoding="utf-8")
        template = (
            TEMPLATE.read_text(encoding="utf-8")
            + TEMPLATE.with_name("_discovery_results_content.html").read_text(encoding="utf-8")
        )
        javascript = (
            ROOT / "products" / "resume_taylor" / "static" / "app.js"
        ).read_text(encoding="utf-8")

        workspace_start = source.index("def job_discovery_workspace()")
        workspace_end = source.index(
            "def create_discovery_source()", workspace_start
        )
        workspace = source[workspace_start:workspace_end]
        hydration_start = source.index(
            "def hydrate_discovered_jobs_from_shared_catalog()"
        )
        hydration_end = source.index(
            "def refresh_discovered_job_source()", hydration_start
        )
        hydration = source[hydration_start:hydration_end]

        self.assertNotIn("hydrate_owner_from_shared_catalog", workspace)
        self.assertIn("hydrate_owner_from_shared_catalog", hydration)
        self.assertIn('/discovery/catalog/hydrate', source)
        self.assertIn("data-discovery-catalog-hydration-url", template)
        self.assertIn("data-discovery-catalog-version", template)
        self.assertIn("requestIdleCallback", javascript)
        self.assertIn("window.location.reload()", javascript)

    def test_routes_are_explicit_user_actions(self) -> None:
        source = APP.read_text(encoding="utf-8")
        for route in (
            "/discovery/jobs/<source_id>/<job_id>/save",
            "/discovery/jobs/<source_id>/<job_id>/ignore",
            "/discovery/jobs/<source_id>/<job_id>/create-application",
        ):
            self.assertIn(route, source)
        self.assertIn("DiscoveredJobApplicationService", source)

    def test_application_record_has_dedicated_source_job_id(self) -> None:
        tree = ast.parse(APPLICATION_TRACKER.read_text(encoding="utf-8"))
        record = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ApplicationRecord"
        )
        annotated_names = {
            node.target.id
            for node in record.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
        }
        self.assertIn("source_job_id", annotated_names)
        source = DYNAMODB_STORAGE.read_text(encoding="utf-8")
        self.assertIn('return f"{_SOURCE_JOB_PREFIX}{source_job_id}"', source)
        self.assertIn('ConditionExpression="attribute_not_exists(#storage_key)"', source)


if __name__ == "__main__":
    unittest.main()
