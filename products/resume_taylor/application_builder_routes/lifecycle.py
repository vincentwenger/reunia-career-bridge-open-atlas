from __future__ import annotations

from time import perf_counter
from typing import Any


def register(namespace: dict[str, Any]) -> dict[str, Any]:
    """Register this feature's Application Builder routes and return shared helpers."""

    globals().update(namespace)
    def _job_discovery_page_timing_active() -> bool:
        return (
            request.method == "GET"
            and str(request.endpoint or "")
            == "application_builder.job_discovery_workspace"
        )

    def _start_job_discovery_timing() -> None:
        if not _job_discovery_page_timing_active():
            return
        if getattr(g, "job_discovery_timing_started_at", None) is None:
            g.job_discovery_timing_started_at = perf_counter()
            g.job_discovery_timing_phases = []

    def _record_job_discovery_phase(
        metric: str, started_at: float, description: str
    ) -> float:
        finished_at = perf_counter()
        if _job_discovery_page_timing_active():
            _start_job_discovery_timing()
            phases = getattr(g, "job_discovery_timing_phases", None)
            if isinstance(phases, list):
                phases.append(
                    (
                        str(metric),
                        max(0.0, (finished_at - started_at) * 1000.0),
                        str(description),
                    )
                )
        return finished_at

    def _job_discovery_slow_request_threshold_ms() -> float:
        raw_value = current_app.config.get(
            "CAREER_BRIDGE_JOB_DISCOVERY_SLOW_REQUEST_MS", 1000
        )
        try:
            return max(0.0, float(raw_value))
        except (TypeError, ValueError):
            return 1000.0

    def _finalize_job_discovery_timing(response: Response) -> Response:
        if (
            not _job_discovery_page_timing_active()
            or bool(getattr(g, "job_discovery_timing_finalized", False))
        ):
            return response

        started_at = getattr(g, "job_discovery_timing_started_at", None)
        if started_at is None:
            return response
        total_ms = max(0.0, (perf_counter() - float(started_at)) * 1000.0)
        phases = tuple(getattr(g, "job_discovery_timing_phases", ()) or ())
        server_timing_values: list[str] = []
        log_values: list[str] = []
        for metric, duration_ms, description in phases:
            safe_metric = re.sub(r"[^A-Za-z0-9_-]", "_", str(metric))
            safe_description = (
                str(description).replace("\\", " ").replace('"', "'")
            )
            server_timing_values.append(
                f'{safe_metric};dur={duration_ms:.2f};desc="{safe_description}"'
            )
            log_values.append(f"{safe_metric}_ms={duration_ms:.2f}")
        server_timing_values.append(
            f'jd_total;dur={total_ms:.2f};desc="Job Discovery total"'
        )
        existing_server_timing = str(
            response.headers.get("Server-Timing") or ""
        ).strip()
        generated_server_timing = ", ".join(server_timing_values)
        response.headers["Server-Timing"] = (
            f"{existing_server_timing}, {generated_server_timing}"
            if existing_server_timing
            else generated_server_timing
        )

        request_id = str(
            getattr(g, "workflow_request_id", "")
            or getattr(g, "request_id", "")
            or ""
        )
        discovery_view = str(
            getattr(g, "job_discovery_timing_view", "")
            or ("settings" if request.args.get("view") == "settings" else "results")
        )
        owner_scope = str(getattr(g, "job_discovery_timing_owner_scope", "") or "")
        index_state = str(getattr(g, "job_discovery_timing_index_state", "") or "")
        details = " ".join(log_values)
        log_method = (
            current_app.logger.warning
            if total_ms >= _job_discovery_slow_request_threshold_ms()
            else current_app.logger.info
        )
        log_method(
            "Job Discovery timing request_id=%s view=%s status=%s "
            "owner_scope=%s index_state=%s total_ms=%.2f %s",
            request_id,
            discovery_view,
            response.status_code,
            owner_scope,
            index_state,
            total_ms,
            details,
        )
        g.job_discovery_timing_finalized = True
        return response

    def workflow_conflict_response(
        conflict: WorkflowConflictError | None = None,
    ) -> Response:
        """Return a recoverable 409 response for an optimistic-lock conflict."""

        message = (
            "This workflow changed in another browser tab or overlapping request. "
            "Your conflicting update was not saved. Reload the latest workflow "
            "state, review it, and apply the change again."
        )
        active_application = getattr(g, "active_application", None)
        foundation_conflict = _career_translation_foundation_request()
        retry_url = (
            url_for("application_builder.career_translation_workspace")
            if foundation_conflict
            else (
                url_for(
                    "application_builder.index",
                    tab="tailoring",
                    application_id=active_application.id,
                )
                if active_application is not None
                else url_for("application_builder.index", tab="applications")
            )
        )
        wants_json = bool(request.is_json) or (
            request.accept_mimetypes.best == "application/json"
            and request.accept_mimetypes["application/json"]
            >= request.accept_mimetypes["text/html"]
        )
        request_id = str(getattr(g, "workflow_request_id", "") or "")
        latest_request_id = str(
            getattr(conflict, "actual_updated_by_request", "") or ""
        )
        if wants_json:
            response = jsonify(
                {
                    "status": "conflict",
                    "message": message,
                    "retry_url": retry_url,
                    "request_id": request_id,
                    "current_version": getattr(conflict, "actual_version", None),
                    "last_updated_by_request": latest_request_id,
                }
            )
            response.status_code = 409
        else:
            response = Response(
                render_template(
                    "application_builder/workflow_conflict.html",
                    active_tab=(
                        "career_translation" if foundation_conflict else "tailoring"
                    ),
                    conflict_message=message,
                    retry_url=retry_url,
                    conflict_request_id=request_id,
                    latest_request_id=latest_request_id,
                ),
                status=409,
                mimetype="text/html",
            )
        response.headers["Cache-Control"] = "no-store"
        return response

    def _career_translation_foundation_request() -> bool:
        """Return True for routes that edit the Baseline Resume."""

        endpoint = str(request.endpoint or "")
        if endpoint in {
            "application_builder.career_translation_workspace",
            "application_builder.update_baseline_career_role",
            "application_builder.delete_baseline_career_role",
            "application_builder.update_baseline_summary",
            "application_builder.update_baseline_skills",
            "application_builder.update_baseline_education",
            "application_builder.delete_baseline_education",
        }:
            return True
        return (
            endpoint == "application_builder.upload_profile"
            and str(request.form.get("return_to") or "").strip().casefold()
            == "career_translation"
        )

    def _job_discovery_account_request() -> bool:
        """Return True when the request belongs to account-level Job Discovery.

        Discovery uses the reusable foundation profile and must not load whichever
        job application happened to be active in the user's previous workspace.
        All Discovery endpoint names contain either ``discovery`` or
        ``discovered``.
        """

        endpoint = str(request.endpoint or "")
        return endpoint.startswith("application_builder.") and (
            "discovery" in endpoint or "discovered" in endpoint
        )

    def _career_translation_workflow_key(owner_id: str) -> str:
        return f"{owner_id}:career-foundation:translation"

    def _application_baseline_is_frozen(workflow_state: WorkflowState) -> bool:
        """Return whether tailoring has already captured this application's baseline."""

        return bool(
            workflow_state.workflow_stage != "initial"
            or workflow_state.analysis is not None
            or workflow_state.initial_report is not None
            or workflow_state.initial_evidence_proposal is not None
            or workflow_state.provisional_proposal is not None
            or workflow_state.draft_proposal is not None
            or workflow_state.final_proposal is not None
            or workflow_state.confirmation_complete
            or workflow_state.workflow_step_snapshots
        )

    def _foundation_baseline_version_fingerprint(
        foundation: WorkflowState,
    ) -> str:
        """Fingerprint the reusable baseline independently of application translation."""

        original = foundation.original_source_profile or foundation.source_profile
        payload = {
            "source_profile": foundation.source_profile.model_dump(mode="json"),
            "original_source_profile": original.model_dump(mode="json"),
            "source_profile_language": foundation.source_profile_language,
            "source_resume_language": foundation.source_resume_language,
            "source_resume_fingerprint": foundation.source_resume_fingerprint,
            "profile_upload_name": foundation.profile_upload_name,
        }
        return _hash_json(payload)

    def _foundation_baseline_differs(
        workflow_state: WorkflowState, foundation: WorkflowState
    ) -> bool:
        """Compare provenance without confusing application translation with drift."""

        foundation_version = _foundation_baseline_version_fingerprint(foundation)
        if workflow_state.foundation_baseline_fingerprint:
            return workflow_state.foundation_baseline_fingerprint != foundation_version

        # Legacy application workflows predate the explicit provenance field.
        # A different imported-file fingerprint is a reliable indication that
        # the application was built from another resume. When both fingerprints
        # are unavailable, fall back to the structured profile comparison.
        if (
            workflow_state.source_resume_fingerprint
            or foundation.source_resume_fingerprint
        ):
            if (
                workflow_state.source_resume_fingerprint
                != foundation.source_resume_fingerprint
            ):
                return True
            if _application_baseline_is_frozen(workflow_state):
                # A frozen legacy application may have intentionally translated
                # the same imported resume for a different target market.
                return False
        return (
            workflow_state.source_profile.model_dump(mode="json")
            != foundation.source_profile.model_dump(mode="json")
        )

    def _copy_foundation_baseline(
        workflow_state: WorkflowState, foundation: WorkflowState
    ) -> None:
        """Replace application resume evidence with the reusable Foundation baseline."""

        has_foundation_resume = bool(
            foundation.source_profile.all_source_text().strip()
        )
        if has_foundation_resume:
            workflow_state.source_profile = foundation.source_profile.model_copy(
                deep=True
            )
            workflow_state.original_source_profile = (
                foundation.original_source_profile.model_copy(deep=True)
                if foundation.original_source_profile is not None
                else foundation.source_profile.model_copy(deep=True)
            )
            workflow_state.source_profile_language = (
                foundation.source_profile_language
            )
            workflow_state.source_resume_language = (
                foundation.source_resume_language
            )
            workflow_state.source_profile_translation_fingerprint = (
                foundation.source_profile_translation_fingerprint
            )
            workflow_state.profile_upload_name = foundation.profile_upload_name
            workflow_state.source_resume_fingerprint = (
                foundation.source_resume_fingerprint
            )
            workflow_state.source_resume_contact_links_fingerprint = (
                foundation.source_resume_contact_links_fingerprint
            )
            workflow_state.foundation_baseline_fingerprint = (
                _foundation_baseline_version_fingerprint(foundation)
            )
        else:
            workflow_state.source_profile = _empty_candidate_profile()
            workflow_state.original_source_profile = None
            workflow_state.source_profile_language = ""
            workflow_state.source_resume_language = ""
            workflow_state.source_profile_translation_fingerprint = ""
            workflow_state.profile_upload_name = ""
            workflow_state.source_resume_fingerprint = ""
            workflow_state.source_resume_contact_links_fingerprint = ""
            workflow_state.foundation_baseline_fingerprint = (
                _foundation_baseline_version_fingerprint(foundation)
            )

        # The original Foundation document remains owned by the account-level
        # workflow. Application workflows store a serialized evidence copy only,
        # preventing an application reset or deletion from deleting that file.
        workflow_state.source_resume_key = ""

    def _sync_application_from_foundation(
        owner_id: str,
        workflow_state: WorkflowState,
        *,
        force: bool = False,
    ) -> str:
        """Keep an application baseline aligned with Foundation until frozen.

        Returns ``synced``, ``current``, ``frozen``, or ``missing`` for UI and
        route decisions. A forced sync intentionally clears all tailoring
        results because they were calculated from the previous baseline.
        """

        foundation = store.load(_career_translation_workflow_key(owner_id)).state
        _backfill_professional_contact_links(foundation, document_store)
        foundation_has_resume = bool(
            foundation.source_profile.all_source_text().strip()
        )
        differs = _foundation_baseline_differs(workflow_state, foundation)
        frozen = _application_baseline_is_frozen(workflow_state)

        if force and not foundation_has_resume:
            # Never erase a frozen application merely because Foundation is
            # currently empty; the refresh route will direct the user to create
            # the reusable baseline first.
            return "missing"

        if frozen and not force:
            # Preserve the immutable baseline used by completed workflow steps,
            # while repairing contact URLs that older imports may have dropped.
            if foundation_has_resume:
                workflow_state.source_profile = inherit_professional_contact_urls(
                    workflow_state.source_profile, foundation.source_profile
                )
                foundation_original = (
                    foundation.original_source_profile or foundation.source_profile
                )
                if workflow_state.original_source_profile is not None:
                    workflow_state.original_source_profile = (
                        inherit_professional_contact_urls(
                            workflow_state.original_source_profile,
                            foundation_original,
                        )
                    )
                _propagate_professional_contact_links(
                    workflow_state, workflow_state.source_profile
                )
            return "frozen" if differs else "current"

        if differs or force:
            _copy_foundation_baseline(workflow_state, foundation)
            workflow_state.clear_results()
            if foundation_has_resume:
                _propagate_professional_contact_links(
                    workflow_state, workflow_state.source_profile
                )
                return "synced"
            return "missing"

        if foundation_has_resume:
            _propagate_professional_contact_links(
                workflow_state, workflow_state.source_profile
            )
            return "current"
        return "missing"

    @application_builder_bp.before_request
    def load_workflow_state() -> Response | None:
        _start_job_discovery_timing()
        context_started_at = perf_counter()
        if current_app.config.get("CAREER_BRIDGE_REQUIRE_AUTH") and not session.get(
            "user_id"
        ):
            response = redirect(
                str(
                    current_app.config.get("CAREER_BRIDGE_LOGIN_URL")
                    or "/login.html"
                )
            )
            _record_job_discovery_phase(
                "jd_context", context_started_at, "Request context"
            )
            return response

        owner_id = (
            str(session.get("user_id") or "").strip()
            or str(session.get("application_owner_id") or "").strip()
            or str(session.get("workflow_sid") or "").strip()
        )
        if not owner_id:
            owner_id = store.new_id()
        session["application_owner_id"] = owner_id
        # Retain the legacy key because existing application routes and tests use it.
        session["workflow_sid"] = owner_id

        foundation_request = _career_translation_foundation_request()
        discovery_request = _job_discovery_account_request()
        requested_application_id = "" if (foundation_request or discovery_request) else (
            str((request.view_args or {}).get("application_id") or "").strip()
            or str(request.args.get("application_id") or "").strip()
            or str(request.form.get("application_id") or "").strip()
            or str(session.get("active_application_id") or "").strip()
        )
        application = (
            application_store.get(
                owner_id, requested_application_id, include_resume_bytes=False
            )
            if requested_application_id
            else None
        )
        if requested_application_id and application is None:
            session.pop("active_application_id", None)
            requested_application_id = ""
        elif application is not None:
            session["active_application_id"] = application.id

        workflow_key = (
            _career_translation_workflow_key(owner_id)
            if foundation_request or discovery_request
            else (
                f"{owner_id}:application:{requested_application_id}"
                if requested_application_id
                else f"{owner_id}:application:scratch"
            )
        )
        session["active_workflow_key"] = workflow_key
        g.application_owner_id = owner_id
        g.active_application = application
        g.workflow_key = workflow_key
        g.skip_workflow_document_hydration = discovery_request
        g.workflow_state_deleted = False
        g.workflow_request_id = normalize_workflow_request_id(
            getattr(g, "request_id", "")
        )
        _record_job_discovery_phase(
            "jd_context", context_started_at, "Request context"
        )
        workflow_started_at = perf_counter()
        loaded_workflow = store.load(workflow_key)
        g.workflow_state = loaded_workflow.state
        g.workflow_initial_version = loaded_workflow.version
        g.workflow_initial_fingerprint = loaded_workflow.fingerprint
        g.workflow_initial_updated_at = loaded_workflow.updated_at
        g.workflow_initial_updated_by_request = loaded_workflow.updated_by_request
        if application is None:
            # Workflows created by older releases may still contain the exact
            # bundled Barclays example. It was never user input, so remove it
            # when opening the unassigned Career Translation scratch workspace.
            if (
                DEFAULT_JOB_DESCRIPTION_NORMALIZED
                and normalize_job_description(g.workflow_state.job_description)
                == DEFAULT_JOB_DESCRIPTION_NORMALIZED
            ):
                g.workflow_state.job_description = ""
        else:
            # A newly created application workflow may be produced from legacy
            # demo state. Seed that untouched workflow from the selected
            # application record so a Job Discovery workspace opens with the
            # actual posting. Once the workflow has been saved, preserve edits.
            is_uninitialized_application_workflow = (
                loaded_workflow.version == 0
                and not loaded_workflow.updated_at
                and not loaded_workflow.updated_by_request
            )
            workflow_job_description = normalize_job_description(
                g.workflow_state.job_description
            )
            uses_demo_job_description = bool(
                application.source_job_id
                and DEFAULT_JOB_DESCRIPTION_NORMALIZED
                and workflow_job_description == DEFAULT_JOB_DESCRIPTION_NORMALIZED
            )
            if (
                not g.workflow_state.source_resume_key
                and application.original_resume_key
            ):
                g.workflow_state.source_resume_key = application.original_resume_key
            if is_uninitialized_application_workflow:
                g.workflow_state.target_title = application.role
                g.workflow_state.job_description = application.job_description
                g.workflow_state.career_background.target_role = application.role
            else:
                if not g.workflow_state.target_title:
                    g.workflow_state.target_title = application.role
                if not g.workflow_state.career_background.target_role:
                    g.workflow_state.career_background.target_role = (
                        g.workflow_state.target_title or application.role
                    )
                if uses_demo_job_description or (
                    not workflow_job_description and application.job_description
                ):
                    g.workflow_state.job_description = application.job_description

            g.application_baseline_status = _sync_application_from_foundation(
                owner_id, g.workflow_state
            )
            if (
                not _application_baseline_is_frozen(g.workflow_state)
                and g.application_baseline_status
                in {"synced", "current", "missing"}
                and application.original_resume_key
            ):
                previous_application_source_key = application.original_resume_key
                updated_application = application_store.update_builder_progress(
                    owner_id,
                    application.id,
                    workflow_step=application.workflow_step,
                    original_resume_key="",
                )
                if updated_application is not None:
                    application = updated_application
                    g.active_application = updated_application
                document_store.delete(previous_application_source_key)
            if _backfill_professional_contact_links(
                g.workflow_state, document_store
            ):
                _propagate_professional_contact_links(g.workflow_state)
            g.workflow_state.career_background.target_role = (
                g.workflow_state.target_title or application.role
            )

            pending_refresh = session.get(
                "pending_application_job_description_refresh"
            )
            if (
                isinstance(pending_refresh, dict)
                and str(pending_refresh.get("application_id") or "")
                == application.id
            ):
                previous_fingerprint = str(
                    pending_refresh.get("previous_fingerprint") or ""
                )
                current_fingerprint = hashlib.sha256(
                    normalize_job_description(
                        g.workflow_state.job_description
                    ).encode("utf-8")
                ).hexdigest()
                if (
                    not normalize_job_description(g.workflow_state.job_description)
                    or current_fingerprint == previous_fingerprint
                ):
                    g.workflow_state.job_description = application.job_description
                session.pop("pending_application_job_description_refresh", None)

        _record_job_discovery_phase(
            "jd_workflow", workflow_started_at, "Workflow load"
        )
        profile_started_at = perf_counter()
        g.reusable_career_profile = _load_reusable_career_profile(owner_id)
        _record_job_discovery_phase(
            "jd_profile", profile_started_at, "Reusable profile load"
        )
        return None

    def _persist_workflow_state_now() -> bool:
        """Durably save the loaded workflow and refresh its optimistic-lock token.

        Most routes can rely on the shared ``after_request`` hook. Operations that
        replace a retained document, such as a Baseline Resume re-import, call this
        helper before deleting the previous object so the new structured profile
        and its source-document reference are committed atomically from the user's
        perspective.
        """

        workflow_key = str(getattr(g, "workflow_key", "") or "")
        workflow_state = getattr(g, "workflow_state", None)
        if (
            not workflow_key
            or workflow_state is None
            or bool(getattr(g, "workflow_state_deleted", False))
        ):
            return False

        _persist_workflow_documents(
            str(getattr(g, "application_owner_id", "") or ""),
            workflow_key,
            workflow_state,
        )
        current_fingerprint = workflow_state_fingerprint(workflow_state)
        initial_fingerprint = str(
            getattr(g, "workflow_initial_fingerprint", "") or ""
        )
        if current_fingerprint == initial_fingerprint:
            return False

        saved = store.save(
            workflow_key,
            workflow_state,
            expected_version=int(getattr(g, "workflow_initial_version", 0) or 0),
            updated_by_request=str(getattr(g, "workflow_request_id", "") or ""),
        )
        g.workflow_initial_version = saved.version
        g.workflow_initial_fingerprint = saved.fingerprint
        g.workflow_initial_updated_at = saved.updated_at
        g.workflow_initial_updated_by_request = saved.updated_by_request
        return True

    @application_builder_bp.after_request
    def add_job_discovery_server_timing(response: Response) -> Response:
        """Expose phase timings after workflow persistence has completed."""

        return _finalize_job_discovery_timing(response)

    @application_builder_bp.after_request
    def persist_workflow_state(response: Response) -> Response:
        """Persist only changed state using optimistic version checking."""

        persist_started_at = perf_counter()
        try:
            try:
                _persist_workflow_state_now()
            except WorkflowConflictError as exc:
                workflow_key = str(getattr(g, "workflow_key", "") or "")
                current_app.logger.warning(
                    "Career Bridge workflow conflict for %s: expected=%s actual=%s "
                    "request=%s last_updated_by=%s",
                    hashlib.sha256(workflow_key.encode("utf-8")).hexdigest()[:12],
                    exc.expected_version,
                    exc.actual_version,
                    str(getattr(g, "workflow_request_id", "") or ""),
                    exc.actual_updated_by_request,
                )
                return workflow_conflict_response(exc)
            return response
        finally:
            _record_job_discovery_phase(
                "jd_persist", persist_started_at, "Workflow persistence"
            )

    @application_builder_bp.context_processor
    def inject_common_template_values() -> dict[str, Any]:
        def application_builder_asset(filename: str) -> str:
            static_root = Path(application_builder_bp.static_folder or "")
            selected_filename = minified_asset_name(
                static_root,
                filename,
                enabled=bool(current_app.config.get("STATIC_USE_MINIFIED")),
            )
            return url_for(
                "application_builder.static",
                filename=selected_filename,
                v=current_app.config.get("STATIC_ASSET_VERSION", ""),
            )

        return {
            "application_builder_asset": application_builder_asset,
            "processing_mode_labels": PROCESSING_MODE_LABELS,
            "processing_mode_order": PROCESSING_MODE_ORDER,
            "reasoning_efforts": ("automatic",) + REASONING_EFFORTS,
            "reasoning_effort_label": reasoning_effort_label,
            "career_bridge_home_url": str(
                current_app.config.get("CAREER_BRIDGE_HOME_URL") or "/app"
            ),
            "is_admin_session": bool(session.get("is_admin")),
            "can_manage_job_catalog": _current_user_can_manage_job_catalog(),
            "active_application": getattr(g, "active_application", None),
            "reusable_career_profile": getattr(
                g, "reusable_career_profile", ReusableCareerProfile()
            ),
        }

    def state(*, hydrate_documents: bool = True) -> WorkflowState:
        workflow_state = g.workflow_state
        if hydrate_documents and not bool(
            getattr(g, "skip_workflow_document_hydration", False)
        ):
            _hydrate_workflow_documents(workflow_state)
        return workflow_state

    def update_job_fields() -> None:
        current = state()
        uploaded = request.files.get("job_file")
        if uploaded and uploaded.filename:
            current.job_description = normalize_job_description(
                uploaded.read().decode("utf-8", errors="replace")
            )
        else:
            current.job_description = normalize_job_description(
                request.form.get("job_description", current.job_description)
            )
        current.target_title = normalize_target_title(
            request.form.get("target_title", current.target_title)
        )
        current.career_background = career_background_from_form(
            request.form,
            target_role=current.target_title,
            base=current.career_background,
        )

    return {
        name: value
        for name, value in locals().items()
        if name != "namespace" and not name.startswith("__")
    }
