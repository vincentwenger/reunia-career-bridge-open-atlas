from __future__ import annotations

from typing import Any


def register(namespace: dict[str, Any]) -> dict[str, Any]:
    """Register this feature's Application Builder routes and return shared helpers."""

    globals().update(namespace)
    @application_builder_bp.post("/career-translation/manual")
    def start_manual_baseline():
        """Open the shared Baseline Resume fields without requiring an upload."""

        current = state(hydrate_documents=False)
        if not current.source_profile.all_source_text().strip():
            current.baseline_creation_method = "manual"
            current.manual_source_profile = current.source_profile.model_copy(deep=True)
            flash(
                "Manual Baseline Resume started. Add your summary, skills, education, and employment history below; you can save each section independently.",
                "success",
            )
        else:
            flash(
                "Continue editing the Baseline Resume fields below. Saved changes are available to future applications automatically.",
                "info",
            )
        return redirect(
            url_for("application_builder.career_translation_workspace")
            + "#professional-summary"
        )

    @application_builder_bp.post("/career-translation/roles")
    def create_baseline_career_role():
        """Create one manually entered employment role in the Baseline Resume."""

        payload = request.get_json(silent=True) or {}
        official_title = " ".join(str(payload.get("official_title") or "").split())
        employer = " ".join(str(payload.get("employer") or "").split())
        if not official_title or not employer:
            return jsonify({"error": "Official job title and employer are required."}), 400
        normalized = {
            "official_title": official_title,
            "employer": employer,
            "dates": str(payload.get("dates") or "").strip(),
            "location": str(payload.get("location") or "").strip(),
            "responsibilities": str(payload.get("responsibilities") or "").strip(),
        }
        limits = {
            "official_title": 240,
            "employer": 240,
            "dates": 160,
            "location": 240,
            "responsibilities": 10000,
        }
        for field, limit in limits.items():
            if len(normalized[field]) > limit:
                return jsonify({"error": f"{field.replace('_', ' ').capitalize()} must be {limit:,} characters or fewer."}), 400

        current = state(hydrate_documents=False)
        experience = append_manual_experience(current.source_profile, normalized)
        method = _baseline_creation_method(current)
        if method == "manual" or not current.profile_upload_name:
            _mark_manual_baseline_ready(current)
        else:
            if current.manual_source_profile is None:
                current.manual_source_profile = _empty_candidate_profile()
            append_manual_experience(current.manual_source_profile, normalized)
            current.baseline_creation_method = "mixed"
        current.clear_results()
        roles_synced = _sync_baseline_roles_to_evidence_library(current)
        message = "Employment role added to the Baseline Resume."
        if not roles_synced:
            message += " Its title-review record will be synchronized when the page is regenerated."
        flash(message, "success")
        return jsonify(
            {
                "success": True,
                "baseline_updated": True,
                "experience_id": experience.id,
                "message": message,
            }
        )

    @application_builder_bp.put("/career-translation/roles/<role_id>")
    def update_baseline_career_role(role_id: str):
        """Save a reviewed role and update the reusable Baseline Resume source facts."""

        payload = request.get_json(silent=True) or {}
        owner_id = str(getattr(g, "application_owner_id", "") or "").strip()
        updated_role = _knowledge_evidence_service().update_career_role(
            owner_id,
            role_id,
            payload,
        )
        current = state(hydrate_documents=False)
        baseline_updated = bool(
            updated_role.get("source_active", True)
            and apply_career_role_to_profile(current.source_profile, updated_role)
        )
        if baseline_updated:
            if current.manual_source_profile is not None:
                apply_career_role_to_profile(current.manual_source_profile, updated_role)
            _refresh_manual_snapshot(current)
            current.clear_results()
            message = (
                "Employment role saved and the Baseline Resume was updated. "
                "Applications that have not started tailoring will use the revised baseline automatically."
            )
        else:
            message = (
                "Employment-role interpretation saved. The Baseline Resume content did not change "
                "because only review or target-market interpretation fields were updated."
            )
        flash(message, "success")
        return jsonify(
            {
                "success": True,
                "career_role": updated_role,
                "baseline_updated": baseline_updated,
                "message": message,
            }
        )

    @application_builder_bp.delete("/career-translation/roles/<role_id>")
    def delete_baseline_career_role(role_id: str):
        """Remove a title-review record and any directly entered manual role."""

        deleted = _knowledge_evidence_service().delete_career_role(
            str(getattr(g, "application_owner_id", "") or "").strip(),
            role_id,
        )
        current = state(hydrate_documents=False)
        source_experience_id = str(deleted.get("source_experience_id") or "").strip()
        baseline_updated = False
        if source_experience_id.startswith("MAN-EXP-"):
            baseline_updated = _remove_baseline_experience(
                current.source_profile, source_experience_id
            ) is not None
            if current.manual_source_profile is not None:
                _remove_baseline_experience(
                    current.manual_source_profile, source_experience_id
                )
            if baseline_updated:
                current.clear_results()
                _refresh_manual_snapshot(current)
        return jsonify(
            {
                "success": True,
                "career_role": deleted,
                "baseline_updated": baseline_updated,
            }
        )

    @application_builder_bp.put("/career-translation/summary")
    def update_baseline_summary():
        """Update the professional summary in the reusable Baseline Resume."""

        payload = request.get_json(silent=True) or {}
        summary = str(payload.get("current_summary") or "").strip()
        if len(summary) > 6000:
            return jsonify({"error": "Professional summary must be 6,000 characters or fewer."}), 400

        current = state(hydrate_documents=False)
        baseline_updated = apply_baseline_summary(current.source_profile, summary)
        if baseline_updated:
            _refresh_manual_snapshot(current)
            current.clear_results()
            message = (
                "Professional summary saved and the Baseline Resume was updated. "
                "Applications that have not started tailoring will use the revised baseline automatically."
            )
        else:
            message = "Professional summary is already up to date."
        flash(message, "success")
        return jsonify(
            {
                "success": True,
                "baseline_updated": baseline_updated,
                "current_summary": current.source_profile.current_summary,
                "message": message,
            }
        )

    @application_builder_bp.put("/career-translation/skills")
    def update_baseline_skills():
        """Update extracted skill categories in the reusable Baseline Resume."""

        payload = request.get_json(silent=True) or {}
        skill_fields = (
            "hard_skills",
            "soft_skills",
            "tools_software",
            "industry_knowledge",
            "languages",
        )
        normalized: dict[str, list[str]] = {}
        for field in skill_fields:
            raw_values = payload.get(field) or []
            if not isinstance(raw_values, list):
                return jsonify({"error": "Each skill category must be a list."}), 400
            values = [" ".join(str(value or "").split()) for value in raw_values]
            values = [value for value in values if value]
            if len(values) > 100:
                return jsonify({"error": "Each skill category may contain at most 100 entries."}), 400
            if any(len(value) > 240 for value in values):
                return jsonify({"error": "Each skill must be 240 characters or fewer."}), 400
            normalized[field] = values

        current = state(hydrate_documents=False)
        baseline_updated = apply_baseline_skills(current.source_profile, normalized)
        if baseline_updated:
            _refresh_manual_snapshot(current)
            current.clear_results()
            message = (
                "Skills saved and the Baseline Resume was updated. "
                "Applications that have not started tailoring will use the revised baseline automatically."
            )
        else:
            message = "Skills are already up to date."
        flash(message, "success")
        return jsonify(
            {
                "success": True,
                "baseline_updated": baseline_updated,
                "skills": current.source_profile.skills.model_dump(mode="json"),
                "message": message,
            }
        )

    @application_builder_bp.post("/career-translation/education")
    def create_baseline_education():
        """Add one manually entered education or credential record."""

        payload = request.get_json(silent=True) or {}
        credential = str(payload.get("credential") or "").strip()
        institution = str(payload.get("institution") or "").strip()
        if not credential or not institution:
            return jsonify({"error": "Credential and institution are required."}), 400
        limits = {
            "credential": 500,
            "institution": 500,
            "location": 300,
            "date": 160,
            "detail": 3000,
        }
        normalized = {field: str(payload.get(field) or "").strip() for field in limits}
        for field, limit in limits.items():
            if len(normalized[field]) > limit:
                return jsonify({"error": f"{field.replace('_', ' ').capitalize()} must be {limit:,} characters or fewer."}), 400

        current = state(hydrate_documents=False)
        education_index = append_baseline_education(current.source_profile, normalized)
        method = _baseline_creation_method(current)
        if method == "manual" or not current.profile_upload_name:
            _mark_manual_baseline_ready(current)
        else:
            if current.manual_source_profile is None:
                current.manual_source_profile = _empty_candidate_profile()
            append_baseline_education(current.manual_source_profile, normalized)
            current.baseline_creation_method = "mixed"
        current.clear_results()
        message = "Education record added to the Baseline Resume."
        flash(message, "success")
        return jsonify(
            {
                "success": True,
                "baseline_updated": True,
                "education_index": education_index,
                "message": message,
            }
        )

    @application_builder_bp.put("/career-translation/education/<int:education_index>")
    def update_baseline_education(education_index: int):
        """Update one extracted education record in the reusable Baseline Resume."""

        payload = request.get_json(silent=True) or {}
        credential = str(payload.get("credential") or "").strip()
        institution = str(payload.get("institution") or "").strip()
        if not credential or not institution:
            return jsonify({"error": "Credential and institution are required."}), 400
        limits = {
            "credential": 500,
            "institution": 500,
            "location": 300,
            "date": 160,
            "detail": 3000,
        }
        normalized = {
            field: str(payload.get(field) or "").strip()
            for field in limits
        }
        for field, limit in limits.items():
            if len(normalized[field]) > limit:
                label = field.replace("_", " ").capitalize()
                return jsonify({"error": f"{label} must be {limit:,} characters or fewer."}), 400

        current = state(hydrate_documents=False)
        original_item = (
            current.source_profile.education[education_index].model_copy(deep=True)
            if 0 <= education_index < len(current.source_profile.education)
            else None
        )
        try:
            baseline_updated = apply_baseline_education(
                current.source_profile, education_index, normalized
            )
        except IndexError:
            abort(404)
        if baseline_updated:
            if original_item is not None and current.manual_source_profile is not None:
                manual_index = _matching_manual_education_index(
                    current.manual_source_profile, original_item
                )
                if manual_index is not None:
                    apply_baseline_education(
                        current.manual_source_profile, manual_index, normalized
                    )
            _refresh_manual_snapshot(current)
            current.clear_results()
            message = (
                "Education record saved and the Baseline Resume was updated. "
                "Applications that have not started tailoring will use the revised baseline automatically."
            )
        else:
            message = "Education record is already up to date."
        flash(message, "success")
        return jsonify(
            {
                "success": True,
                "baseline_updated": baseline_updated,
                "education_index": education_index,
                "education": current.source_profile.education[education_index].model_dump(mode="json"),
                "message": message,
            }
        )

    @application_builder_bp.delete("/career-translation/education/<int:education_index>")
    def delete_baseline_education(education_index: int):
        """Remove one education record from the reusable Baseline Resume."""

        current = state(hydrate_documents=False)
        try:
            deleted = remove_baseline_education(
                current.source_profile, education_index
            )
        except IndexError:
            abort(404)
        if current.manual_source_profile is not None:
            manual_index = _matching_manual_education_index(
                current.manual_source_profile, deleted
            )
            if manual_index is not None:
                remove_baseline_education(
                    current.manual_source_profile, manual_index
                )
        current.clear_results()
        _refresh_manual_snapshot(current)
        message = (
            f"{deleted.credential or 'Education record'} was removed from the Baseline Resume. "
            "Applications that have not started tailoring will use the revised baseline automatically."
        )
        flash(message, "success")
        return jsonify(
            {
                "success": True,
                "baseline_updated": True,
                "education": deleted.model_dump(mode="json"),
                "message": message,
            }
        )

    @application_builder_bp.get("/career-translation")
    def career_translation_workspace():
        """Open the reusable, job-independent Career Translation foundation."""

        current = state()
        reusable_profile = getattr(
            g, "reusable_career_profile", ReusableCareerProfile()
        )
        # Career Profile is the single source of truth for the reusable
        # Baseline Resume market. This also migrates older foundation records
        # that stored a second editable target-country value.
        current.career_background.target_country = (
            reusable_profile.target_country if reusable_profile.enabled else ""
        )
        language_choice = _resolved_resume_language(current)
        contact_links_changed = _backfill_professional_contact_links(
            current, document_store
        )
        if (
            contact_links_changed
            and current.original_source_profile is not None
            and current.source_profile_language == language_choice.code
            and current.source_profile_translation_fingerprint
        ):
            # Contact fields are protected and do not require retranslation. Keep
            # an already translated baseline ready after the deterministic repair.
            current.source_profile_translation_fingerprint = translated_profile_fingerprint(
                current.original_source_profile,
                language_choice.code,
                language_choice.country,
            )
        source_profile = current.source_profile
        baseline_creation_method = _baseline_creation_method(current)
        baseline_has_content = bool(source_profile.all_source_text().strip())
        baseline_source_label = {
            "import": "Imported from resume",
            "manual": "Entered manually",
            "mixed": "Imported and manually supplemented",
        }.get(baseline_creation_method, "Not started")
        background = _effective_career_background(current)
        profile_stats = {
            "experiences": len(source_profile.experiences),
            "bullets": sum(
                len(experience.bullets)
                for experience in source_profile.experiences
            ),
            "skills": len(source_profile.all_verified_skills()),
            "education": len(source_profile.education),
        }
        original_profile = current.original_source_profile or source_profile
        expected_translation_fingerprint = translated_profile_fingerprint(
            original_profile, language_choice.code, language_choice.country
        )
        translation_ready = bool(
            source_profile.all_source_text().strip()
            and current.source_profile_language == language_choice.code
            and current.source_profile_translation_fingerprint
            == expected_translation_fingerprint
        )
        source_resume_language_code = _source_resume_language_code(current)
        source_resume_language_name = (
            language_name(source_resume_language_code)
            if source_resume_language_code
            else "Could not detect automatically"
        )
        no_translation_needed = bool(
            source_resume_language_code
            and source_resume_language_code == language_choice.code
        )
        preview_language_code = language_choice.code
        preview_language_name = language_choice.name
        try:
            career_roles = _knowledge_evidence_service().list_career_roles(
                _application_owner_id()
            )
        except Exception:
            current_app.logger.exception(
                "Could not load Baseline Resume employment roles"
            )
            career_roles = []
        if source_profile.all_source_text().strip() and not translation_ready:
            detected_source_language = (
                source_resume_language_code
                or detect_text_language(source_profile.all_source_text())
            )
            if detected_source_language:
                preview_language_code = detected_source_language
                preview_language_name = language_name(detected_source_language)
        workflow_key = str(getattr(g, "workflow_key", "") or "")
        resume_jobs = [
            job
            for job in async_job_store.list_for_owner(
                _application_owner_id(), limit=50
            )
            if is_resume_async_job(job)
            and str(job.payload.get("workflow_key") or "") == workflow_key
        ]
        active_resume_job = active_resume_job_for_workflow(
            resume_jobs, workflow_key
        )
        if active_resume_job is None and resume_jobs and resume_jobs[0].status in {
            AsyncJobStatus.FAILED,
            AsyncJobStatus.CANCELED,
        }:
            active_resume_job = resume_jobs[0]
        return render_template(
            "application_builder/career_translation.html",
            active_tab="career_translation",
            resume_async_job=(
                resume_job_public_payload(active_resume_job, url_for=url_for)
                if active_resume_job is not None
                else None
            ),
            state=current,
            source_profile=source_profile,
            original_source_profile=(
                current.original_source_profile or source_profile
            ),
            career_background=background,
            resume_language_choice=language_choice,
            resume_language_options=resume_language_options(),
            selected_resume_language=current.career_background.resume_language,
            resume_labels=resume_labels(preview_language_code),
            preview_language_name=preview_language_name,
            source_resume_language_name=source_resume_language_name,
            no_translation_needed=no_translation_needed,
            profile_stats=profile_stats,
            career_roles=career_roles,
            translation_ready=translation_ready,
            baseline_creation_method=baseline_creation_method,
            baseline_has_content=baseline_has_content,
            baseline_source_label=baseline_source_label,
            manual_baseline_requires_import_choice=(
                baseline_creation_method in {"manual", "mixed"}
                and baseline_has_content
            ),
        )

    return {
        name: value
        for name, value in locals().items()
        if name != "namespace" and not name.startswith("__")
    }
