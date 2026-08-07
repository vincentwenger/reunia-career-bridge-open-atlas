from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""HTTP commands for company sources, preferences, and scan schedules."""

_routes = DeferredRouteRegistry()

@_routes.post('/discovery/sources')
def create_discovery_source():
    _require_job_catalog_manager()
    owner_id = SHARED_CATALOG_SOURCE_OWNER_ID
    try:
        source_type = JobSourceType(
            str(request.form.get("source_type") or "").strip()
        )
        source = _normalized_company_source(
            source_id=uuid4().hex,
            owner_id=owner_id,
            company_name=request.form.get("company_name", ""),
            source_type=source_type,
            source_identifier=request.form.get("source_identifier", ""),
            careers_url=request.form.get("careers_url", ""),
            enabled=request.form.get("enabled", "1") not in {"0", "false"},
        )
        discovery_store.put_company_source(source)
    except (ValueError, DiscoveryOptimisticLockError) as exc:
        flash(f"Company source could not be saved: {exc}", "error")
    else:
        flash("Company source added. Refresh jobs for everyone to collect its postings.", "success")
    return redirect(
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-settings"
    )


@_routes.post('/discovery/sources/import')
def import_discovery_sources():
    _require_job_catalog_manager()
    owner_id = SHARED_CATALOG_SOURCE_OWNER_ID
    uploaded = request.files.get("source_import_file")
    if uploaded is None or not uploaded.filename:
        flash("Choose a CSV or JSON company-source file to import.", "error")
        return redirect(
            url_for("application_builder.job_discovery_workspace", view="settings")
            + "#job-discovery-source-import"
        )
    duplicate_policy = str(
        request.form.get("duplicate_policy") or "skip"
    ).strip().casefold()
    if duplicate_policy not in {"skip", "update"}:
        duplicate_policy = "skip"
    try:
        content = uploaded.stream.read(MAX_SOURCE_IMPORT_BYTES + 1)
        rows = parse_company_source_import(uploaded.filename, content)
        candidates: list[tuple[CompanySourceImportRow, CompanySource]] = []
        normalization_errors: list[str] = []
        for row in rows:
            try:
                candidate = _normalized_company_source(
                    source_id=uuid4().hex,
                    owner_id=owner_id,
                    company_name=row.company_name,
                    source_type=row.source_type,
                    source_identifier=row.source_identifier,
                    careers_url=row.careers_url,
                    enabled=row.enabled,
                )
            except ValueError as exc:
                normalization_errors.append(f"Row {row.row_number}: {exc}")
            else:
                candidates.append((row, candidate))
        if normalization_errors:
            preview = "; ".join(normalization_errors[:5])
            remaining = len(normalization_errors) - 5
            if remaining > 0:
                preview += f"; and {remaining} more error{'s' if remaining != 1 else ''}"
            raise CompanySourceImportError(preview)

        existing_sources = discovery_store.list_company_sources(owner_id)
        by_identity = {
            _company_source_identity(source): source
            for source in existing_sources
        }
        imported = 0
        updated = 0
        skipped = 0
        for row, candidate in candidates:
            identity = _company_source_identity(candidate)
            existing = by_identity.get(identity)
            if existing is not None and duplicate_policy == "skip":
                skipped += 1
                continue
            source_to_store = candidate
            if existing is not None:
                source_to_store = _normalized_company_source(
                    source_id=existing.id,
                    owner_id=owner_id,
                    company_name=row.company_name,
                    source_type=row.source_type,
                    source_identifier=row.source_identifier,
                    careers_url=row.careers_url,
                    enabled=row.enabled,
                    existing=existing,
                )
            stored = discovery_store.put_company_source(source_to_store)
            by_identity[identity] = stored
            if existing is None:
                imported += 1
            else:
                updated += 1
    except (CompanySourceImportError, DiscoveryOptimisticLockError, ValueError) as exc:
        flash(f"Company sources could not be imported: {exc}", "error")
    else:
        summary = (
            f"Company-source import completed: {imported} added, "
            f"{updated} updated, and {skipped} duplicate"
            f"{'s' if skipped != 1 else ''} skipped."
        )
        if imported or updated:
            summary += " Refresh jobs for everyone to collect their postings."
        flash(summary, "success")
    return redirect(
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-source-import"
    )


@_routes.get('/discovery/sources/import-template.csv')
def download_discovery_source_csv_template():
    _require_job_catalog_manager()
    content = (
        "Company,Source type,ATS site identifier,Career-page URL,Enabled\n"
        "Intel,Workday,,https://intel.wd1.myworkdayjobs.com/External,true\n"
        "SAP,SAP SuccessFactors,,https://jobs.sap.com/,true\n"
        "Oracle,Oracle Cloud HCM,,https://careers.oracle.com/en/sites/jobsearch/jobs,true\n"
        "iCIMS,iCIMS,,https://careers.icims.com/careers-home/jobs,true\n"
        "ServiceNow,SmartRecruiters,,https://careers.smartrecruiters.com/ServiceNow,true\n"
        "Avature,Avature,,https://careers.avature.net/en_US/main/SearchJobs,true\n"
        "Eightfold,Eightfold,,https://app.eightfold.ai/careers?domain=eightfold.ai,true\n"
        "Costco Wholesale,Eightfold,,https://careers.costco.com/jobs,true\n"
        "Transport for London,Taleo,,https://tfl.taleo.net/careersection/external/jobsearch.ftl,true\n"
        "Dayforce,Dayforce,,https://jobs.dayforcehcm.com/en-US/mydayforce/alljobs,true\n"
        "First Tech Federal Credit Union,Talemetry / TTC Portals,,https://firsttechfedcareers.ttcportals.com/search/jobs,true\n"
        "Washington Trust Bank,UKG Pro / UltiPro,,https://recruiting2.ultipro.com/WAS1000WTB/JobBoard/cb002c76-8419-4941-9c78-d28ae4e9c89e,true\n"
        "Portland State University,PeopleAdmin,,https://jobs.hrc.pdx.edu/postings/search,true\n"
        "Boeing,Radancy / TalentBrew,,https://jobs.boeing.com/search-jobs,true\n"
        "Amazon,Amazon Jobs,,https://www.amazon.jobs/en/search?country=USA,true\n"
        "Heritage Bank,Branded Requisition Portal,,https://careers.heritagebanknw.com/search-jobs,true\n"
    )
    return Response(
        content,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=job-discovery-company-sources.csv"
        },
    )


@_routes.get('/discovery/sources/import-template.json')
def download_discovery_source_json_template():
    _require_job_catalog_manager()
    content = json.dumps(
        {
            "companies": [
                {
                    "company": "Intel",
                    "source_type": "Workday",
                    "ats_site_identifier": "",
                    "career_page_url": "https://intel.wd1.myworkdayjobs.com/External",
                    "enabled": True,
                },
                {
                    "company": "SAP",
                    "source_type": "SAP SuccessFactors",
                    "ats_site_identifier": "",
                    "career_page_url": "https://jobs.sap.com/",
                    "enabled": True,
                },
                {
                    "company": "Oracle",
                    "source_type": "Oracle Cloud HCM",
                    "ats_site_identifier": "",
                    "career_page_url": "https://careers.oracle.com/en/sites/jobsearch/jobs",
                    "enabled": True,
                },
                {
                    "company": "iCIMS",
                    "source_type": "iCIMS",
                    "ats_site_identifier": "",
                    "career_page_url": "https://careers.icims.com/careers-home/jobs",
                    "enabled": True,
                },
                {
                    "company": "ServiceNow",
                    "source_type": "SmartRecruiters",
                    "ats_site_identifier": "",
                    "career_page_url": "https://careers.smartrecruiters.com/ServiceNow",
                    "enabled": True,
                },
                {
                    "company": "Avature",
                    "source_type": "Avature",
                    "ats_site_identifier": "",
                    "career_page_url": "https://careers.avature.net/en_US/main/SearchJobs",
                    "enabled": True,
                },
                {
                    "company": "Eightfold",
                    "source_type": "Eightfold",
                    "ats_site_identifier": "",
                    "career_page_url": "https://app.eightfold.ai/careers?domain=eightfold.ai",
                    "enabled": True,
                },
                {
                    "company": "Costco Wholesale",
                    "source_type": "Eightfold",
                    "ats_site_identifier": "",
                    "career_page_url": "https://careers.costco.com/jobs",
                    "enabled": True,
                },
                {
                    "company": "Transport for London",
                    "source_type": "Taleo",
                    "ats_site_identifier": "",
                    "career_page_url": "https://tfl.taleo.net/careersection/external/jobsearch.ftl",
                    "enabled": True,
                },
                {
                    "company": "Dayforce",
                    "source_type": "Dayforce",
                    "ats_site_identifier": "",
                    "career_page_url": "https://jobs.dayforcehcm.com/en-US/mydayforce/alljobs",
                    "enabled": True,
                },
                {
                    "company": "First Tech Federal Credit Union",
                    "source_type": "Talemetry / TTC Portals",
                    "ats_site_identifier": "",
                    "career_page_url": "https://firsttechfedcareers.ttcportals.com/search/jobs",
                    "enabled": True,
                },
                {
                    "company": "Washington Trust Bank",
                    "source_type": "UKG Pro / UltiPro",
                    "ats_site_identifier": "",
                    "career_page_url": "https://recruiting2.ultipro.com/WAS1000WTB/JobBoard/cb002c76-8419-4941-9c78-d28ae4e9c89e",
                    "enabled": True,
                },
                {
                    "company": "Portland State University",
                    "source_type": "PeopleAdmin",
                    "ats_site_identifier": "",
                    "career_page_url": "https://jobs.hrc.pdx.edu/postings/search",
                    "enabled": True,
                },
                {
                    "company": "Boeing",
                    "source_type": "Radancy / TalentBrew",
                    "ats_site_identifier": "",
                    "career_page_url": "https://jobs.boeing.com/search-jobs",
                    "enabled": True,
                },
                {
                    "company": "Amazon",
                    "source_type": "Amazon Jobs",
                    "ats_site_identifier": "",
                    "career_page_url": "https://www.amazon.jobs/en/search?country=USA",
                    "enabled": True,
                },
                {
                    "company": "Heritage Bank",
                    "source_type": "Branded Requisition Portal",
                    "ats_site_identifier": "",
                    "career_page_url": "https://careers.heritagebanknw.com/search-jobs",
                    "enabled": True,
                },
            ]
        },
        indent=2,
    )
    return Response(
        content + "\n",
        mimetype="application/json",
        headers={
            "Content-Disposition": "attachment; filename=job-discovery-company-sources.json"
        },
    )


@_routes.post('/discovery/sources/<source_id>/update')
def update_discovery_source(source_id: str):
    _require_job_catalog_manager()
    owner_id = SHARED_CATALOG_SOURCE_OWNER_ID
    existing = discovery_store.get_company_source(owner_id, source_id)
    if existing is None:
        abort(404)
    try:
        source_type = JobSourceType(
            str(request.form.get("source_type") or existing.source_type.value).strip()
        )
        revision = int(request.form.get("revision", existing.revision))
        if revision != existing.revision:
            raise DiscoveryOptimisticLockError(
                "This source changed after the page was loaded. Reload before saving."
            )
        updated = _normalized_company_source(
            source_id=existing.id,
            owner_id=owner_id,
            company_name=request.form.get("company_name") or existing.company_name,
            source_type=source_type,
            source_identifier=request.form.get("source_identifier", ""),
            careers_url=request.form.get("careers_url", ""),
            enabled=request.form.get("enabled") == "1",
            existing=existing,
        )
        discovery_store.put_company_source(updated)
    except (ValueError, DiscoveryOptimisticLockError) as exc:
        flash(f"Company source could not be updated: {exc}", "error")
    else:
        flash("Company source updated.", "success")
    return redirect(
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-settings"
    )


@_routes.post('/discovery/sources/<source_id>/toggle')
def toggle_discovery_source(source_id: str):
    _require_job_catalog_manager()
    owner_id = SHARED_CATALOG_SOURCE_OWNER_ID
    existing = discovery_store.get_company_source(owner_id, source_id)
    if existing is None:
        abort(404)
    try:
        discovery_store.put_company_source(
            replace(existing, enabled=not existing.enabled)
        )
    except DiscoveryOptimisticLockError as exc:
        flash(str(exc), "error")
    else:
        flash(
            "Company source enabled." if not existing.enabled else "Company source disabled.",
            "success",
        )
    return redirect(
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-settings"
    )


@_routes.post('/discovery/sources/<source_id>/delete')
def delete_discovery_source(source_id: str):
    _require_job_catalog_manager()
    owner_id = SHARED_CATALOG_SOURCE_OWNER_ID
    if not discovery_store.delete_company_source(owner_id, source_id):
        abort(404)
    flash(
        "Company source removed from the shared catalog. Existing saved jobs and Application Workspaces remain private to their users.",
        "success",
    )
    return redirect(
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-settings"
    )


@_routes.post('/discovery/sources/delete-all')
def delete_all_discovery_sources():
    _require_job_catalog_manager()
    owner_id = SHARED_CATALOG_SOURCE_OWNER_ID
    sources = discovery_store.list_company_sources(owner_id)
    expected_count_value = str(
        request.form.get("expected_source_count") or ""
    ).strip()
    try:
        expected_count = int(expected_count_value)
        if expected_count < 0:
            raise ValueError
    except ValueError:
        flash(
            "The remove-all confirmation was missing or invalid. No sources were removed; reload the page and try again.",
            "error",
        )
        return redirect(
            url_for("application_builder.job_discovery_workspace", view="settings")
            + "#job-discovery-settings"
        )

    if expected_count != len(sources):
        flash(
            "The shared company-source catalog changed after this page was loaded. No sources were removed; reload the page and try again.",
            "error",
        )
        return redirect(
            url_for("application_builder.job_discovery_workspace", view="settings")
            + "#job-discovery-settings"
        )

    if not sources:
        flash("There are no company sources to remove.", "success")
        return redirect(
            url_for("application_builder.job_discovery_workspace", view="settings")
            + "#job-discovery-settings"
        )

    removed_count = 0
    for source in sources:
        if discovery_store.delete_company_source(owner_id, source.id):
            removed_count += 1

    remaining_sources = discovery_store.list_company_sources(owner_id)
    if remaining_sources:
        flash(
            f"Removed {removed_count} company sources, but {len(remaining_sources)} could not be removed. Reload the page before trying again.",
            "error",
        )
    else:
        flash(
            f"Removed all {len(sources)} company sources from the shared catalog. Previously collected postings, saved jobs, and Application Workspaces were not deleted.",
            "success",
        )
    return redirect(
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-settings"
    )


@_routes.post('/discovery/preferences')
def update_discovery_preferences():
    owner_id = _application_owner_id()
    raw_salary = str(request.form.get("minimum_salary") or "").strip()
    raw_maximum_age = str(
        request.form.get("maximum_posting_age_days") or "30"
    ).strip().casefold()
    try:
        maximum_posting_age_days = (
            None if raw_maximum_age in {"0", "any", "all"}
            else int(raw_maximum_age)
        )
        preferences = DiscoverySearchPreferences(
            owner_id=owner_id,
            target_titles=_split_discovery_values(
                request.form.get("target_titles", "")
            ),
            preferred_locations=_split_discovery_values(
                request.form.get("preferred_locations", "")
            ),
            accepted_workplace_types=tuple(
                request.form.getlist("accepted_workplace_types")
            ),
            preferred_employment_types=_split_discovery_values(
                request.form.get("preferred_employment_types", "")
            ),
            preferred_keywords=_split_discovery_values(
                request.form.get("preferred_keywords", "")
            ),
            required_keywords=_split_discovery_values(
                request.form.get("required_keywords", "")
            ),
            minimum_salary=float(raw_salary) if raw_salary else None,
            minimum_salary_currency=str(
                request.form.get("minimum_salary_currency") or "USD"
            ),
            minimum_salary_interval=str(
                request.form.get("minimum_salary_interval") or "year"
            ),
            excluded_terms=_split_discovery_values(
                request.form.get("excluded_terms", "")
            ),
            excluded_title_terms=_split_discovery_values(
                request.form.get("excluded_title_terms", "")
            ),
            maximum_posting_age_days=maximum_posting_age_days,
            require_title_match=request.form.get("require_title_match") == "1",
            require_location_match=request.form.get("require_location_match") == "1",
            require_workplace_match=request.form.get("require_workplace_match") == "1",
            require_employment_type_match=(
                request.form.get("require_employment_type_match") == "1"
            ),
        )
        discovery_store.put_search_preferences(preferences)
        catalog_sources = discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID
        )
        (
            JobDiscoveryService(store=discovery_store)
            .enable_shared_public_catalog()
            .hydrate_owner_from_shared_catalog(
                owner_id, catalog_sources, force=True
            )
        )
        _try_prebuild_discovery_result_index(owner_id, current=state())
    except ValueError as exc:
        flash(f"Search preferences could not be saved: {exc}", "error")
    else:
        flash(
            "Search preferences saved. Search Priority has been recalculated without changing Job Fit.",
            "success",
        )
    return redirect(
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-settings"
    )


@_routes.post('/discovery/schedule')
def update_discovery_schedule():
    _require_job_catalog_manager()
    owner_id = SHARED_CATALOG_SOURCE_OWNER_ID
    existing = discovery_store.get_scan_schedule(owner_id)
    try:
        schedule = DiscoveryScanSchedule(
            owner_id=owner_id,
            cadence=str(request.form.get("cadence") or "manual"),
            local_hour=int(request.form.get("local_hour") or 8),
            weekday=int(request.form.get("weekday") or 0),
            timezone_name=str(request.form.get("timezone_name") or "UTC"),
            last_run_at=existing.last_run_at if existing else "",
        )
        # Validate the IANA time-zone name before persisting it.
        next_scheduled_run(schedule)
        discovery_store.put_scan_schedule(schedule)
    except (TypeError, ValueError) as exc:
        flash(f"Scan schedule could not be saved: {exc}", "error")
    else:
        flash(
            "Scan schedule saved. It will be honored by the external discovery runner; no scheduler runs inside Flask or Gunicorn.",
            "success",
        )
    return redirect(
        url_for("application_builder.job_discovery_workspace", view="settings")
        + "#job-discovery-schedule"
    )


_EXPORT_NAMES = (
    'create_discovery_source',
    'import_discovery_sources',
    'download_discovery_source_csv_template',
    'download_discovery_source_json_template',
    'update_discovery_source',
    'toggle_discovery_source',
    'delete_discovery_source',
    'delete_all_discovery_sources',
    'update_discovery_preferences',
    'update_discovery_schedule',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
