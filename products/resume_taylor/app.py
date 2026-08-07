from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4
from urllib.parse import urlsplit

from dotenv import load_dotenv
from botocore.exceptions import BotoCoreError, ClientError
from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    flash,
    g,
    current_app,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
    jsonify,
    has_app_context,
)
from werkzeug.local import LocalProxy

from career_bridge.countries import COUNTRY_OPTIONS
from career_bridge.static_assets import minified_asset_name
from career_bridge.profile_context import (
    ReusableCareerProfile,
    text_not_already_in_profile,
    values_not_already_in_profile,
)
from career_bridge.reusable_evidence import (
    find_best_evidence_match,
    normalize_evidence_text,
    stored_answer_fully_satisfies,
)
from job_discovery.application_conversion import DiscoveredJobApplicationService
from job_discovery.location_filter import job_matches_location_filters
from job_discovery.models import (
    CompanySource,
    DEFAULT_MAX_POSTING_AGE_DAYS,
    DiscoveredJob,
    DiscoveryJobDisposition,
    DiscoveryResultIndexSummary,
    DiscoveryResultRecord,
    DiscoverySearchPreferences,
    DiscoveryScanSchedule,
    DiscoveryScheduleCadence,
    JobSourceType,
    WorkplaceType,
    utc_now_iso,
)
from job_discovery.posting_age import evaluate_posting_age
from job_discovery.posting_details import PostingDescriptionFetcher
from job_discovery.public_catalog import (
    SHARED_CATALOG_SOURCE_OWNER_ID,
    public_source_key,
)
from job_discovery.ranking import (
    CandidateJobProfile,
    evaluate_stage_one,
    ranked_from_snapshot,
)
from job_discovery.result_policy import (
    DEFAULT_CONFIDENCE_TIERS,
    DEFAULT_MINIMUM_FIT,
    DEFAULT_RECOMMENDATION_FILTER,
    DEFAULT_SORT_MODE,
    DiscoveryResultFilters,
    assessed_sort_key,
    assessed_visibility_group,
    confidence_tier,
    parse_confidence_query,
    recommendation_tier,
)
from job_discovery.service import JobDiscoveryService
from job_discovery.source_import import (
    CompanySourceImportError,
    CompanySourceImportRow,
    MAX_SOURCE_IMPORT_BYTES,
    parse_company_source_import,
)
from job_discovery.scheduling import next_scheduled_run
from job_discovery.storage import (
    DiscoveryOptimisticLockError,
    DiscoveryStore,
    DynamoDBDiscoveryStore,
    InMemoryDiscoveryStore,
)
from job_discovery.background_worker import candidate_profile_payload
from career_bridge.async_jobs import (
    AsyncJob,
    AsyncJobStatus,
    AsyncJobStore,
    AsyncJobType,
    async_worker_health_payload,
    create_async_job_store,
    configured_async_job_backend,
)

from resume_tailor.ai import ResumeAI, ResumeAIError
from resume_tailor.terminology import (
    APPLICATION_BASELINE_LABEL,
    CAREER_BASELINE_RESUME_LABEL,
)
from resume_tailor.application_tracker import (
    APPLICATION_STATUS_OPTIONS,
    INTERVIEW_AUDIENCE_SUGGESTIONS,
    RESUME_VERSION_OPTIONS,
    UPCOMING_EVENT_TYPE_OPTIONS,
    build_application_metrics,
    normalize_application_status,
    normalize_iso_date,
    normalize_job_url,
)
from resume_tailor.application_fit import (
    ApplicationFitAssessment,
    build_application_fit_assessment,
)
from resume_tailor.interview_preparation import (
    InterviewPreparationWorkspace,
    build_verified_evidence_bundle,
    job_description_fingerprint,
    restrict_workspace_to_evidence,
)
from resume_tailor.impact_tracking import build_workflow_impact_snapshot
from resume_tailor.resume_findings import (
    ResumeFindingsSnapshot,
    build_resume_findings_snapshot,
    resume_findings_fingerprint,
)
from resume_tailor.evidence_fixes import apply_concrete_individual_audit_rephrase
from resume_tailor.export_naming import final_resume_filename, safe_filename
from resume_tailor.audit_identity import audit_issue_family
from resume_tailor.confirmation import (
    build_profile_with_candidate_answers,
    confirmation_dispositions,
    ensure_confirmed_answers_visible,
    is_candidate_confirmed_bullet_id,
    validate_candidate_answers,
)
from resume_tailor.baseline_manual import merge_candidate_profiles
from resume_tailor.baseline_role_updates import (
    append_manual_experience,
    apply_career_role_to_profile,
)
from resume_tailor.baseline_profile_updates import (
    append_baseline_education,
    apply_baseline_education,
    apply_baseline_skills,
    apply_baseline_summary,
    remove_baseline_education,
)
from resume_tailor.career_translation import ensure_career_translation_assessment
from resume_tailor.confirmation_followup import (
    MAX_TARGETED_FOLLOW_UP_ROUNDS,
    apply_final_follow_up_answers_locally,
    build_targeted_follow_up_questions,
    partition_targeted_follow_up_issues,
    split_post_confirmation_issues,
)
from resume_tailor.docx_export import TemplateError, export_resume_docx
from resume_tailor.pdf_export import PdfConversionError, export_resume_pdf
from resume_tailor.docx_styles import (
    RESUME_STYLE_THEMES,
    career_stage_options,
    career_stage_template_key,
    normalize_career_stage,
    normalize_resume_format,
    normalize_resume_style,
    normalize_visual_design,
    recommend_career_stage,
    recommend_resume_format,
    recommend_resume_style,
    recommend_visual_design,
    resume_format_options,
    resume_preference_label,
    resume_style_options,
    visual_design_options,
)
from resume_tailor.experience_comparison import classify_bullet_inclusion
from resume_tailor.model_config import (
    PROCESSING_MODE_LABELS,
    PROCESSING_MODE_ORDER,
    REASONING_EFFORTS,
    get_default_evidence_review_effort,
    get_default_evidence_review_model,
    get_default_analysis_tailoring_effort,
    get_default_analysis_tailoring_model,
    get_default_processing_mode,
    get_preset,
    validated_reasoning_effort,
)
from resume_tailor.models import (
    ApprovedResume,
    AuditIssue,
    CandidateAnswer,
    CandidateProfile,
    CandidateQuestion,
    ContactInfo,
    EducationItem,
    Experience,
    NewcomerCareerProfile,
    ProposalAudit,
    SkillSet,
    TailoringProposal,
    VerifiedSkills,
)
from resume_tailor.bullet_text import normalize_resume_bullet_terminal_punctuation
from resume_tailor.profile_io import (
    candidate_bullet_text,
    load_candidate_profile_bytes,
)
from resume_tailor.resume_import import (
    extract_resume_text,
    inherit_professional_contact_urls,
    restore_professional_contact_urls,
    resume_extension,
)
from resume_tailor.resume_language import (
    detect_text_language,
    language_name,
    normalize_language,
    resolve_resume_language,
    resume_labels,
    resume_language_options,
    translated_profile_fingerprint,
)
from resume_tailor.optimization import (
    FINAL_OPTIMIZATION_SECTIONS,
    final_optimization_actionable_issue_batches,
    final_optimization_score_guard,
)
from resume_tailor.proposal_changes import summarize_tailoring_changes
from resume_tailor.proposal_integrity import (
    DETERMINISTIC_DUPLICATE_PREFIX,
    DETERMINISTIC_EXCLUDE_PREFIX,
    DETERMINISTIC_INCLUDE_PREFIX,
    DETERMINISTIC_TRANSFERABLE_INCLUDE_PREFIX,
    is_auto_reconciled_exclusion,
    is_auto_reconciled_inclusion,
    is_duplicate_selection_exclusion,
    is_missing_selection_decision,
    repair_missing_bullet_proposals,
    selection_consistency_warnings,
)
from resume_tailor.question_prioritization import (
    candidate_question_display_label,
    order_candidate_questions_for_display,
    prioritize_candidate_questions,
)
from resume_tailor.skill_rules import (
    SKILL_CATEGORY_RULES,
    SKILL_TOTAL_MAXIMUM,
    SKILL_TOTAL_RECOMMENDED_MINIMUM,
    balance_skill_categories,
    skill_category_counts,
)
from resume_tailor.resume_async_jobs import (
    RESUME_ASYNC_JOB_TYPES,
    ResumeWorkflowAsyncProcessor,
    ResumeWorkflowJobError,
    active_resume_job_for_workflow,
    is_resume_async_job,
    queued_resume_job,
    resume_job_guard,
    resume_job_public_payload,
)
from resume_tailor.resume_report import (
    ResumeReport,
    build_evidence_gap_report,
    build_initial_resume_proposal,
    build_resume_report,
    initial_resume_title,
)
from resume_tailor.report_impacts import (
    attributable_bullet_report_impacts,
    comparison_view,
    tailoring_report_impacts,
)
from resume_tailor.text_diff import build_word_diff
from resume_tailor.deterministic_fixes import (
    apply_all_until_valid,
)
from resume_tailor.validation import (
    build_approved_resume,
    candidate_claim_grounding_issues,
    reconcile_audit_with_deterministic_rules,
    sentence_count,
    validate_proposal,
    word_count,
)
from resume_tailor.web_state import (
    WorkflowStepSnapshot,
    WorkflowState,
    initial_report_fingerprint,
    normalize_job_description,
    normalize_target_title,
)
from resume_tailor.storage import (
    ApplicationStore,
    WorkflowStore,
    WorkflowConflictError,
    configured_application_backend,
    configured_workflow_backend,
    create_application_store,
    create_workflow_store,
    normalize_workflow_request_id,
)
from resume_tailor.object_storage import (
    CareerBridgeObjectStore,
    ObjectNotFoundError,
    ObjectStorageError,
    application_object_key,
    configured_document_backend,
    create_document_store,
    workflow_object_key,
)
from resume_tailor.workflow_serialization import workflow_state_fingerprint

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
RESUME_TEMPLATE_PATHS = {
    "early_career": BASE_DIR / "data" / "resume_template_early_career.docx",
    "professional": BASE_DIR / "data" / "resume_template_professional.docx",
    "executive": BASE_DIR / "data" / "resume_template_executive.docx",
}
DEFAULT_JOB_PATH = BASE_DIR / "data" / "job_description_example.txt"
DEFAULT_JOB_DESCRIPTION = (
    DEFAULT_JOB_PATH.read_text(encoding="utf-8")
    if DEFAULT_JOB_PATH.exists()
    else ""
)
DEFAULT_JOB_DESCRIPTION_NORMALIZED = normalize_job_description(
    DEFAULT_JOB_DESCRIPTION
)

try:
    RESUME_PAGE_LIMIT = max(1, int(os.getenv("RESUME_PAGE_LIMIT", "2")))
except ValueError:
    RESUME_PAGE_LIMIT = 2


def _bounded_environment_seconds(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        configured = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        configured = default
    return min(maximum, max(minimum, configured))


# Long final optimization now runs through the durable Resume Workflow worker.


def resume_template_path(career_stage: str | None) -> Path:
    return RESUME_TEMPLATE_PATHS[career_stage_template_key(career_stage)]


def ensure_recommended_resume_style(state: WorkflowState) -> None:
    """Normalize and recommend independent resume preferences.

    The legacy ``resume_style`` field remains synchronized so saved sessions and
    application records created by older versions continue to work.
    """
    target_title = (
        state.analysis.target_title if state.analysis is not None else state.target_title
    )
    candidate_profile = state.confirmed_profile or state.source_profile
    candidate_answers = state.candidate_answers

    if state.resume_style_explicit and not state.resume_career_stage_explicit:
        state.resume_career_stage = normalize_career_stage(state.resume_style)
        state.resume_career_stage_explicit = True

    if not state.resume_career_stage_explicit:
        state.resume_career_stage = recommend_career_stage(
            state.job_description,
            target_title,
            candidate_profile=candidate_profile,
            candidate_answers=candidate_answers,
        )
    else:
        state.resume_career_stage = normalize_career_stage(
            state.resume_career_stage
        )

    if not state.resume_format_explicit:
        state.resume_format = recommend_resume_format(
            state.job_description,
            target_title,
            candidate_profile=candidate_profile,
            candidate_answers=candidate_answers,
        )
    else:
        state.resume_format = normalize_resume_format(state.resume_format)

    if not state.resume_visual_design_explicit:
        state.resume_visual_design = recommend_visual_design(
            state.job_description,
            target_title,
            resume_format=state.resume_format,
            career_stage=state.resume_career_stage,
            candidate_profile=candidate_profile,
            candidate_answers=candidate_answers,
        )
    else:
        state.resume_visual_design = normalize_visual_design(
            state.resume_visual_design
        )

    state.resume_style = career_stage_template_key(state.resume_career_stage)
    state.resume_style_explicit = state.resume_career_stage_explicit


def resume_export_kwargs(state: WorkflowState) -> dict[str, str]:
    return {
        "career_stage": normalize_career_stage(state.resume_career_stage),
        "resume_format": normalize_resume_format(state.resume_format),
        "visual_design": normalize_visual_design(state.resume_visual_design),
        "resume_language": _resolved_resume_language(state).name,
    }


def current_resume_preference_label(state: WorkflowState) -> str:
    return resume_preference_label(
        state.resume_career_stage, state.resume_format, state.resume_visual_design
    )


@dataclass(frozen=True)
class ActiveModels:
    analysis_tailoring_model: str
    evidence_review_model: str
    analysis_tailoring_reasoning_effort: str | None
    evidence_review_reasoning_effort: str | None
    description: str = ""
    warning: str = ""


def effective_final_resume_title(state: WorkflowState) -> str:
    title = normalize_target_title(state.final_resume_title)
    if title:
        return title
    if state.analysis is not None:
        return state.analysis.target_title
    return normalize_target_title(state.target_title)


def current_final_resume_filename(state: WorkflowState, extension: str) -> str:
    profile = state.final_report_profile or state.confirmed_profile or state.source_profile
    return final_resume_filename(profile, effective_final_resume_title(state), extension)


def parse_comma_list(value: str) -> list[str]:
    """Parse a compact list entered with commas, semicolons, or new lines."""
    seen: set[str] = set()
    items: list[str] = []
    for raw_item in re.split(r"[,;\n]+", value):
        item = " ".join(raw_item.split())
        key = item.casefold()
        if item and key not in seen:
            items.append(item)
            seen.add(key)
    return items


def _load_reusable_career_profile(owner_id: str) -> ReusableCareerProfile:
    """Load the account-level Career Profile without coupling its schema to a workflow."""

    try:
        from meeting_assistant.services.user_service import UserService

        return ReusableCareerProfile.from_mapping(
            UserService().get_assistant_context(str(owner_id or "").strip())
        )
    except Exception:
        # Application Builder can be embedded in isolated tests without the
        # Réunia user repository. In that case the resume remains authoritative.
        if has_app_context():
            current_app.logger.exception("Could not load reusable Career Profile")
        return ReusableCareerProfile()


def _bind_reusable_career_profile_context(owner_id: str) -> ReusableCareerProfile:
    """Make account-level profile context available to web and worker fingerprints.

    Resume workflow fingerprints include the effective Career Profile. Background
    workers run inside an application context but outside the request lifecycle, so
    they must explicitly load the same account-level profile that web requests use.
    """

    profile = _load_reusable_career_profile(owner_id)
    if has_app_context():
        g.reusable_career_profile = profile
        g.application_owner_id = str(owner_id or "").strip()
    return profile


def _merge_unique(existing: list[str], defaults: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in (*existing, *defaults):
        item = " ".join(str(raw or "").split())
        key = item.casefold()
        if item and key not in seen:
            values.append(item)
            seen.add(key)
    return values


def _career_background_with_profile(
    background: NewcomerCareerProfile,
    profile: ReusableCareerProfile,
    *,
    target_role: str = "",
) -> NewcomerCareerProfile:
    """Apply reusable profile defaults while preserving application-specific edits."""

    payload = profile.newcomer_payload(target_role=target_role)
    updated = background.model_copy(deep=True)
    for field in (
        "professional_headline",
        "current_role",
        "years_experience",
        "current_location",
        "target_country_experience",
        "work_preferences",
        "relocation_preferences",
        "work_authorization",
        "career_goals",
        "constraints",
        "career_profile_fingerprint",
    ):
        # These fields are account-level and are not edited in the application
        # workflow, so the latest saved Career Profile remains authoritative.
        setattr(updated, field, payload.get(field, ""))

    for field in (
        "preferred_roles",
        "core_skills",
        "key_accomplishments",
        "countries_worked",
        "industries",
        "roles",
        "languages",
        "international_credentials",
        "professional_certifications",
        "unfamiliar_job_titles",
        "career_transitions",
    ):
        setattr(
            updated,
            field,
            _merge_unique(list(getattr(updated, field)), tuple(payload.get(field) or ())),
        )

    if not updated.target_country:
        updated.target_country = str(payload.get("target_country") or "")
    if target_role:
        updated.target_role = normalize_target_title(target_role)
    elif not updated.target_role:
        updated.target_role = str(payload.get("target_role") or "")
    if not updated.us_employment_experience:
        updated.us_employment_experience = str(
            payload.get("us_employment_experience") or ""
        )
    return updated



def _effective_career_background(state: WorkflowState) -> NewcomerCareerProfile:
    """Return request-time profile context with the resolved resume language."""

    reusable = (
        getattr(g, "reusable_career_profile", ReusableCareerProfile())
        if has_app_context()
        else ReusableCareerProfile()
    )
    updated = _career_background_with_profile(
        state.career_background,
        reusable,
        target_role=state.target_title,
    )
    choice = resolve_resume_language(
        updated.target_country,
        state.career_background.resume_language,
        state.job_description,
    )
    # AI prompts receive the effective language name even when the form is set
    # to Automatic. The persisted field remains blank so the UI still reflects
    # that the user did not choose an override.
    updated.resume_language = choice.name
    return updated


def _resolved_resume_language(state: WorkflowState):
    effective = _career_background_with_profile(
        state.career_background,
        (
            getattr(g, "reusable_career_profile", ReusableCareerProfile())
            if has_app_context()
            else ReusableCareerProfile()
        ),
        target_role=state.target_title,
    )
    return resolve_resume_language(
        effective.target_country,
        state.career_background.resume_language,
        state.job_description,
    )


def _source_resume_language_code(state: WorkflowState) -> str:
    """Return the imported resume language, detecting it for older workflows."""

    stored = normalize_language(getattr(state, "source_resume_language", ""))
    if stored:
        return stored
    original = state.original_source_profile or state.source_profile
    detected = detect_text_language(original.all_source_text())
    if detected:
        state.source_resume_language = detected
    return detected


def _ensure_target_language_profile(
    state: WorkflowState,
    ai: ResumeAI | None,
) -> CandidateProfile:
    """Prepare the Imported Resume once per source/language combination.

    A resume that is already in the requested Baseline Resume language is copied
    directly from the immutable imported profile. No translation request is made.
    """

    if state.original_source_profile is None:
        # Workflows created before target-language translation was introduced
        # still need an immutable verified source baseline for later language
        # changes and for downloading the originally imported profile.
        state.original_source_profile = state.source_profile.model_copy(deep=True)
    original = state.original_source_profile
    choice = _resolved_resume_language(state)
    fingerprint = translated_profile_fingerprint(
        original, choice.code, choice.country
    )
    if (
        state.source_profile_translation_fingerprint == fingerprint
        and state.source_profile_language == choice.code
    ):
        return state.source_profile

    source_language = _source_resume_language_code(state)
    # Older workflows may contain a stale language value detected from noisy PDF
    # text. Re-check the canonical structured profile before paying for a
    # translation. When the profile is already in the target language, correct
    # the stored metadata and deterministically reuse the imported evidence.
    profile_language = detect_text_language(original.all_source_text())
    if profile_language and profile_language == choice.code:
        source_language = profile_language
        state.source_resume_language = profile_language
    if source_language and source_language == choice.code:
        state.source_profile = original.model_copy(deep=True)
        state.source_profile_language = choice.code
        state.source_profile_translation_fingerprint = fingerprint
        # A changed upload or target market can still invalidate cached results,
        # even when the source and output language are the same.
        state.clear_results()
        return state.source_profile

    if ai is None:
        raise ValueError(
            "AI translation must run through a durable Resume Workflow background job."
        )
    translated = ai.translate_candidate_profile(
        original,
        target_language=choice.code,
        target_country=choice.country,
    )
    state.source_profile = translated
    state.source_profile_language = choice.code
    state.source_profile_translation_fingerprint = fingerprint
    # Translation changes the reusable Baseline Resume. Cached reports
    # and tailoring proposals must therefore be rebuilt from the translated text.
    state.clear_results()
    return translated


def _backfill_professional_contact_links(
    state: WorkflowState,
    document_store: CareerBridgeObjectStore,
) -> bool:
    """Repair LinkedIn/GitHub links for resumes imported before hyperlink support.

    Older Word imports discarded the target behind hyperlink labels. The original
    document is already retained in object storage, so the Baseline Resume page
    can perform a one-time deterministic backfill without another AI request or
    requiring the user to upload the same resume again.
    """

    source_fingerprint = str(state.source_resume_fingerprint or "").strip()
    if (
        not state.source_resume_key
        or not state.profile_upload_name
        or not source_fingerprint
        or state.source_resume_contact_links_fingerprint == source_fingerprint
    ):
        return False

    # JSON imports already carry their contact fields explicitly and do not need
    # binary document inspection.
    if resume_extension(state.profile_upload_name) == ".json":
        state.source_resume_contact_links_fingerprint = source_fingerprint
        return False

    try:
        source_bytes = document_store.get(state.source_resume_key)
        resume_text = extract_resume_text(source_bytes, state.profile_upload_name)
    except ObjectNotFoundError:
        current_app.logger.warning(
            "Could not backfill resume contact links because the original document is missing: %s",
            state.source_resume_key,
        )
        state.source_resume_contact_links_fingerprint = source_fingerprint
        return False
    except Exception:
        current_app.logger.exception(
            "Could not backfill professional contact links from the original resume"
        )
        return False

    changed = False
    if state.original_source_profile is not None:
        restored_original = restore_professional_contact_urls(
            state.original_source_profile, resume_text
        )
        if restored_original.contact.model_dump() != state.original_source_profile.contact.model_dump():
            state.original_source_profile = restored_original
            changed = True

    restored_source = restore_professional_contact_urls(state.source_profile, resume_text)
    if restored_source.contact.model_dump() != state.source_profile.contact.model_dump():
        state.source_profile = restored_source
        changed = True

    state.source_resume_contact_links_fingerprint = source_fingerprint
    return changed


def _propagate_professional_contact_links(
    state: WorkflowState,
    source_profile: CandidateProfile | None = None,
) -> bool:
    """Repair missing professional URLs across application profile snapshots.

    Only blank LinkedIn/GitHub fields are filled. Existing application-specific
    contact values remain untouched, while older Application Baselines and later
    workflow snapshots gain links that were present in the verified import.
    """

    source = source_profile or state.source_profile
    if not (
        source.contact.linkedin_url.strip()
        or source.contact.github_url.strip()
    ):
        return False

    changed = False

    def repair(profile: CandidateProfile | None) -> CandidateProfile | None:
        nonlocal changed
        if profile is None:
            return None
        restored = inherit_professional_contact_urls(profile, source)
        if restored.contact.model_dump() != profile.contact.model_dump():
            changed = True
            return restored
        return profile

    state.source_profile = repair(state.source_profile) or state.source_profile
    state.original_source_profile = repair(state.original_source_profile)
    state.confirmed_profile = repair(state.confirmed_profile)
    state.final_report_profile = repair(state.final_report_profile)
    for snapshot in state.workflow_step_snapshots.values():
        snapshot.profile = repair(snapshot.profile)
    return changed


def _career_background_application_additions(
    background: NewcomerCareerProfile,
    profile: ReusableCareerProfile,
) -> NewcomerCareerProfile:
    """Return only application-specific context not already in Career Profile.

    Older workflow submissions copied the effective reusable profile values into
    the application record because the Career Translation form displayed those
    values as editable inputs.  Subtracting reusable values here both avoids
    asking the user to repeat them and quietly cleans those duplicates the next
    time the setup form is submitted.
    """

    payload = profile.newcomer_payload()
    additions = background.model_copy(deep=True)

    for field in (
        "countries_worked",
        "industries",
        "roles",
        "languages",
        "international_credentials",
        "professional_certifications",
        "unfamiliar_job_titles",
        "career_transitions",
    ):
        setattr(
            additions,
            field,
            values_not_already_in_profile(
                getattr(background, field),
                payload.get(field, ()),
            ),
        )

    additions.us_employment_experience = text_not_already_in_profile(
        background.us_employment_experience,
        payload.get("us_employment_experience"),
    )

    # These values are supplied directly by Career Profile or are displayed in
    # the application-specific Target country field, not in the additions panel.
    for field in (
        "professional_headline",
        "current_role",
        "years_experience",
        "current_location",
        "preferred_roles",
        "core_skills",
        "key_accomplishments",
        "target_country",
        "resume_language",
        "target_country_experience",
        "target_role",
        "work_preferences",
        "relocation_preferences",
        "work_authorization",
        "career_goals",
        "constraints",
        "career_profile_fingerprint",
    ):
        value = getattr(additions, field)
        setattr(additions, field, [] if isinstance(value, list) else "")

    return additions

def career_background_from_form(
    form: Any,
    *,
    target_role: str,
    base: NewcomerCareerProfile | None = None,
) -> NewcomerCareerProfile:
    """Update application-specific translation context and retain profile defaults."""

    current = (base or NewcomerCareerProfile()).model_copy(deep=True)
    current.countries_worked = parse_comma_list(form.get("countries_worked", ""))
    current.industries = parse_comma_list(form.get("career_industries", ""))
    current.roles = parse_comma_list(form.get("career_roles", ""))
    current.languages = parse_comma_list(form.get("career_languages", ""))
    current.target_country = " ".join(str(form.get("target_country", "")).split())
    current.resume_language = " ".join(str(form.get("resume_language", "")).split())
    current.target_role = normalize_target_title(target_role)
    current.international_credentials = parse_comma_list(
        form.get("international_credentials", "")
    )
    current.professional_certifications = parse_comma_list(
        form.get("professional_certifications", "")
    )
    current.unfamiliar_job_titles = parse_comma_list(
        form.get("unfamiliar_job_titles", "")
    )
    current.career_transitions = parse_comma_list(
        form.get("career_transitions", "")
    )
    current.us_employment_experience = " ".join(
        str(form.get("us_employment_experience", "")).split()
    )
    return current


def reasoning_effort_label(effort: str | None) -> str:
    return effort or "not used"


def _hash_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _proposal_json(proposal: TailoringProposal) -> str:
    # The Target-Market Review is advisory workflow metadata. It does
    # not change the resume document, report scores, or revision identity.
    return json.dumps(
        proposal.model_dump(exclude={"career_translation_assessment"}),
        ensure_ascii=False,
        sort_keys=True,
    )


def _proposal_fingerprint(proposal: TailoringProposal | None) -> str:
    if proposal is None:
        return ""
    return hashlib.sha256(_proposal_json(proposal).encode("utf-8")).hexdigest()


WORKFLOW_SNAPSHOT_STAGE_ORDER = ("initial", "confirmation", "draft", "final")
WORKFLOW_STEP_ORDER = (
    "setup",
    "confirmation",
    "review",
    "quality",
    "finalize",
    "evidence_export",
)
WORKFLOW_STEP_ALIASES = {
    "initial": "setup",
    "draft": "review",
    "final": "finalize",
}
WORKFLOW_PANEL_BY_STEP = {
    "setup": "initial",
    "confirmation": "confirmation",
    "review": "draft",
    "quality": "final",
    "finalize": "final",
    "evidence_export": "final",
}

# User-facing resume names follow the workflow outputs rather than the internal
# Draft/Final storage lifecycle. Keep these centralized so every comparison,
# heading, and download uses the same concise vocabulary.
INITIAL_RESUME_LABEL = APPLICATION_BASELINE_LABEL
JOB_ALIGNED_RESUME_LABEL = "Job-Aligned Resume"
FINAL_RESUME_LABEL = "Final Resume"

CAREER_TRANSLATION_CATEGORY_LABELS = {
    "job_title_translation": "Job titles that may be misunderstood",
    "credential_explanation": "Credentials requiring explanation",
    "regional_terminology": "Region-specific professional terminology",
    "hidden_accomplishment": "Accomplishments hidden by unfamiliar language",
    "transferable_skill": "Transferable skills",
    "unsupported_requirement": "Requirements kept outside the resume",
    "missing_evidence": "Experience questions to answer",
}
CAREER_TRANSLATION_CATEGORY_ORDER = tuple(CAREER_TRANSLATION_CATEGORY_LABELS)
CAREER_EVIDENCE_DISPOSITION_LABELS = {
    "confirmed_experience": "Confirmed experience",
    "reasonable_rephrasing": "Reasonable rephrasing",
    "user_clarification_required": "Evidence question available",
    "unsupported_claim": "Keep outside resume for now",
    "recommended_learning_or_future_action": "Confirmed development opportunity",
}
CAREER_EVIDENCE_DISPOSITION_DESCRIPTIONS = {
    "confirmed_experience": "Directly supported by traceable resume or confirmed-profile evidence.",
    "reasonable_rephrasing": "Facts stay unchanged while wording is translated for the target market.",
    "user_clarification_required": "A focused question can determine whether this experience may be used.",
    "unsupported_claim": "No verified evidence is linked yet, so the requirement stays outside the resume.",
    "recommended_learning_or_future_action": "The candidate explicitly confirmed this as a development area; it is never presented as current experience.",
}

# Keep routine, already-protected findings available for transparency without
# making Step 2 feel like a report the user must read line by line. Rephrasings
# involving titles, credentials, or hidden accomplishments remain visible because
# they can materially affect how the candidate is understood in the target market.
CAREER_TRANSLATION_MATERIAL_REPHRASING_CATEGORIES = {
    "job_title_translation",
    "credential_explanation",
    "hidden_accomplishment",
}


def _career_translation_finding_needs_review(
    category: str,
    disposition: str,
) -> bool:
    """Return whether the candidate must provide information or approve wording.

    Unsupported requirements are deliberately *not* action items. They remain
    visible under the collapsed ``No evidence found`` section, but the candidate
    does not need to answer or resolve them unless they choose to add experience.
    """

    if disposition == "user_clarification_required":
        return True
    if disposition == "reasonable_rephrasing":
        return category in CAREER_TRANSLATION_MATERIAL_REPHRASING_CATEGORIES
    return False


def _career_translation_review_bucket(disposition: str) -> str:
    """Group findings by the natural action the candidate should take."""

    if disposition in {"confirmed_experience", "reasonable_rephrasing"}:
        return "evidence_found"
    if disposition == "user_clarification_required":
        return "confirmation_needed"
    return "no_evidence"


def career_translation_assessment_view(
    proposal: TailoringProposal | None,
) -> dict[str, Any] | None:
    if proposal is None:
        return None
    assessment = proposal.career_translation_assessment
    if not assessment.summary and not assessment.findings:
        return None

    groups: list[dict[str, Any]] = []
    evidence_found_groups: list[dict[str, Any]] = []
    confirmation_needed_groups: list[dict[str, Any]] = []
    no_evidence_groups: list[dict[str, Any]] = []
    counts = {key: 0 for key in CAREER_EVIDENCE_DISPOSITION_LABELS}
    bucket_counts = {
        "evidence_found": 0,
        "confirmation_needed": 0,
        "no_evidence": 0,
    }
    material_rephrasing_count = 0

    for category in CAREER_TRANSLATION_CATEGORY_ORDER:
        findings: list[dict[str, Any]] = []
        bucket_rows: dict[str, list[dict[str, Any]]] = {
            "evidence_found": [],
            "confirmation_needed": [],
            "no_evidence": [],
        }
        for finding in assessment.findings:
            if finding.category != category:
                continue
            counts[finding.disposition] = counts.get(finding.disposition, 0) + 1
            evidence_labels = [
                (
                    f"Education credential {evidence_id.removeprefix('EDUCATION-')}"
                    if evidence_id.startswith("EDUCATION-")
                    else evidence_id
                )
                for evidence_id in finding.evidence_ids
            ]
            needs_review = _career_translation_finding_needs_review(
                category,
                finding.disposition,
            )
            if finding.disposition == "reasonable_rephrasing" and needs_review:
                material_rephrasing_count += 1
            bucket = _career_translation_review_bucket(finding.disposition)
            row = {
                "finding": finding,
                "evidence_labels": evidence_labels,
                "disposition_label": CAREER_EVIDENCE_DISPOSITION_LABELS[
                    finding.disposition
                ],
                "disposition_description": CAREER_EVIDENCE_DISPOSITION_DESCRIPTIONS[
                    finding.disposition
                ],
                "needs_review": needs_review,
                "review_bucket": bucket,
            }
            findings.append(row)
            bucket_rows[bucket].append(row)
            bucket_counts[bucket] += 1

        if findings:
            groups.append(
                {
                    "key": category,
                    "label": CAREER_TRANSLATION_CATEGORY_LABELS[category],
                    "findings": findings,
                }
            )
        for bucket, target in (
            ("evidence_found", evidence_found_groups),
            ("confirmation_needed", confirmation_needed_groups),
            ("no_evidence", no_evidence_groups),
        ):
            if bucket_rows[bucket]:
                target.append(
                    {
                        "key": category,
                        "label": CAREER_TRANSLATION_CATEGORY_LABELS[category],
                        "findings": bucket_rows[bucket],
                    }
                )

    # Legacy aliases remain available to reports and extensions while the
    # interactive Target-Market Review uses the three natural action buckets.
    attention_groups = confirmation_needed_groups
    reference_groups = evidence_found_groups

    return {
        "summary": assessment.summary,
        "target_country": assessment.target_country,
        "target_role": assessment.target_role,
        "groups": groups,
        "evidence_found_groups": evidence_found_groups,
        "confirmation_needed_groups": confirmation_needed_groups,
        "no_evidence_groups": no_evidence_groups,
        "evidence_found_count": bucket_counts["evidence_found"],
        "confirmation_needed_count": bucket_counts["confirmation_needed"],
        "no_evidence_count": bucket_counts["no_evidence"],
        "attention_groups": attention_groups,
        "reference_groups": reference_groups,
        "attention_count": bucket_counts["confirmation_needed"],
        "reference_count": bucket_counts["evidence_found"],
        "material_rephrasing_count": material_rephrasing_count,
        "routine_rephrasing_count": max(
            0, counts["reasonable_rephrasing"] - material_rephrasing_count
        ),
        "counts": counts,
        "disposition_labels": CAREER_EVIDENCE_DISPOSITION_LABELS,
        "disposition_descriptions": CAREER_EVIDENCE_DISPOSITION_DESCRIPTIONS,
    }


def capture_workflow_step_snapshot(
    state: WorkflowState,
    stage: str,
    *,
    proposal: TailoringProposal | None = None,
    profile: CandidateProfile | None = None,
) -> WorkflowStepSnapshot:
    """Capture the exact server-side state shown when a workflow step is completed."""
    if stage not in WORKFLOW_SNAPSHOT_STAGE_ORDER:
        raise ValueError(f"Unknown workflow snapshot stage: {stage}")
    captured = WorkflowStepSnapshot(
        stage=stage,
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        job_description=state.job_description,
        target_title=(
            effective_final_resume_title(state)
            if stage == "final"
            else state.target_title
        ),
        career_background=_effective_career_background(state),
        proposal=proposal.model_copy(deep=True) if proposal is not None else None,
        profile=(profile or state.confirmed_profile or state.source_profile).model_copy(
            deep=True
        ),
        candidate_answers=[
            answer.model_copy(deep=True) for answer in state.candidate_answers
        ],
        draft_revision=state.draft_revision,
        previous_draft_revision=state.previous_draft_revision,
        previous_draft_proposal=(
            state.previous_draft_proposal.model_copy(deep=True)
            if state.previous_draft_proposal is not None
            else None
        ),
        change_label=state.draft_last_change_label,
        changed_at=state.draft_last_changed_at,
    )
    state.workflow_step_snapshots[stage] = captured
    return captured


def discard_workflow_step_snapshots_after(
    state: WorkflowState,
    stage: str,
    *,
    include_stage: bool = False,
) -> None:
    """Discard snapshots invalidated by reopening an earlier workflow step."""
    if stage not in WORKFLOW_SNAPSHOT_STAGE_ORDER:
        return
    cutoff = WORKFLOW_SNAPSHOT_STAGE_ORDER.index(stage)
    for key in list(state.workflow_step_snapshots):
        key_index = (
            WORKFLOW_SNAPSHOT_STAGE_ORDER.index(key)
            if key in WORKFLOW_SNAPSHOT_STAGE_ORDER
            else len(WORKFLOW_SNAPSHOT_STAGE_ORDER)
        )
        if key_index > cutoff or (include_stage and key_index == cutoff):
            state.workflow_step_snapshots.pop(key, None)


def _approved_resume_from_proposal(
    profile: CandidateProfile,
    title: str,
    proposal: TailoringProposal,
    analysis: Any | None = None,
) -> ApprovedResume:
    """Build export content only after generated claims pass evidence grounding."""
    if analysis is not None:
        grounding_issues = candidate_claim_grounding_issues(profile, analysis, proposal)
        if grounding_issues:
            details = "; ".join(issue.issue for issue in grounding_issues[:3])
            raise ValueError(
                "Export blocked because generated candidate claims are not traceable "
                f"to verified evidence. {details}"
            )
    proposal_lookup = {
        item.source_bullet_id: item for item in proposal.bullet_proposals
    }
    bullets_by_experience: dict[str, list[str]] = {}
    for experience in profile.experiences:
        bullets_by_experience[experience.id] = [
            normalize_resume_bullet_terminal_punctuation(
                proposal_lookup[bullet.id].proposed_text
            )
            for bullet in experience.bullets
            if bullet.id in proposal_lookup
            and proposal_lookup[bullet.id].include
            and proposal_lookup[bullet.id].proposed_text.strip()
        ]

    return ApprovedResume(
        target_title=title.strip(),
        professional_summary=proposal.professional_summary.strip(),
        skills=proposal.skills,
        bullets_by_experience=bullets_by_experience,
    )




def _empty_candidate_profile() -> CandidateProfile:
    """Return a neutral profile for a user who has not imported a resume yet."""
    return CandidateProfile(
        name="",
        contact=ContactInfo(location="", phone="", email=""),
        current_summary="",
        skills=VerifiedSkills(),
        education=[],
        experiences=[],
    )


def _default_state() -> WorkflowState:
    return WorkflowState(
        source_profile=_empty_candidate_profile(),
        career_background=NewcomerCareerProfile(),
        # A new user has not selected a target job yet. Keep this empty rather
        # than exposing the bundled Barclays example in Career Translation.
        job_description="",
        processing_mode=get_default_processing_mode(),
        custom_analysis_tailoring_model=get_default_analysis_tailoring_model(),
        custom_evidence_review_model=get_default_evidence_review_model(),
        custom_analysis_tailoring_reasoning_effort=get_default_analysis_tailoring_effort(),
        custom_evidence_review_reasoning_effort=get_default_evidence_review_effort(),
    )


def resolve_models(state: WorkflowState) -> ActiveModels:
    if state.processing_mode != "custom":
        preset = get_preset(state.processing_mode)
        return ActiveModels(
            analysis_tailoring_model=preset.analysis_tailoring_model,
            evidence_review_model=preset.evidence_review_model,
            analysis_tailoring_reasoning_effort=preset.analysis_tailoring_reasoning_effort,
            evidence_review_reasoning_effort=preset.evidence_review_reasoning_effort,
            description=preset.description,
            warning=preset.warning,
        )

    analysis_tailoring_model = state.custom_analysis_tailoring_model.strip()
    evidence_review_model = state.custom_evidence_review_model.strip()
    if not analysis_tailoring_model or not evidence_review_model:
        raise ValueError("Both the analysis/tailoring model and evidence review model are required.")
    return ActiveModels(
        analysis_tailoring_model=analysis_tailoring_model,
        evidence_review_model=evidence_review_model,
        analysis_tailoring_reasoning_effort=validated_reasoning_effort(
            analysis_tailoring_model, state.custom_analysis_tailoring_reasoning_effort
        ),
        evidence_review_reasoning_effort=validated_reasoning_effort(
            evidence_review_model, state.custom_evidence_review_reasoning_effort
        ),
        description=(
            "Set separate models for analysis and tailoring, and for the "
            "independent evidence review used in Step 3."
        ),
    )


def input_fingerprint(state: WorkflowState, models: ActiveModels) -> str:
    return _hash_json(
        {
            "job_description": normalize_job_description(state.job_description),
            "target_title": normalize_target_title(state.target_title),
            "career_background": _effective_career_background(state).model_dump(mode="json"),
            "analysis_tailoring_model": models.analysis_tailoring_model,
            "analysis_tailoring_reasoning_effort": models.analysis_tailoring_reasoning_effort,
        }
    )




def preliminary_application_fit(
    state: WorkflowState,
    application_records: list[Any] | tuple[Any, ...] = (),
) -> ApplicationFitAssessment | None:
    """Return the pre-question fit score from the original resume evidence only.

    This baseline intentionally ignores candidate answers and the confirmed
    profile so it remains stable after Step 2 and can be compared with the
    verified score shown later in the workflow.
    """
    if state.analysis is None or state.initial_evidence_proposal is None:
        return None

    return build_application_fit_assessment(
        state.analysis,
        state.initial_evidence_proposal,
        state.source_profile,
        application_records=application_records,
        confirmation_complete=False,
    )


def current_application_fit(
    state: WorkflowState,
    application_records: list[Any] | tuple[Any, ...] = (),
) -> ApplicationFitAssessment | None:
    """Score application fit from job requirements and verified source evidence.

    The fit score intentionally does not read the rewritten resume text. Before
    confirmation it uses the original evidence proposal; afterward it uses the
    evidence decisions plus facts from Verified Resume Evidence. This prevents a
    more aggressive rewrite from manufacturing a higher apply recommendation.
    """
    if state.analysis is None or state.initial_evidence_proposal is None:
        return None

    proposal = state.initial_evidence_proposal
    if state.confirmed_profile is not None:
        proposal = (
            state.draft_proposal
            or state.provisional_proposal
            or state.initial_evidence_proposal
        )

    return build_application_fit_assessment(
        state.analysis,
        proposal,
        state.confirmed_profile or state.source_profile,
        candidate_answers=state.candidate_answers,
        application_records=application_records,
        confirmation_complete=state.confirmation_complete,
    )


def guided_stage_for_state(state: WorkflowState) -> str:
    """Return the current user-facing stage in the six-step Application Builder."""
    if state.workflow_stage == "initial":
        return "setup"
    if not state.confirmation_complete:
        return "confirmation"
    if state.workflow_stage == "draft":
        return "quality" if getattr(state, "quality_review_started", False) else "review"
    if state.final_resume_bytes is not None:
        return "evidence_export"
    if state.final_proposal is not None:
        return "finalize"
    return "quality"


def normalize_workflow_step(value: str | None, *, fallback: str) -> str:
    candidate = WORKFLOW_STEP_ALIASES.get((value or "").strip(), (value or "").strip())
    return candidate if candidate in WORKFLOW_STEP_ORDER else fallback


def build_guided_workflow(
    *,
    workflow_stage: str,
    input_is_current: bool,
    confirmation_complete: bool,
    blocking_local: bool,
    resume_ready: bool,
    quality_review_started: bool = False,
    final_proposal_ready: bool = False,
    application_id: str = "",
) -> dict[str, Any]:
    """Build the canonical six-step Application Builder status."""
    if workflow_stage == "initial":
        current_key = "setup"
        headline = "Current stage: Application and Job Setup"
        guidance = "Add the target job and imported resume, then analyze the match."
    elif not confirmation_complete:
        current_key = "confirmation"
        headline = "Current stage: Confirm Relevant Experience"
        guidance = "Confirm only evidence that truthfully supports this target role."
    elif workflow_stage == "draft" and not quality_review_started:
        current_key = "review"
        headline = "Current stage: Review Tailored Resume"
        guidance = (
            "Review every tailored change and confirm that the wording remains accurate."
            if input_is_current
            else "The job inputs changed. Return to Application and Job Setup before continuing."
        )
    elif workflow_stage != "final":
        current_key = "quality"
        headline = "Current stage: Improve Resume Quality"
        guidance = "Apply score-protected improvements without weakening evidence or job alignment."
    elif not final_proposal_ready:
        current_key = "quality"
        headline = "Current stage: Improve Resume Quality"
        guidance = (
            "Resolve the remaining blocking quality issue before finalizing."
            if blocking_local
            else "Run the score-protected quality pass."
        )
    elif not resume_ready:
        current_key = "finalize"
        headline = "Current stage: Finalize Resume"
        guidance = "Choose the final format, career stage, and visual design."
    else:
        current_key = "evidence_export"
        headline = "Workflow complete: Evidence Review and Export"
        guidance = "Review the final evidence-backed result and export the approved resume."

    status_rank = {
        "setup": 1,
        "confirmation": 2,
        "review": 3,
        "quality": 4,
        "finalize": 5,
        "evidence_export": 6,
    }
    current_rank = status_rank[current_key]
    labels = {
        "not_started": "Not started",
        "in_progress": "In progress",
        "completed": "Completed",
        "needs_attention": "Needs attention",
    }

    def step_status(key: str) -> str:
        rank = status_rank[key]
        if rank < current_rank:
            return "completed"
        if rank > current_rank:
            return "not_started"
        if key in {"confirmation", "review"} and not input_is_current:
            return "needs_attention"
        if key == "quality" and blocking_local:
            return "needs_attention"
        return "completed" if key == "evidence_export" and resume_ready else "in_progress"

    definitions = (
        ("setup", "Application and Job Setup", "Review the Baseline Resume, add the target job, and capture application-specific context.", "#job-input"),
        ("confirmation", "Confirm Relevant Experience", "Confirm the evidence that may support this application.", "#confirmation-stage"),
        ("review", "Review Tailored Resume", "Review the tailored resume and every evidence-backed change.", "#tailored-resume"),
        ("quality", "Improve Resume Quality", "Apply score-protected writing and searchability improvements.", "#resume-quality"),
        ("finalize", "Finalize Resume", "Choose the final structure, format, and visual design.", "#finalize-resume"),
        ("evidence_export", "Evidence Review and Export", "Review final evidence and export PDF or Word.", "#evidence-review-export"),
    )
    steps = []
    for number, (key, title, description, anchor) in enumerate(definitions, start=1):
        status = step_status(key)
        steps.append(
            {
                "number": number,
                "key": key,
                "title": title,
                "description": description,
                "status": status,
                "status_label": labels[status],
                "href": url_for(
                    "application_builder.index",
                    tab="tailoring",
                    stage=key,
                    **({"application_id": application_id} if application_id else {}),
                ) + anchor,
            }
        )
    return {
        "current_key": current_key,
        "headline": headline,
        "guidance": guidance,
        "steps": steps,
    }

def report_view(
    report: ResumeReport,
    *,
    profile=None,
    analysis=None,
    proposal: TailoringProposal | None = None,
    candidate_answers: list[CandidateAnswer] | None = None,
) -> dict[str, Any]:
    sections = []
    for section in report.sections():
        sections.append(
            {
                "name": section.name,
                "slug": re.sub(r"[^a-z0-9]+", "-", section.name.casefold()).strip("-"),
                "intro": section.intro,
                "score": section.score(),
                "subsections": [
                    {
                        "name": subsection.name,
                        "checks": [
                            {
                                "label": check.label,
                                "status": check.status,
                                "detail": check.detail,
                                "score": check.score(),
                            }
                            for check in subsection.checks
                        ],
                    }
                    for subsection in section.subsections
                ],
            }
        )

    evidence = None
    if profile is not None and analysis is not None and proposal is not None:
        summary, rows = build_evidence_gap_report(profile, analysis, proposal, candidate_answers)
        question_lookup = {
            item.id: item
            for item in (proposal.candidate_questions or [])
        }
        evidence = {
            "summary": {
                "supported": summary.supported,
                "partial": summary.partial,
                "unsupported": summary.unsupported,
                "confirmations": summary.candidate_confirmations,
            },
            "rows": [
                {
                    "requirement_id": row.requirement_id,
                    "priority": row.priority,
                    "category": row.category.replace("_", " "),
                    "requirement": row.requirement,
                    "evidence_status": row.evidence_status,
                    "appears_in_resume": row.appears_in_resume,
                    "score": row.score,
                    "evidence_locations": row.evidence_locations,
                    "rationale": row.rationale,
                    "recommended_action": row.recommended_action,
                }
                for row in rows
            ],
            "unsupported": list(proposal.unsupported_requirements),
            "questions": [item.question for item in proposal.candidate_questions],
            "answers": [
                {
                    "question": question_lookup.get(answer.question_id).question
                    if question_lookup.get(answer.question_id)
                    else (answer.question or answer.question_id),
                    "response": (
                        "Yes" if answer.yes_no is True else "No" if answer.yes_no is False else answer.text
                    ),
                    "details": answer.text or "—",
                    "requirement_id": answer.requirement_id or "—",
                }
                for answer in (candidate_answers or [])
            ],
        }

    return {
        "overall": report.overall_score(),
        "job_match": report.job_match_score(),
        "resume_quality": report.resume_quality_score(),
        "sections": sections,
        "evidence": evidence,
    }



def working_proposal_for_stage(state: WorkflowState) -> TailoringProposal | None:
    """Return the proposal needed by the active user-facing workflow step.

    The internal ``draft`` lifecycle covers both experience confirmation and the
    completed Job-Aligned Resume. Before confirmation finishes, Step 2 must read
    from ``provisional_proposal`` because ``draft_proposal`` is intentionally not
    created until the confirmed answers have been applied.
    """
    if state.workflow_stage == "final":
        return state.final_proposal
    if state.workflow_stage == "draft":
        if not state.confirmation_complete:
            return state.provisional_proposal
        return state.draft_proposal
    return None


def _refresh_initial_resume_report(
    state: WorkflowState,
    analysis,
    evidence_source: TailoringProposal,
    *,
    force: bool = False,
) -> bool:
    """Create the Step 1 report automatically without blocking tailoring on failure."""
    report_fingerprint = initial_report_fingerprint(state)
    if (
        not force
        and state.initial_report is not None
        and state.initial_report_input_fingerprint == report_fingerprint
    ):
        state.initial_report_error = ""
        return False

    initial_proposal = build_initial_resume_proposal(state.source_profile, evidence_source)
    initial_filename = safe_filename(
        f"{state.source_profile.name}_Initial_Resume"
    ) + ".docx"
    try:
        report = build_resume_report(
            state.source_profile,
            analysis,
            initial_proposal,
            generated_filename=initial_filename,
            template_path=resume_template_path(state.resume_career_stage),
            job_description=state.job_description,
            resume_title=initial_resume_title(state.source_profile),
            page_limit=RESUME_PAGE_LIMIT,
            **resume_export_kwargs(state),
        )
    except (TemplateError, ValueError) as exc:
        state.initial_report = None
        state.initial_report_input_fingerprint = None
        state.initial_report_analysis = None
        state.initial_report_proposal = None
        state.initial_report_created_at = ""
        state.initial_report_error = str(exc)
        return False

    state.initial_report = report
    state.initial_report_input_fingerprint = report_fingerprint
    state.initial_report_analysis = analysis.model_copy(deep=True)
    state.initial_report_proposal = initial_proposal.model_copy(deep=True)
    state.initial_report_created_at = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    state.initial_report_error = ""
    return True


def _refresh_job_aligned_resume_report(
    state: WorkflowState,
    profile: CandidateProfile,
    proposal: TailoringProposal,
    *,
    force: bool = True,
) -> bool:
    """Create or refresh the Step 3 report whenever its resume version changes."""
    if state.analysis is None:
        state.updated_report_error = (
            "Job analysis is required before creating the Job-Aligned Resume Report."
        )
        return False

    proposal_fingerprint = hashlib.sha256(
        _proposal_json(proposal).encode("utf-8")
    ).hexdigest()
    current_input = state.analyzed_input_fingerprint
    if (
        not force
        and state.updated_report is not None
        and state.updated_report_input_fingerprint == current_input
        and state.updated_report_proposal_fingerprint == proposal_fingerprint
    ):
        state.updated_report_error = ""
        return False

    style_label = current_resume_preference_label(state)
    filename = safe_filename(
        f"{profile.name}_{state.analysis.target_title}_Resume_{style_label}"
    ) + ".docx"
    try:
        report = build_resume_report(
            profile,
            state.analysis,
            proposal,
            generated_filename=filename,
            template_path=resume_template_path(state.resume_career_stage),
            job_description=state.job_description,
            resume_title=state.analysis.target_title,
            candidate_answers=state.candidate_answers,
            page_limit=RESUME_PAGE_LIMIT,
            **resume_export_kwargs(state),
        )
    except (TemplateError, ValueError) as exc:
        state.updated_report = None
        state.updated_report_input_fingerprint = None
        state.updated_report_proposal_fingerprint = None
        state.updated_report_created_at = ""
        state.updated_report_error = str(exc)
        return False

    state.updated_report = report
    state.updated_report_input_fingerprint = current_input
    state.updated_report_proposal_fingerprint = proposal_fingerprint
    state.updated_report_created_at = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    state.updated_report_error = ""
    return True


def store_working_proposal(
    state: WorkflowState,
    proposal: TailoringProposal,
    *,
    invalidate: bool = True,
    previous_proposal: TailoringProposal | None = None,
    change_label: str = "Saved changes",
) -> None:
    """Persist an edit into the active version and retain the prior Draft revision.

    Draft history is intentionally shallow: the immutable Initial baseline, the immediately
    previous Draft, and the current Draft. This is enough to explain the latest operation
    without turning the editor into a full version-control system.
    """
    profile = state.confirmed_profile or state.source_profile
    proposal = repair_missing_bullet_proposals(profile, proposal)

    if state.workflow_stage == "final":
        state.final_proposal = proposal
        if invalidate:
            state.clear_final_report()
        return

    prior = previous_proposal or state.draft_proposal
    changed = bool(
        prior is not None and _proposal_json(prior) != _proposal_json(proposal)
    )
    if changed and prior is not None:
        prior_revision = max(1, state.draft_revision or 1)
        state.previous_draft_proposal = prior.model_copy(deep=True)
        state.previous_draft_revision = prior_revision
        state.draft_revision = prior_revision + 1
        state.draft_last_change_label = change_label
        state.draft_last_changed_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
    elif state.draft_revision < 1:
        state.draft_revision = 1

    state.workflow_stage = "draft"
    state.draft_proposal = proposal
    if invalidate:
        state.clear_draft_report()
        state.clear_final_report()
    if state.confirmation_complete and state.analysis is not None and (
        changed or state.updated_report is None
    ):
        # Saving the Job-Aligned Resume only marks its report stale. The browser
        # refreshes it automatically after the updated resume is visible.
        state.clear_draft_report()


def proposal_view_data(
    profile: CandidateProfile,
    analysis,
    proposal: TailoringProposal,
    *,
    comparison_proposal: TailoringProposal | None = None,
    comparison_label: str = INITIAL_RESUME_LABEL,
    current_label: str = "Current Resume",
    comparison_title: str = "",
    current_title: str = "",
    bullet_fix_details: dict[str, Any] | None = None,
    bullet_tailoring_details: dict[str, Any] | None = None,
    summary_fix_detail: dict[str, Any] | None = None,
    skill_fix_details: dict[str, dict[str, Any]] | None = None,
    include_comparison_reasons: bool = False,
) -> dict[str, Any]:
    """Build the shared view model used by read-only and editable resume versions."""
    bullet_fix_details = bullet_fix_details or {}
    bullet_tailoring_details = bullet_tailoring_details or {}
    skill_fix_details = skill_fix_details or {}
    proposal = repair_missing_bullet_proposals(profile, proposal)
    selection_warnings = selection_consistency_warnings(profile, analysis, proposal)
    if comparison_proposal is not None:
        comparison_source_ids = {
            item.source_bullet_id for item in comparison_proposal.bullet_proposals
        }
        comparison_proposal = repair_missing_bullet_proposals(profile, comparison_proposal)
        # Candidate-confirmed bullets did not exist in the pre-confirmation resume.
        # Keep them absent from that comparison snapshot so they display as added,
        # rather than as unchanged auto-restored source bullets.
        comparison_proposal.bullet_proposals = [
            item
            for item in comparison_proposal.bullet_proposals
            if not is_candidate_confirmed_bullet_id(item.source_bullet_id)
            or item.source_bullet_id in comparison_source_ids
        ]
    proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
    has_comparison = comparison_proposal is not None
    comparison_summary = (
        comparison_proposal.professional_summary if comparison_proposal else proposal.professional_summary
    )
    summary_reference_html, summary_current_html = build_word_diff(
        comparison_summary, proposal.professional_summary
    )

    def comparison_reason(label: str, detail: str) -> dict[str, str] | None:
        if not include_comparison_reasons:
            return None
        return {"label": label, "detail": detail}

    def display_evidence_note(note: str) -> str:
        """Remove the status prefix already rendered by the comparison card."""

        value = note.strip()
        for prefix in (
            DETERMINISTIC_INCLUDE_PREFIX,
            DETERMINISTIC_TRANSFERABLE_INCLUDE_PREFIX,
            DETERMINISTIC_EXCLUDE_PREFIX,
            DETERMINISTIC_DUPLICATE_PREFIX,
        ):
            if value.startswith(prefix):
                value = value[len(prefix):].strip()
                break
        return re.sub(
            r"\s*Job relevance \d/3; evidence strength \d/2; unique coverage \d/2\.\s*$",
            "",
            value,
        ).strip()

    def selection_score(note: str) -> dict[str, int] | None:
        match = re.search(
            r"Job relevance (\d)/3; evidence strength (\d)/2; unique coverage (\d)/2",
            note,
        )
        if not match:
            return None
        relevance, evidence, unique = (int(value) for value in match.groups())
        return {
            "relevance": relevance,
            "evidence_strength": evidence,
            "unique_coverage": unique,
            "total": relevance + evidence + unique,
        }

    def compare_skills(reference_items: list[str], current_items: list[str]) -> dict[str, Any]:
        reference_by_key = {item.strip().casefold(): item.strip() for item in reference_items if item.strip()}
        current_by_key = {item.strip().casefold(): item.strip() for item in current_items if item.strip()}
        return {
            "added": [current_by_key[key] for key in current_by_key if key not in reference_by_key],
            "removed": [reference_by_key[key] for key in reference_by_key if key not in current_by_key],
            "unchanged": [current_by_key[key] for key in current_by_key if key in reference_by_key],
        }

    reference_skills = comparison_proposal.skills if comparison_proposal else proposal.skills
    skill_comparisons = [
        {
            "key": "hard_skills",
            "label": "Hard Skills",
            **compare_skills(reference_skills.hard_skills, proposal.skills.hard_skills),
        },
        {
            "key": "soft_skills",
            "label": "Soft Skills",
            **compare_skills(reference_skills.soft_skills, proposal.skills.soft_skills),
        },
        {
            "key": "tools_software",
            "label": "Tools & Software",
            **compare_skills(reference_skills.tools_software, proposal.skills.tools_software),
        },
        {
            "key": "industry_knowledge",
            "label": "Industry Knowledge",
            **compare_skills(reference_skills.industry_knowledge, proposal.skills.industry_knowledge),
        },
    ]
    for skill_comparison in skill_comparisons:
        skill_comparison["automatic_fix"] = skill_fix_details.get(
            skill_comparison["label"]
        )
        skill_comparison["comparison_reason"] = (
            comparison_reason(
                "Skills updated during resume refinement",
                "This skill group differs because relevant skills were added or removed while refining the resume for clarity, focus, and supported job alignment.",
            )
            if skill_comparison["added"] or skill_comparison["removed"]
            else None
        )

    title_reference_html, title_current_html = build_word_diff(
        comparison_title or current_title, current_title
    )
    comparison_lookup = (
        {item.source_bullet_id: item for item in comparison_proposal.bullet_proposals}
        if comparison_proposal
        else {}
    )
    requirement_lookup = {item.id: item for item in analysis.requirements} if analysis else {}
    experiences = []
    for experience in profile.experiences:
        bullets = []
        status_counts = {
            "unchanged": 0,
            "modified": 0,
            "added": 0,
            "excluded": 0,
            "excluded_unchanged": 0,
            "restored_missing_included": 0,
            "restored_missing_excluded": 0,
            "selection_missing": 0,
            "auto_reconciled_included": 0,
            "auto_reconciled_excluded": 0,
            "rewritten": 0,
        }
        for source_bullet in experience.bullets:
            # Both proposals are repaired above, so every source bullet has an explicit
            # structured inclusion decision before the comparison is rendered.
            item = proposal_lookup[source_bullet.id]
            comparison_item = comparison_lookup.get(source_bullet.id)
            current_include = item.include
            proposed_text = item.proposed_text
            if comparison_proposal is not None:
                reference_present = comparison_item is not None
                reference_include = bool(comparison_item and comparison_item.include)
                comparison_text = comparison_item.proposed_text if comparison_item else ""
            else:
                reference_present = True
                reference_include = current_include
                comparison_text = proposed_text

            rewritten_as_id = ""
            rewritten_text = ""
            rewritten_prefix = "User marked this source bullet as rewritten as "
            if (
                item is not None
                and not item.include
                and item.evidence_note.startswith(rewritten_prefix)
            ):
                rewritten_as_id = (
                    item.evidence_note[len(rewritten_prefix):]
                    .split(".", 1)[0]
                    .strip()
                )
                rewritten_item = proposal_lookup.get(rewritten_as_id)
                if rewritten_item is not None and rewritten_item.include:
                    rewritten_text = rewritten_item.proposed_text
                else:
                    rewritten_as_id = ""

            inclusion = classify_bullet_inclusion(
                reference_include=reference_include,
                current_include=current_include,
                reference_text=comparison_text,
                current_text=proposed_text,
                current_label=current_label,
                reference_label=comparison_label,
                reference_present=reference_present,
                current_present=item is not None,
                rewritten_as_id=rewritten_as_id,
                rewritten_text=rewritten_text,
            )
            inclusion_status = inclusion.status
            status_label = inclusion.label
            if is_missing_selection_decision(item):
                # Defensive legacy fallback. New proposals never expose a missing
                # model decision because deterministic code owns selection.
                inclusion_status = "auto_reconciled_excluded"
                status_label = "Not included — lower priority"
            elif is_auto_reconciled_inclusion(item):
                inclusion_status = "auto_reconciled_included"
                status_label = (
                    "Included — strong transferable evidence"
                    if item.evidence_note.startswith(DETERMINISTIC_TRANSFERABLE_INCLUDE_PREFIX)
                    else "Included — strong job match"
                )
            elif is_auto_reconciled_exclusion(item):
                inclusion_status = "auto_reconciled_excluded"
                status_label = (
                    "Not included — similar evidence already selected"
                    if is_duplicate_selection_exclusion(item)
                    else "Not included — lower priority"
                )

            selected_instead = []
            if not current_include:
                for selected_id in item.selected_instead_ids:
                    selected_item = proposal_lookup.get(selected_id)
                    if selected_item is None or not selected_item.include:
                        continue
                    selected_instead.append(
                        {
                            "id": selected_id,
                            "text": selected_item.proposed_text,
                            "reasons": item.selection_comparison_reasons.get(
                                selected_id, []
                            ),
                            "score": selection_score(selected_item.evidence_note),
                        }
                    )

            comparison_html, proposed_html = build_word_diff(
                inclusion.reference_for_diff, inclusion.current_for_diff
            )

            status_counts[inclusion_status] += 1
            bullet_comparison_reason = None
            if include_comparison_reasons:
                reason_by_status = {
                    "modified": (
                        "Reworded during resume refinement",
                        "The wording was adjusted for clarity, impact, or consistency while preserving the source-backed meaning of the accomplishment.",
                    ),
                    "added": (
                        "Added during resume refinement",
                        "This source-backed accomplishment was added after the comparison version to strengthen the current resume.",
                    ),
                    "excluded": (
                        "Removed during resume refinement",
                        "This accomplishment was left out after the comparison version to keep the current resume focused and concise.",
                    ),
                    "rewritten": (
                        "Replaced during resume refinement",
                        "The original bullet was replaced by a later source-backed version so the resume avoids duplicate or less effective wording.",
                    ),
                    "restored_missing_included": (
                        "Restored during resume refinement",
                        "The workflow restored this source-backed bullet so the current resume keeps a complete and explicit inclusion decision.",
                    ),
                    "restored_missing_excluded": (
                        "Restored for review",
                        "The workflow restored this source-backed bullet to the structured draft, but it remains excluded from the visible resume.",
                    ),
                    "selection_missing": (
                        "Evidence mapping restored",
                        "Career Bridge restored the verified source bullet and evaluated it with the deterministic Job Alignment selector.",
                    ),
                    "auto_reconciled_included": (
                        "Selected by Job Alignment",
                        "Career Bridge selected this verified accomplishment using job relevance, evidence strength, unique coverage, and duplication checks.",
                    ),
                    "auto_reconciled_excluded": (
                        "Not selected by Job Alignment",
                        (
                            "Career Bridge selected "
                            + ", ".join(
                                alternative["id"] for alternative in selected_instead
                            )
                            + " instead after comparing job relevance, evidence strength, unique coverage, and duplication within the role's bullet limit."
                            if selected_instead
                            else "Career Bridge ranked this verified accomplishment below stronger or less repetitive evidence within the available resume space."
                        ),
                    ),
                }
                reason_parts = reason_by_status.get(inclusion_status)
                if reason_parts:
                    bullet_comparison_reason = comparison_reason(*reason_parts)

            bullets.append(
                {
                    "id": source_bullet.id,
                    "include": current_include,
                    "reference_include": reference_include,
                    "comparison": inclusion.reference_for_diff,
                    "proposed": proposed_text,
                    "comparison_html": comparison_html,
                    "proposed_html": proposed_html,
                    "comparison_label": comparison_label,
                    "current_label": current_label,
                    "inclusion_status": inclusion_status,
                    "status_label": status_label,
                    "automatic_fix": bullet_fix_details.get(source_bullet.id),
                    "tailoring_reason": bullet_tailoring_details.get(source_bullet.id),
                    "comparison_reason": bullet_comparison_reason,
                    "rewritten_as_id": rewritten_as_id,
                    "rewritten_as_text": rewritten_text,
                    "evidence_note": item.evidence_note,
                    "evidence_note_display": display_evidence_note(item.evidence_note),
                    "selected_instead": selected_instead,
                    "selection_score": selection_score(item.evidence_note),
                    "matched_requirements": [
                        f"{rid}: {requirement_lookup[rid].requirement}"
                        for rid in item.matched_requirement_ids
                        if rid in requirement_lookup
                    ],
                    "selection_decision_missing": is_missing_selection_decision(item),
                }
            )
        experiences.append(
            {
                "id": experience.id,
                "employer": experience.employer,
                "title": experience.title,
                "location": experience.location,
                "dates": experience.dates,
                "bullets": bullets,
                "status_counts": status_counts,
                "retained_count": (
                    status_counts["unchanged"]
                    + status_counts["modified"]
                    + status_counts["auto_reconciled_included"]
                ),
                "rewritten_count": status_counts["rewritten"],
                "changed_count": status_counts["modified"],
                "added_count": status_counts["added"],
                "excluded_count": (
                    status_counts["excluded"]
                    + status_counts["auto_reconciled_excluded"]
                ),
                "selection_missing_count": status_counts["selection_missing"],
                "restored_count": (
                    status_counts["restored_missing_included"]
                    + status_counts["restored_missing_excluded"]
                ),
            }
        )
    return {
        "summary": proposal.professional_summary,
        "hard_skills": ", ".join(proposal.skills.hard_skills),
        "soft_skills": ", ".join(proposal.skills.soft_skills),
        "tools_software": ", ".join(proposal.skills.tools_software),
        "industry_knowledge": ", ".join(proposal.skills.industry_knowledge),
        "skill_category_counts": skill_category_counts(proposal.skills),
        "skill_category_rules": SKILL_CATEGORY_RULES,
        "skill_total_count": proposal.skills.total_count(),
        "skill_total_recommended_minimum": SKILL_TOTAL_RECOMMENDED_MINIMUM,
        "skill_total_maximum": SKILL_TOTAL_MAXIMUM,
        "experiences": experiences,
        "mutually_excluded_count": sum(
            experience["status_counts"]["excluded_unchanged"] for experience in experiences
        ),
        "selection_consistency_warnings": selection_warnings,
        "has_comparison": has_comparison,
        "comparison_label": comparison_label,
        "current_label": current_label,
        "title_comparison": {
            "reference": comparison_title or current_title,
            "current": current_title,
            "reference_html": title_reference_html,
            "current_html": title_current_html,
            "comparison_reason": (
                comparison_reason(
                    "Updated during resume refinement",
                    "The profile title differs from the comparison version because it was revised to keep the resume aligned with the target role.",
                )
                if (comparison_title or current_title) != current_title
                else None
            ),
        },
        "summary_automatic_fix": summary_fix_detail,
        "summary_comparison": {
            "reference": comparison_summary,
            "current": proposal.professional_summary,
            "reference_html": summary_reference_html,
            "current_html": summary_current_html,
            "reference_word_count": word_count(comparison_summary),
            "current_word_count": word_count(proposal.professional_summary),
            "reference_sentence_count": sentence_count(comparison_summary),
            "current_sentence_count": sentence_count(proposal.professional_summary),
            "comparison_reason": (
                comparison_reason(
                    "Refined for clarity and focus",
                    "The professional summary differs because its wording was refined to improve clarity, relevance, and recruiter readability while preserving supported experience.",
                )
                if comparison_summary != proposal.professional_summary
                else None
            ),
        },
        "skill_comparisons": skill_comparisons,
    }




def proposal_from_form(
    proposal: TailoringProposal,
    form,
    profile: CandidateProfile | None = None,
) -> TailoringProposal:
    updated = repair_missing_bullet_proposals(
        profile, proposal
    ) if profile is not None else proposal.model_copy(deep=True)
    if updated is proposal:
        updated = proposal.model_copy(deep=True)
    updated.professional_summary = form.get("professional_summary", "").strip()
    updated.skills = SkillSet(
        hard_skills=parse_comma_list(form.get("hard_skills", "")),
        soft_skills=parse_comma_list(form.get("soft_skills", "")),
        tools_software=parse_comma_list(form.get("tools_software", "")),
        industry_knowledge=parse_comma_list(form.get("industry_knowledge", "")),
    )
    rewritten_prefix = "User marked this source bullet as rewritten as "
    for item in updated.bullet_proposals:
        previous_include = item.include
        item.include = form.get(f"include__{item.source_bullet_id}") == "on"
        item.proposed_text = form.get(
            f"text__{item.source_bullet_id}", item.proposed_text
        ).strip()
        if item.include != previous_include:
            item.evidence_note = (
                "Candidate manually included this source-backed accomplishment."
                if item.include
                else "Candidate manually excluded this source-backed accomplishment."
            )
        elif item.include and item.evidence_note.startswith(rewritten_prefix):
            item.evidence_note = (
                "Restored after previously being marked as represented by another "
                "included bullet."
            )

    return updated


def profile_with_education_from_form(
    profile: CandidateProfile,
    form,
) -> CandidateProfile:
    """Return a profile copy with final-stage education edits applied."""
    updated = profile.model_copy(deep=True)
    education: list[EducationItem] = []
    for index, item in enumerate(profile.education):
        prefix = f"education__{index}__"
        credential = form.get(prefix + "credential", item.credential).strip()
        institution = form.get(prefix + "institution", item.institution).strip()
        location = form.get(prefix + "location", item.location).strip()
        date = form.get(prefix + "date", item.date).strip()
        detail = form.get(prefix + "detail", item.detail).strip()
        if not credential or not institution or not date:
            raise ValueError(
                f"Education entry {index + 1} requires a credential, institution, and date."
            )
        education.append(
            EducationItem(
                credential=credential,
                institution=institution,
                location=location,
                date=date,
                detail=detail,
            )
        )
    updated.education = education
    return updated


def collect_candidate_answers(
    questions: list[CandidateQuestion], form
) -> tuple[list[CandidateAnswer], dict[str, str]]:
    answers: list[CandidateAnswer] = []
    draft: dict[str, str] = {}
    for question in questions:
        choice = form.get(f"choice__{question.id}", "")
        text = form.get(f"answer__{question.id}", "").strip()
        experience_id = form.get(f"experience__{question.id}", "").strip()
        placement = form.get(f"placement__{question.id}", "auto").strip() or "auto"
        draft[f"choice__{question.id}"] = choice
        draft[f"answer__{question.id}"] = text
        draft[f"experience__{question.id}"] = experience_id
        draft[f"placement__{question.id}"] = placement
        yes_no = None
        if choice == "yes":
            yes_no = True
        elif choice == "no":
            yes_no = False
        answers.append(
            CandidateAnswer(
                question_id=question.id,
                question=question.question,
                requirement_id=question.requirement_id,
                answer_type=question.answer_type,
                yes_no=yes_no,
                text=text,
                experience_id=experience_id,
                placement=placement,
            )
        )
    return answers, draft











def _knowledge_evidence_service():
    # Imported lazily because this blueprint is registered by the main Réunia app.
    from meeting_assistant.services.knowledge_service import KnowledgeService

    return KnowledgeService()


def _baseline_role_entries(profile: CandidateProfile) -> list[dict[str, str]]:
    return [
        {
            "source_experience_id": experience.id,
            "official_title": experience.title,
            "employer": experience.employer,
            "dates": experience.dates,
            "location": experience.location,
            "responsibilities": "\n".join(
                f"• {bullet.text.strip()}"
                for bullet in experience.bullets
                if bullet.text.strip()
            ),
        }
        for experience in profile.experiences
        if experience.title.strip() and experience.employer.strip()
    ]


def _sync_baseline_roles_to_evidence_library(current: WorkflowState) -> bool:
    owner_id = str(getattr(g, "application_owner_id", "") or "").strip()
    if not owner_id or not current.source_profile.experiences:
        return True
    try:
        _knowledge_evidence_service().sync_career_roles_from_baseline(
            owner_id,
            _baseline_role_entries(current.source_profile),
            source_fingerprint=(
                current.source_profile_translation_fingerprint
                or current.source_resume_fingerprint
            ),
            target_market=_effective_career_background(current).target_country,
        )
    except Exception:
        current_app.logger.exception(
            "Could not synchronize Baseline Resume employment roles to Career Evidence Library"
        )
        return False
    return True


def _baseline_creation_method(current: WorkflowState) -> str:
    """Return the persisted or safely inferred Baseline Resume source type."""

    method = str(getattr(current, "baseline_creation_method", "") or "").strip()
    if method in {"import", "manual", "mixed"}:
        return method
    if current.profile_upload_name or current.source_resume_key:
        return "import"
    if current.source_profile.all_source_text().strip():
        return "manual"
    return ""


def _mark_manual_baseline_ready(current: WorkflowState) -> None:
    """Mark direct user entry as a ready, user-confirmed Baseline Resume."""

    current.baseline_creation_method = "manual"
    current.manual_source_profile = current.source_profile.model_copy(deep=True)
    current.original_source_profile = current.source_profile.model_copy(deep=True)
    choice = _resolved_resume_language(current)
    current.source_resume_language = choice.code
    current.source_profile_language = choice.code
    current.source_profile_translation_fingerprint = translated_profile_fingerprint(
        current.source_profile,
        choice.code,
        choice.country,
    )


def _refresh_manual_snapshot(current: WorkflowState) -> None:
    """Keep the direct-entry source synchronized while the baseline is manual."""

    if _baseline_creation_method(current) == "manual":
        _mark_manual_baseline_ready(current)


def _remove_baseline_experience(
    profile: CandidateProfile,
    source_experience_id: str,
) -> Experience | None:
    for index, experience in enumerate(profile.experiences):
        if str(experience.id or "").strip() == source_experience_id:
            return profile.experiences.pop(index)
    return None


def _education_identity(item: EducationItem) -> tuple[str, str, str]:
    return tuple(
        " ".join(str(value or "").casefold().split())
        for value in (item.credential, item.institution, item.date)
    )


def _matching_manual_education_index(
    profile: CandidateProfile | None,
    item: EducationItem,
) -> int | None:
    if profile is None:
        return None
    identity = _education_identity(item)
    for index, candidate in enumerate(profile.education):
        if _education_identity(candidate) == identity:
            return index
    return None


def _apply_confirmed_title_interpretations(
    owner_id: str,
    profile: CandidateProfile,
    proposal: TailoringProposal,
) -> TailoringProposal:
    """Resolve title findings with active, user-confirmed library records."""

    if not owner_id or not proposal.career_translation_assessment.findings:
        return proposal
    try:
        roles = _knowledge_evidence_service().list_career_roles(owner_id)
    except Exception:
        current_app.logger.exception(
            "Could not load confirmed employment roles for target-market review"
        )
        return proposal
    confirmed_roles = [
        role
        for role in roles
        if role.get("status") == "confirmed" and role.get("source_active", True)
    ]
    if not confirmed_roles:
        return proposal

    updated = proposal.model_copy(deep=True)
    for finding in updated.career_translation_assessment.findings:
        if finding.category != "job_title_translation":
            continue
        source_key = normalize_evidence_text(finding.source_text)
        evidence_ids = set(finding.evidence_ids)
        matches = [
            role
            for role in confirmed_roles
            if (
                str(role.get("source_experience_id") or "") in evidence_ids
                or source_key
                in {
                    normalize_evidence_text(str(role.get("official_title") or "")),
                    normalize_evidence_text(str(role.get("target_market_title") or "")),
                }
            )
        ]
        if not matches:
            continue
        interpretations = {
            (
                normalize_evidence_text(str(role.get("target_market_title") or "")),
                normalize_evidence_text(str(role.get("recruiter_explanation") or "")),
            )
            for role in matches
        }
        if len(matches) > 1 and len(interpretations) != 1:
            # The same official title can represent different functions at
            # different employers. Reuse it only when every confirmed record
            # agrees on the interpretation.
            continue
        role = matches[0]
        official_title = str(role.get("official_title") or finding.source_text).strip()
        target_title = str(role.get("target_market_title") or official_title).strip()
        explanation = str(role.get("recruiter_explanation") or "").strip()
        finding.disposition = "confirmed_experience"
        finding.translated_meaning = target_title
        if explanation:
            finding.translated_meaning = f"{target_title}. {explanation}"
        finding.rationale = (
            "The official title and its target-market interpretation were confirmed "
            "in Career Evidence Library and remain linked to the documented Baseline Resume role."
        )
        for matching_role in matches:
            source_experience_id = str(
                matching_role.get("source_experience_id") or ""
            ).strip()
            if source_experience_id and source_experience_id not in finding.evidence_ids:
                finding.evidence_ids.append(source_experience_id)
        finding.recommended_action = (
            "No further clarification is required. Keep the official title and use the "
            "confirmed target-market explanation only when it improves recruiter understanding."
        )
    return updated


def _resolve_reusable_experience_id(
    profile: CandidateProfile, stored: dict[str, Any]
) -> str:
    experience_lookup = profile.experience_lookup()
    stored_id = str(stored.get("experience_id") or "").strip()
    if stored_id in experience_lookup:
        return stored_id

    stored_employer = normalize_evidence_text(
        str(stored.get("experience_employer") or "")
    )
    stored_title = normalize_evidence_text(str(stored.get("experience_title") or ""))
    matches = [
        experience.id
        for experience in profile.experiences
        if (
            stored_employer
            and normalize_evidence_text(experience.employer) == stored_employer
            and (
                not stored_title
                or normalize_evidence_text(experience.title) == stored_title
            )
        )
    ]
    return matches[0] if len(matches) == 1 else ""


def _reusable_follow_up_question(
    question: CandidateQuestion,
    requirement_text: str,
    stored: dict[str, Any],
) -> CandidateQuestion:
    """Turn a related but incomplete saved answer into one narrow follow-up."""

    updated = question.model_copy(deep=True)
    topic = " ".join(str(requirement_text or "").split()).rstrip(".")
    if topic:
        updated.question = (
            f'You previously confirmed related experience for “{topic}.” '
            "Can you add one or two specific examples?"
        )
    else:
        updated.question = (
            "You previously confirmed related experience. "
            "Can you add one or two specific examples?"
        )
    previous_question = " ".join(str(stored.get("question") or "").split())
    updated.help_text = (
        "Career Evidence Library matched a related answer, but the new question "
        "asks for more specific evidence. Your previous answer is prefilled below."
    )
    if previous_question:
        updated.help_text += f" Previously answered: {previous_question}"
    updated.details_prompt = (
        "Keep the confirmed facts already shown and add what you personally did, "
        "the tools or techniques used, and the result or scope."
    )
    return updated


def _reuse_library_confirmation_answers(
    owner_id: str,
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> tuple[
    TailoringProposal,
    CandidateProfile | None,
    list[CandidateAnswer],
    dict[str, str],
]:
    """Reuse complete prior answers and prefill only missing-detail follow-ups."""

    if not proposal.candidate_questions:
        return proposal.model_copy(deep=True), None, [], {}
    try:
        service = _knowledge_evidence_service()
        stored_answers = service.list_evidence_answers(owner_id)
    except Exception:
        current_app.logger.exception(
            "Could not load reusable Career Evidence Library answers for %s", owner_id
        )
        return proposal.model_copy(deep=True), None, [], {}
    if not stored_answers:
        return proposal.model_copy(deep=True), None, [], {}

    requirement_lookup = {item.id: item for item in analysis.requirements}
    reused_answers: list[CandidateAnswer] = []
    reused_questions: list[CandidateQuestion] = []
    reused_ids: list[str] = []
    remaining_questions: list[CandidateQuestion] = []
    confirmation_draft: dict[str, str] = {}

    for question in proposal.candidate_questions:
        requirement = requirement_lookup.get(question.requirement_id)
        requirement_text = requirement.requirement if requirement else ""
        match, _score = find_best_evidence_match(
            question.question,
            requirement_text,
            stored_answers,
            answer_type=question.answer_type,
        )
        if match is None:
            remaining_questions.append(question.model_copy(deep=True))
            continue

        yes_no = match.get("yes_no")
        if yes_no not in (True, False, None):
            remaining_questions.append(question.model_copy(deep=True))
            continue
        answer_text = str(match.get("answer_text") or "").strip()
        experience_id = _resolve_reusable_experience_id(profile, dict(match))
        supports_evidence = yes_no is True or (
            yes_no is None and bool(answer_text)
        )
        placement = str(match.get("placement") or "auto")
        if placement not in {"auto", "update_existing", "new_bullet"}:
            placement = "auto"

        fully_satisfies = stored_answer_fully_satisfies(
            question.question,
            requirement_text,
            match,
            answer_type=question.answer_type,
            details_prompt=question.details_prompt,
        )
        if supports_evidence and not experience_id:
            fully_satisfies = False

        if not fully_satisfies and yes_no is not False:
            follow_up = _reusable_follow_up_question(
                question, requirement_text, dict(match)
            )
            remaining_questions.append(follow_up)
            confirmation_draft[f"choice__{question.id}"] = "yes" if answer_text else ""
            confirmation_draft[f"answer__{question.id}"] = answer_text
            confirmation_draft[f"experience__{question.id}"] = experience_id
            confirmation_draft[f"placement__{question.id}"] = placement
            continue

        answer_yes_no = yes_no
        if question.answer_type in {"yes_no", "yes_no_with_details"}:
            if answer_yes_no is None and answer_text:
                answer_yes_no = True
        answer = CandidateAnswer(
            question_id=question.id,
            question=question.question,
            requirement_id=question.requirement_id,
            answer_type=question.answer_type,
            yes_no=answer_yes_no,
            text=answer_text,
            experience_id=experience_id,
            placement=placement,
            reused_from_library=True,
            library_evidence_id=str(match.get("evidence_id") or ""),
        )
        if validate_candidate_answers([question], [answer]):
            follow_up = _reusable_follow_up_question(
                question, requirement_text, dict(match)
            )
            remaining_questions.append(follow_up)
            confirmation_draft[f"choice__{question.id}"] = (
                "no" if yes_no is False else "yes" if answer_text else ""
            )
            confirmation_draft[f"answer__{question.id}"] = answer_text
            confirmation_draft[f"experience__{question.id}"] = experience_id
            confirmation_draft[f"placement__{question.id}"] = placement
            continue
        reused_questions.append(question.model_copy(deep=True))
        reused_answers.append(answer)
        reused_ids.append(answer.library_evidence_id)

    updated = proposal.model_copy(deep=True)
    updated.candidate_questions = remaining_questions
    if not reused_answers:
        return updated, None, [], confirmation_draft

    confirmed_profile = build_profile_with_candidate_answers(
        profile, analysis, reused_questions, reused_answers
    )
    try:
        service.record_evidence_reuse(owner_id, reused_ids)
    except Exception:
        current_app.logger.exception(
            "Could not record reusable evidence usage for %s", owner_id
        )
    return updated, confirmed_profile, reused_answers, confirmation_draft


def _confirmation_evidence_entries(
    current: WorkflowState, answers: list[CandidateAnswer]
) -> list[dict[str, Any]]:
    profile = current.confirmed_profile or current.source_profile
    experiences = profile.experience_lookup()
    requirements = (
        {item.id: item for item in current.analysis.requirements}
        if current.analysis is not None
        else {}
    )
    application = getattr(g, "active_application", None)
    application_id = str(getattr(application, "id", "") or "")
    company = (
        str(getattr(application, "company", "") or "")
        or str(getattr(current.analysis, "target_company", "") or "")
    )
    target_title = (
        str(getattr(application, "role", "") or "")
        or current.target_title
        or str(getattr(current.analysis, "target_title", "") or "")
    )
    entries: list[dict[str, Any]] = []
    for answer in answers:
        experience = experiences.get(answer.experience_id)
        requirement = requirements.get(answer.requirement_id)
        entries.append(
            {
                "question": answer.question,
                "requirement": requirement.requirement if requirement else "",
                "answer_type": answer.answer_type,
                "yes_no": answer.yes_no,
                "answer_text": answer.text,
                "experience_id": answer.experience_id,
                "experience_label": (
                    f"{experience.employer} — {experience.title}" if experience else ""
                ),
                "experience_employer": experience.employer if experience else "",
                "experience_title": experience.title if experience else "",
                "placement": answer.placement,
                "source_application_id": application_id,
                "source_job_title": target_title,
                "source_company": company,
            }
        )
    return entries


def _save_confirmation_answers_to_library(
    owner_id: str, current: WorkflowState, answers: list[CandidateAnswer]
) -> int:
    entries = _confirmation_evidence_entries(current, answers)
    if not entries:
        return 0
    saved = _knowledge_evidence_service().save_evidence_answers(owner_id, entries)
    return len(saved)

def _normalize_audit_result(audit: ProposalAudit) -> ProposalAudit:
    """Keep pass/fail consistent with the application's documented blocking semantics."""
    has_blocking = any(issue.severity == "blocking" for issue in audit.issues)
    return audit.model_copy(update={"passed": not has_blocking})




def _run_reconciled_evidence_audit(
    audit_ai: ResumeAI,
    profile: CandidateProfile,
    analysis,
    proposal: TailoringProposal,
    career_background: NewcomerCareerProfile | None = None,
) -> ProposalAudit:
    """Run independent evidence review and reconcile objective rules deterministically."""
    return _normalize_audit_result(
        reconcile_audit_with_deterministic_rules(
            audit_ai.audit_proposal(
                profile, analysis, proposal, career_background
            ),
            proposal,
            profile,
            analysis,
        )
    )


def _build_optimization_report(
    state: WorkflowState,
    profile: CandidateProfile,
    proposal: TailoringProposal,
    filename: str,
    *,
    exact_page_count: bool = True,
) -> ResumeReport:
    """Build a comparable report using the exact same Step 4 inputs each time."""
    if state.analysis is None:
        raise ValueError("Job analysis is required before optimizing the resume.")
    return build_resume_report(
        profile,
        state.analysis,
        proposal,
        generated_filename=filename,
        template_path=resume_template_path(state.resume_career_stage),
        job_description=state.job_description,
        resume_title=state.analysis.target_title,
        candidate_answers=state.candidate_answers,
        page_limit=RESUME_PAGE_LIMIT,
        exact_page_count=exact_page_count,
        **resume_export_kwargs(state),
    )


def final_optimization_summary(
    before: ResumeReport | None,
    after: ResumeReport | None,
) -> list[dict[str, Any]]:
    """Return compact before/after category results for the consolidated final step."""
    if before is None and after is None:
        return []
    before_sections = {section.name: section for section in before.sections()} if before else {}
    after_sections = {section.name: section for section in after.sections()} if after else {}
    rows: list[dict[str, Any]] = []
    for name in FINAL_OPTIMIZATION_SECTIONS:
        before_section = before_sections.get(name)
        after_section = after_sections.get(name)
        before_open = sum(
            check.status in {"warning", "fail"}
            for subsection in before_section.subsections
            for check in subsection.checks
        ) if before_section else 0
        after_open = sum(
            check.status in {"warning", "fail"}
            for subsection in after_section.subsections
            for check in subsection.checks
        ) if after_section else 0
        before_score = before_section.score() if before_section else None
        after_score = after_section.score() if after_section else None
        rows.append(
            {
                "name": name,
                "before_score": before_score,
                "after_score": after_score,
                "score_delta": (
                    round(after_score - before_score, 1)
                    if before_score is not None and after_score is not None
                    else None
                ),
                "before_open": before_open,
                "after_open": after_open,
                "resolved": max(0, before_open - after_open),
            }
        )
    return rows


def final_optimization_recommendations(report: ResumeReport | None) -> list[dict[str, str]]:
    """Return remaining advisory checks from the five Step 4 optimization categories."""
    if report is None:
        return []
    items: list[dict[str, str]] = []
    for section in report.sections():
        if section.name not in FINAL_OPTIMIZATION_SECTIONS:
            continue
        for subsection in section.subsections:
            for check in subsection.checks:
                if check.status not in {"warning", "fail"}:
                    continue
                items.append(
                    {
                        "section": section.name,
                        "subsection": subsection.name,
                        "label": check.label,
                        "detail": check.detail,
                    }
                )
    return items


def _store_optimized_final_export(
    state: WorkflowState,
    profile: CandidateProfile,
    proposal: TailoringProposal,
    *,
    build_exact_report: bool = True,
) -> None:
    """Create the Final Resume export and optionally its exact rendered report.

    The interactive Step 3 → Step 4 transition uses ``build_exact_report=False``
    so the next screen can open as soon as the Word file and fast score-safe report
    are ready. Exact page rendering then continues through the automatic report
    endpoint after the page becomes interactive.
    """
    if state.analysis is None:
        raise ValueError("Job analysis is required before creating the Final Resume.")
    title = effective_final_resume_title(state)
    approved = _approved_resume_from_proposal(
        profile, title, proposal, state.analysis
    )
    resume_bytes = export_resume_docx(
        resume_template_path(state.resume_career_stage),
        profile,
        approved,
        **resume_export_kwargs(state),
    )
    state.final_proposal = proposal
    state.final_resume_bytes = resume_bytes
    state.final_report_filename = final_resume_filename(
        profile, title, "docx"
    )
    state.final_resume_pdf_bytes = None
    state.final_resume_pdf_error = ""
    if build_exact_report:
        try:
            _build_final_report_snapshot(state, profile, proposal, resume_bytes)
        except (TemplateError, ValueError) as exc:
            # A report-rendering failure must not discard an otherwise valid Word export.
            # Keep the export available and expose a retry action.
            state.final_report = None
            state.final_report_input_fingerprint = None
            state.final_report_proposal_fingerprint = None
            state.final_report_proposal = None
            state.final_report_profile = None
            state.final_report_candidate_answers = []
            state.final_report_created_at = ""
            state.final_report_error = str(exc)
            state.final_report_exact = False
    else:
        state.final_report_exact = False
        state.final_report_error = ""
    capture_workflow_step_snapshot(
        state,
        "final",
        proposal=proposal,
        profile=profile,
    )


def _store_fast_final_report_snapshot(
    state: WorkflowState,
    profile: CandidateProfile,
    proposal: TailoringProposal,
    report: ResumeReport,
) -> None:
    """Store a fast report immediately while exact page rendering is deferred."""
    if state.analysis is None:
        raise ValueError("Job analysis is required before storing the Final Resume Report.")
    title = effective_final_resume_title(state)
    filename = final_resume_filename(profile, title, "docx")
    proposal_fingerprint = _proposal_fingerprint(proposal)
    state.final_report = report
    state.final_report_input_fingerprint = state.analyzed_input_fingerprint
    state.final_report_proposal_fingerprint = proposal_fingerprint
    state.final_report_proposal = proposal.model_copy(deep=True)
    state.final_report_profile = profile.model_copy(deep=True)
    state.final_report_candidate_answers = [
        answer.model_copy(deep=True) for answer in state.candidate_answers
    ]
    state.final_report_created_at = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    state.final_report_filename = filename
    state.final_report_error = ""
    state.final_report_exact = False


def _build_final_report_snapshot(
    state: WorkflowState,
    profile: CandidateProfile,
    proposal: TailoringProposal,
    resume_bytes: bytes | None,
) -> None:
    if state.analysis is None:
        raise ValueError("Job analysis is required before creating the Final Resume Report.")

    title = effective_final_resume_title(state)
    filename = final_resume_filename(profile, title, "docx")
    report_filename = final_resume_filename(profile, title, "pdf")
    proposal_json = _proposal_json(proposal)
    proposal_fingerprint = hashlib.sha256(proposal_json.encode("utf-8")).hexdigest()
    state.final_report = build_resume_report(
        profile,
        state.analysis,
        proposal,
        generated_filename=report_filename,
        template_path=resume_template_path(state.resume_career_stage),
        job_description=state.job_description,
        resume_title=title,
        candidate_answers=state.candidate_answers,
        page_limit=RESUME_PAGE_LIMIT,
        generated_document_bytes=resume_bytes,
        exact_page_count=True,
        **resume_export_kwargs(state),
    )
    state.final_report_input_fingerprint = state.analyzed_input_fingerprint
    state.final_report_proposal_fingerprint = proposal_fingerprint
    state.final_report_proposal = proposal.model_copy(deep=True)
    state.final_report_profile = profile.model_copy(deep=True)
    state.final_report_candidate_answers = [
        answer.model_copy(deep=True) for answer in state.candidate_answers
    ]
    state.final_report_created_at = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    state.final_report_filename = filename
    state.final_report_error = ""
    state.final_report_exact = True
    state.final_resume_bytes = resume_bytes
    state.final_resume_pdf_bytes = None
    state.final_resume_pdf_error = ""


def _merge_candidate_answers(
    existing: list[CandidateAnswer],
    new_answers: list[CandidateAnswer],
) -> list[CandidateAnswer]:
    """Preserve earlier confirmation rounds while replacing duplicate question IDs."""
    merged = {answer.question_id: answer.model_copy(deep=True) for answer in existing}
    for answer in new_answers:
        merged[answer.question_id] = answer.model_copy(deep=True)
    return list(merged.values())


def _fallback_candidate_issue_to_verified_source(
    profile: CandidateProfile,
    proposal: TailoringProposal,
    issue: AuditIssue,
) -> TailoringProposal:
    """Remove generated uncertainty by restoring the narrowest verified source."""
    updated = proposal.model_copy(deep=True)
    source_id = issue.source_id.strip()
    family = audit_issue_family(issue)
    verified_source_text = candidate_bullet_text(profile, source_id)

    target = next(
        (
            item
            for item in updated.bullet_proposals
            if item.source_bullet_id == source_id
        ),
        None,
    )
    if target is not None and verified_source_text is not None:
        target.include = True
        target.proposed_text = verified_source_text
        target.evidence_note = (
            "Restored to the candidate-verified source wording because the stronger "
            "generated wording could not be fully verified."
        )
        return updated

    if family == "summary_evidence":
        updated.professional_summary = profile.current_summary
        return updated

    if family == "skill_evidence":
        finding_text = f"{issue.issue} {issue.suggested_fix}".casefold()
        removed = False
        for field in (
            "hard_skills",
            "soft_skills",
            "tools_software",
            "industry_knowledge",
        ):
            values = getattr(updated.skills, field)
            retained = [
                skill for skill in values if skill.casefold() not in finding_text
            ]
            removed = removed or len(retained) != len(values)
            setattr(updated.skills, field, retained)
        if removed:
            return updated

    requirement_match = re.search(
        r"\bR\d+\b",
        f"{source_id} {issue.issue} {issue.suggested_fix}",
        flags=re.IGNORECASE,
    )
    if requirement_match:
        requirement_id = requirement_match.group(0).upper()
        for match in updated.evidence_matches:
            if match.requirement_id == requirement_id:
                match.status = "unsupported"
                match.evidence_ids = []
                match.rationale = (
                    "The generated claim was reduced because the candidate profile "
                    "does not contain enough evidence to support it."
                )
        for bullet in updated.bullet_proposals:
            bullet.matched_requirement_ids = [
                item
                for item in bullet.matched_requirement_ids
                if item != requirement_id
            ]
    return updated


def _conservatively_resolve_candidate_findings(
    audit_ai: ResumeAI,
    profile: CandidateProfile,
    analysis,
    proposal: TailoringProposal,
    issues: list[AuditIssue],
    career_background: NewcomerCareerProfile | None = None,
) -> tuple[TailoringProposal, ProposalAudit]:
    """Resolve candidate-dependent findings with local source-backed edits.

    The former implementation could make several sequential model calls while
    trying to repair the same findings. Candidate-dependent claims now use exact
    auditor wording when available and otherwise fall back directly to verified
    source text. One independent re-audit verifies the completed local repair.
    """
    reviewed = proposal.model_copy(deep=True)
    for issue in issues:
        try:
            reviewed = apply_concrete_individual_audit_rephrase(reviewed, issue)
        except ValueError:
            reviewed = _fallback_candidate_issue_to_verified_source(
                profile, reviewed, issue
            )

    reviewed = repair_missing_bullet_proposals(profile, reviewed)
    reviewed = ensure_confirmed_answers_visible(profile, reviewed)
    reviewed, _ = apply_all_until_valid(profile, analysis, reviewed)
    audit = _run_reconciled_evidence_audit(
        audit_ai, profile, analysis, reviewed, career_background
    )
    return reviewed, audit


def _run_post_confirmation_evidence_review(
    models: ActiveModels,
    profile: CandidateProfile,
    analysis,
    proposal: TailoringProposal,
    career_background: NewcomerCareerProfile | None = None,
    *,
    allow_candidate_questions: bool = True,
    audit_ai: ResumeAI | None = None,
) -> tuple[TailoringProposal, ProposalAudit, list[AuditIssue]]:
    """Run one independent evidence audit and resolve safe fixes locally.

    This transition used to perform an audit, a work-model repair, another audit,
    and sometimes a third verification. Those sequential requests dominated the
    Step 2 → Step 3 wait. The refined proposal now receives one independent audit;
    explicit rephrases and source-backed fallbacks are applied locally, while
    nonblocking writing recommendations are left for the score-guarded Step 4
    optimizer. Candidate-dependent findings still produce the one allowed targeted
    follow-up round.
    """
    audit_ai = audit_ai or ResumeAI(
        model=models.evidence_review_model,
        reasoning_effort=models.evidence_review_reasoning_effort,
    )
    reviewed = proposal.model_copy(deep=True)
    audit = _run_reconciled_evidence_audit(
        audit_ai, profile, analysis, reviewed, career_background
    )
    candidate_needed, auto_fixable = split_post_confirmation_issues(audit.issues)

    if allow_candidate_questions:
        question_issues, conservative_issues = partition_targeted_follow_up_issues(
            candidate_needed
        )
    else:
        question_issues = []
        conservative_issues = candidate_needed

    changed = False

    # Apply exact auditor replacements immediately. When no concrete replacement
    # exists, use the narrowest verified source fallback for blocking evidence
    # findings; ordinary writing-quality recommendations can be handled in Step 4.
    for issue in auto_fixable:
        try:
            updated = apply_concrete_individual_audit_rephrase(reviewed, issue)
        except ValueError:
            updated = (
                _fallback_candidate_issue_to_verified_source(profile, reviewed, issue)
                if issue.severity == "blocking"
                else reviewed
            )
        changed = changed or _proposal_json(updated) != _proposal_json(reviewed)
        reviewed = updated

    # Findings outside the one allowed question set are made conservative directly
    # from verified source content instead of triggering more model round trips.
    for issue in conservative_issues:
        try:
            updated = apply_concrete_individual_audit_rephrase(reviewed, issue)
        except ValueError:
            updated = _fallback_candidate_issue_to_verified_source(
                profile, reviewed, issue
            )
        changed = changed or _proposal_json(updated) != _proposal_json(reviewed)
        reviewed = updated

    if changed:
        reviewed = repair_missing_bullet_proposals(profile, reviewed)
        reviewed = ensure_confirmed_answers_visible(profile, reviewed)
        reviewed, _ = apply_all_until_valid(profile, analysis, reviewed)

    return reviewed, audit, question_issues


application_builder_bp = Blueprint(
    "application_builder",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)


def _application_builder_storage_error_response(exc: Exception) -> tuple[str, int]:
    """Return a safe, actionable response when AWS persistence is unavailable."""

    error_code = exc.__class__.__name__
    if isinstance(exc, ClientError):
        error_code = str(exc.response.get("Error", {}).get("Code") or error_code)
    request_id = str(getattr(g, "request_id", "") or "")
    current_app.logger.exception(
        "Application Builder storage request failed: code=%s request_id=%s",
        error_code,
        request_id,
    )
    try:
        from meeting_assistant.services.server_error_reporting_service import (
            ServerErrorReportingService,
        )

        ServerErrorReportingService().report_safely(
            exc,
            status_code=503,
            reference_id=request_id or "unavailable",
        )
    except Exception:
        current_app.logger.exception(
            "Could not record Application Builder storage incident request_id=%s",
            request_id,
        )
    active_tab = request.args.get("tab", "applications")
    if request.path.endswith("/career-translation") or (
        request.path.endswith("/profile/upload")
        and str(request.form.get("return_to") or "").strip().casefold()
        == "career_translation"
    ):
        active_tab = "career_translation"
    elif request.path.endswith("/job-discovery"):
        active_tab = "discovery"
    return (
        render_template(
            "application_builder/storage_unavailable.html",
            active_tab=active_tab,
            storage_error_code=error_code,
            storage_request_id=request_id,
        ),
        503,
    )


@application_builder_bp.errorhandler(ClientError)
def handle_application_builder_client_error(exc: ClientError) -> tuple[str, int]:
    return _application_builder_storage_error_response(exc)


@application_builder_bp.errorhandler(BotoCoreError)
def handle_application_builder_boto_error(exc: BotoCoreError) -> tuple[str, int]:
    return _application_builder_storage_error_response(exc)


store: WorkflowStore = LocalProxy(
    lambda: current_app.extensions["career_bridge_workflow_store"]
)
application_store: ApplicationStore = LocalProxy(
    lambda: current_app.extensions["career_bridge_application_store"]
)
discovery_store: DiscoveryStore = LocalProxy(
    lambda: current_app.extensions["career_bridge_job_discovery_store"]
)
async_job_store: AsyncJobStore = LocalProxy(
    lambda: current_app.extensions["career_bridge_async_job_store"]
)
posting_description_fetcher: PostingDescriptionFetcher = LocalProxy(
    lambda: current_app.extensions["career_bridge_posting_description_fetcher"]
)
document_store: CareerBridgeObjectStore = LocalProxy(
    lambda: current_app.extensions["career_bridge_document_store"]
)


def _normalized_access_values(value: Any) -> set[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = ()
    return {str(item).strip().lower() for item in items if str(item).strip()}


def _current_user_can_manage_job_catalog() -> bool:
    if bool(session.get("is_admin")):
        return True
    user_id = str(session.get("user_id") or "").strip().lower()
    configured_users = _normalized_access_values(
        current_app.config.get("JOB_CATALOG_MANAGER_USER_IDS", ())
    )
    if user_id and user_id in configured_users:
        return True
    configured_groups = _normalized_access_values(
        current_app.config.get("JOB_CATALOG_MANAGER_GROUPS", ())
    )
    user_groups = _normalized_access_values(session.get("groups", ()))
    return bool(configured_groups & user_groups)


def _require_job_catalog_manager() -> None:
    if not _current_user_can_manage_job_catalog():
        abort(403, description="Job catalog management access is required.")


def _persist_workflow_documents(
    owner_id: str, workflow_key: str, workflow_state: WorkflowState
) -> None:
    """Externalize generated workflow documents before remote state persistence."""

    documents = (
        (
            "final_resume_bytes",
            "final_resume_docx_key",
            "final_resume_docx_fingerprint",
            "final-resume-docx",
            workflow_state.final_report_filename or "final-resume.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "final_resume_pdf_bytes",
            "final_resume_pdf_key",
            "final_resume_pdf_fingerprint",
            "final-resume-pdf",
            (
                Path(workflow_state.final_report_filename).with_suffix(".pdf").name
                if workflow_state.final_report_filename
                else "final-resume.pdf"
            ),
            "application/pdf",
        ),
    )
    for bytes_field, key_field, fingerprint_field, category, filename, content_type in documents:
        content = getattr(workflow_state, bytes_field)
        if not content:
            continue
        fingerprint = hashlib.sha256(content).hexdigest()
        if (
            getattr(workflow_state, fingerprint_field) == fingerprint
            and getattr(workflow_state, key_field)
        ):
            setattr(workflow_state, bytes_field, None)
            continue
        object_key = workflow_object_key(
            current_app.config,
            owner_id,
            workflow_key,
            category,
            filename,
            fingerprint,
        )
        document_store.put(
            object_key,
            content,
            content_type,
            metadata={
                "artifact-type": category,
                "workflow-namespace": hashlib.sha256(
                    workflow_key.encode("utf-8")
                ).hexdigest()[:24],
            },
        )
        previous_key = str(getattr(workflow_state, key_field) or "")
        setattr(workflow_state, key_field, object_key)
        setattr(workflow_state, fingerprint_field, fingerprint)
        setattr(workflow_state, bytes_field, None)
        if previous_key and previous_key != object_key:
            document_store.delete(previous_key)


def _delete_workflow_document_objects(
    workflow_state: WorkflowState, *, include_source: bool = True
) -> None:
    """Remove current object references when a workflow/application is deleted."""

    key_fields = ["final_resume_docx_key", "final_resume_pdf_key"]
    if include_source:
        key_fields.append("source_resume_key")
    for key_field in key_fields:
        object_key = str(getattr(workflow_state, key_field, "") or "")
        if object_key:
            document_store.delete(object_key)
            setattr(workflow_state, key_field, "")


def _hydrate_workflow_documents(workflow_state: WorkflowState) -> None:
    """Load S3-backed final documents only when a workflow needs their bytes."""

    for bytes_field, key_field in (
        ("final_resume_bytes", "final_resume_docx_key"),
        ("final_resume_pdf_bytes", "final_resume_pdf_key"),
    ):
        if getattr(workflow_state, bytes_field) is not None:
            continue
        object_key = str(getattr(workflow_state, key_field) or "")
        if not object_key:
            continue
        try:
            setattr(workflow_state, bytes_field, document_store.get(object_key))
        except ObjectNotFoundError:
            current_app.logger.warning(
                "Career Bridge workflow document is missing from object storage: %s",
                object_key,
            )


def async_worker_health_status() -> dict[str, Any]:
    """Return cached liveness metadata for the external async worker."""

    cache_seconds = max(
        0,
        int(
            current_app.config.get(
                "CAREER_BRIDGE_ASYNC_WORKER_HEALTH_CACHE_SECONDS"
            )
            or 10
        ),
    )
    now_monotonic = time.monotonic()
    cached = current_app.extensions.get(
        "career_bridge_async_worker_health_cache"
    )
    cache_fresh = (
        isinstance(cached, dict)
        and now_monotonic - float(cached.get("cached_at") or 0) <= cache_seconds
    )

    lookup_failed = False
    if cache_fresh:
        heartbeat = cached.get("heartbeat")
        lookup_failed = bool(cached.get("lookup_failed"))
    else:
        store = current_app.extensions.get("career_bridge_async_job_store")
        try:
            heartbeat = store.get_worker_heartbeat() if store is not None else None
        except Exception:
            current_app.logger.exception(
                "Could not read the async worker heartbeat for /health."
            )
            heartbeat = None
            lookup_failed = True
        current_app.extensions["career_bridge_async_worker_health_cache"] = {
            "cached_at": now_monotonic,
            "heartbeat": heartbeat,
            "lookup_failed": lookup_failed,
        }

    max_age_seconds = max(
        15,
        int(
            current_app.config.get("CAREER_BRIDGE_ASYNC_WORKER_MAX_AGE_SECONDS")
            or 90
        ),
    )
    payload = async_worker_health_payload(
        heartbeat, max_age_seconds=max_age_seconds
    )
    if lookup_failed:
        payload["status"] = "unavailable"
    payload["mode"] = "external"
    return payload


def application_builder_storage_status() -> dict[str, Any]:
    """Return non-secret storage capabilities for operational health checks.

    Job Discovery reports the adapter that is actually installed in the Flask
    extension registry rather than trusting configuration alone. This catches a
    standalone/local fallback to the in-memory store before a deployment is
    considered durable.
    """

    workflow_backend = configured_workflow_backend(current_app.config)
    application_backend = configured_application_backend(current_app.config)
    document_backend = configured_document_backend(current_app.config)

    configured_discovery_backend = str(
        current_app.config.get("CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND")
        or "memory"
    ).strip().casefold()
    discovery_store = current_app.extensions.get(
        "career_bridge_job_discovery_store"
    )
    if isinstance(discovery_store, DynamoDBDiscoveryStore):
        discovery_backend = "dynamodb"
    elif isinstance(discovery_store, InMemoryDiscoveryStore):
        discovery_backend = "memory"
    else:
        discovery_backend = configured_discovery_backend

    discovery_table = (
        str(
            current_app.config.get(
                "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME"
            )
            or ""
        ).strip()
        if discovery_backend == "dynamodb"
        else ""
    )
    discovery_persistent = (
        discovery_backend == "dynamodb" and bool(discovery_table)
    )
    async_job_backend = configured_async_job_backend(current_app.config)
    async_jobs_persistent = async_job_backend == "dynamodb" and bool(
        str(
            current_app.config.get("CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME")
            or discovery_table
            or ""
        ).strip()
    )
    fully_persistent = (
        workflow_backend == "dynamodb"
        and application_backend == "dynamodb"
        and discovery_persistent
        and async_jobs_persistent
        and document_backend == "s3"
    )
    return {
        "workflow_storage": workflow_backend,
        "application_storage": application_backend,
        "job_discovery_storage": discovery_backend,
        "job_discovery_table": discovery_table,
        "job_discovery_durability": (
            "persistent" if discovery_persistent else "ephemeral"
        ),
        "async_job_storage": async_job_backend,
        "async_job_durability": (
            "persistent" if async_jobs_persistent else "ephemeral"
        ),
        "async_worker_mode": "external",
        "document_storage": document_backend,
        "durability": "persistent" if fully_persistent else "mixed",
        "multi_worker_safe": fully_persistent,
        "multi_node_safe": fully_persistent,
    }


def init_application_builder(app: Flask) -> None:
    """Initialize configured Application Builder stores on the Réunia app."""

    app.config.setdefault("CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND", "memory")
    app.config.setdefault("CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND", "dynamodb")
    app.config.setdefault("CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND", "local")
    app.config.setdefault("CAREER_BRIDGE_DOCUMENTS_PREFIX", "career-bridge")
    workflow_backend = configured_workflow_backend(app.config)
    application_backend = configured_application_backend(app.config)
    document_backend = configured_document_backend(app.config)

    if document_backend == "local" and not str(
        app.config.get("CAREER_BRIDGE_DOCUMENTS_LOCAL_PATH") or ""
    ).strip():
        app.config["CAREER_BRIDGE_DOCUMENTS_LOCAL_PATH"] = str(
            Path(app.instance_path) / "career_bridge_documents"
        )

    if app.extensions.get("career_bridge_document_store") is None:
        app.extensions["career_bridge_document_store"] = create_document_store(
            app.config,
            require_s3=(workflow_backend == "dynamodb"),
        )

    if app.extensions.get("career_bridge_workflow_store") is None:
        app.extensions["career_bridge_workflow_store"] = create_workflow_store(
            app.config,
            _default_state,
            document_store=app.extensions["career_bridge_document_store"],
        )

    if app.config.get("TESTING") and app.config.get(
        "CAREER_BRIDGE_APPLICATIONS_TABLE_RESOURCE"
    ) is None:
        from resume_tailor.testing_dynamodb import InMemoryApplicationTable

        app.config["CAREER_BRIDGE_APPLICATIONS_TABLE_RESOURCE"] = (
            InMemoryApplicationTable()
        )
        app.config.setdefault(
            "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME",
            "careerbridge_test_applications",
        )

    if app.extensions.get("career_bridge_application_store") is None:
        app.extensions["career_bridge_application_store"] = (
            create_application_store(
                app.config,
                document_store=app.extensions["career_bridge_document_store"],
            )
        )

    # The Réunia shell normally initializes this extension. Keep a memory
    # adapter for standalone Builder tests and local embedding.
    if app.extensions.get("career_bridge_job_discovery_store") is None:
        app.extensions["career_bridge_job_discovery_store"] = InMemoryDiscoveryStore()

    if app.extensions.get("career_bridge_async_job_store") is None:
        app.extensions["career_bridge_async_job_store"] = create_async_job_store(
            app.config
        )

    if app.extensions.get("career_bridge_posting_description_fetcher") is None:
        app.extensions["career_bridge_posting_description_fetcher"] = (
            PostingDescriptionFetcher()
        )

    warning_key = "career_bridge_application_builder_persistence_warning_logged"
    if not app.extensions.get(warning_key):
        discovery_store = app.extensions.get(
            "career_bridge_job_discovery_store"
        )
        if isinstance(discovery_store, DynamoDBDiscoveryStore):
            discovery_backend = "dynamodb"
        elif isinstance(discovery_store, InMemoryDiscoveryStore):
            discovery_backend = "memory"
        else:
            discovery_backend = str(
                app.config.get(
                    "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND"
                )
                or "memory"
            ).strip().casefold()
        discovery_table = (
            str(
                app.config.get(
                    "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME"
                )
                or ""
            ).strip()
            if discovery_backend == "dynamodb"
            else ""
        )
        discovery_persistent = (
            discovery_backend == "dynamodb" and bool(discovery_table)
        )
        fully_persistent = (
            workflow_backend == "dynamodb"
            and application_backend == "dynamodb"
            and discovery_persistent
            and document_backend == "s3"
        )
        log = app.logger.info if fully_persistent else app.logger.warning
        log(
            "Application Builder storage configured: workflow=%s, "
            "applications=%s, job_discovery=%s, job_discovery_table=%s, "
            "documents=%s%s",
            workflow_backend,
            application_backend,
            discovery_backend,
            discovery_table or "<none>",
            document_backend,
            (
                ""
                if fully_persistent
                else "; one or more Career Bridge stores are not fully durable"
            ),
        )
        app.extensions[warning_key] = True


def _register_application_builder_routes() -> None:
    """Register Application Builder routes from feature-focused modules."""

    from products.resume_taylor.application_builder_routes import merge_exports
    from products.resume_taylor.application_builder_routes import application_context
    from products.resume_taylor.application_builder_routes import applications
    from products.resume_taylor.application_builder_routes import career_translation
    from products.resume_taylor.application_builder_routes import interview_preparation
    from products.resume_taylor.application_builder_routes import job_discovery
    from products.resume_taylor.application_builder_routes import lifecycle
    from products.resume_taylor.application_builder_routes import resume_workflow

    namespace = globals()
    for registrar in (
        lifecycle.register,
        application_context.register,
        resume_workflow.register,
        career_translation.register,
        interview_preparation.register,
        job_discovery.register,
        applications.register,
    ):
        merge_exports(namespace, registrar(namespace))


_register_application_builder_routes()
