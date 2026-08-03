from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from time import perf_counter
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
from job_discovery.sources.workday import parse_workday_careers_url
from job_discovery.sources.successfactors import successfactors_search_url
from job_discovery.sources.oracle_cloud_hcm import (
    parse_oracle_cloud_hcm_careers_url,
)
from job_discovery.sources.icims import parse_icims_careers_url
from job_discovery.sources.smartrecruiters import parse_smartrecruiters_careers_url
from job_discovery.sources.avature import parse_avature_careers_url
from job_discovery.sources.eightfold import parse_eightfold_careers_url
from job_discovery.sources.taleo import parse_taleo_careers_url
from job_discovery.sources.dayforce import parse_dayforce_careers_url
from job_discovery.sources.talemetry_ttc import parse_talemetry_ttc_careers_url
from job_discovery.sources.jobvite import parse_jobvite_careers_url
from job_discovery.sources.ukg_pro import parse_ukg_pro_careers_url
from job_discovery.sources.peopleadmin import parse_peopleadmin_careers_url
from job_discovery.sources.radancy_talentbrew import (
    parse_radancy_talentbrew_careers_url,
)
from job_discovery.sources.amazon_jobs import parse_amazon_jobs_careers_url
from job_discovery.sources.branded_requisition import (
    parse_branded_requisition_careers_url,
)
from job_discovery.scheduling import next_scheduled_run
from job_discovery.storage import (
    DiscoveryOptimisticLockError,
    DiscoveryStore,
    DynamoDBDiscoveryStore,
    InMemoryDiscoveryStore,
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


# Keep the synchronous Step 3 -> Step 4 request safely below the web gateway
# timeout. The AI pass is optional: the approved Job-Aligned Resume remains the
# protected fallback whenever the provider cannot finish inside this budget.
FINAL_OPTIMIZATION_REQUEST_BUDGET_SECONDS = _bounded_environment_seconds(
    "CAREER_BRIDGE_FINAL_OPTIMIZATION_REQUEST_BUDGET_SECONDS",
    50.0,
    minimum=20.0,
    maximum=55.0,
)
FINAL_OPTIMIZATION_EXPORT_RESERVE_SECONDS = _bounded_environment_seconds(
    "CAREER_BRIDGE_FINAL_OPTIMIZATION_EXPORT_RESERVE_SECONDS",
    8.0,
    minimum=5.0,
    maximum=15.0,
)
FINAL_OPTIMIZATION_AI_TIMEOUT_SECONDS = _bounded_environment_seconds(
    "CAREER_BRIDGE_FINAL_OPTIMIZATION_AI_TIMEOUT_SECONDS",
    32.0,
    minimum=5.0,
    maximum=40.0,
)


def _final_optimization_ai_timeout_seconds(started_at: float) -> float:
    """Return the provider time still safe for this interactive request."""
    elapsed = max(0.0, perf_counter() - started_at)
    remaining = (
        FINAL_OPTIMIZATION_REQUEST_BUDGET_SECONDS
        - FINAL_OPTIMIZATION_EXPORT_RESERVE_SECONDS
        - elapsed
    )
    if remaining < 5.0:
        return 0.0
    return min(FINAL_OPTIMIZATION_AI_TIMEOUT_SECONDS, remaining)


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
    ai: ResumeAI,
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
    if source_language and source_language == choice.code:
        state.source_profile = original.model_copy(deep=True)
        state.source_profile_language = choice.code
        state.source_profile_translation_fingerprint = fingerprint
        # A changed upload or target market can still invalidate cached results,
        # even when the source and output language are the same.
        state.clear_results()
        return state.source_profile

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
    audit_ai = ResumeAI(
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
    fully_persistent = (
        workflow_backend == "dynamodb"
        and application_backend == "dynamodb"
        and discovery_persistent
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
            "test-career-bridge-applications",
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
        return {
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

    def _split_discovery_values(raw: str) -> tuple[str, ...]:
        values: list[str] = []
        for item in re.split(r"[,\n;]+", str(raw or "")):
            value = " ".join(item.split())
            if value and value.casefold() not in {existing.casefold() for existing in values}:
                values.append(value)
        return tuple(values)

    def _source_identifier_value(
        source_type: JobSourceType, raw: str, careers_url: str = ""
    ) -> str:
        value = str(raw or "").strip()
        candidate_url = value if "://" in value else str(careers_url or "").strip()
        if source_type is JobSourceType.WORKDAY and candidate_url:
            return parse_workday_careers_url(
                candidate_url,
                site_identifier="" if "://" in value else value,
            ).site
        if source_type is JobSourceType.SUCCESSFACTORS:
            return value.strip().strip("/")
        if source_type in {
            JobSourceType.ORACLE_CLOUD_HCM,
            JobSourceType.ICIMS,
            JobSourceType.SMARTRECRUITERS,
            JobSourceType.AVATURE,
            JobSourceType.EIGHTFOLD,
            JobSourceType.TALEO,
            JobSourceType.DAYFORCE,
            JobSourceType.TALEMETRY_TTC,
            JobSourceType.JOBVITE,
            JobSourceType.UKG_PRO,
            JobSourceType.PEOPLEADMIN,
            JobSourceType.RADANCY_TALENTBREW,
            JobSourceType.AMAZON_JOBS,
        }:
            return ""
        if candidate_url and "://" in candidate_url:
            parsed = urlsplit(candidate_url)
            path_parts = [part for part in parsed.path.split("/") if part]
            if path_parts:
                value = path_parts[0]
        return value.strip().strip("/")

    def _default_source_url(source_type: JobSourceType, identifier: str) -> str:
        value = str(identifier or "").strip().strip("/")
        if not value:
            return ""
        if source_type is JobSourceType.GREENHOUSE:
            return f"https://boards.greenhouse.io/{value}"
        if source_type is JobSourceType.LEVER:
            return f"https://jobs.lever.co/{value}"
        if source_type is JobSourceType.ASHBY:
            return f"https://jobs.ashbyhq.com/{value}"
        return ""

    def _normalized_company_source(
        *,
        source_id: str,
        owner_id: str,
        company_name: str,
        source_type: JobSourceType,
        source_identifier: str,
        careers_url: str,
        enabled: bool,
        existing: CompanySource | None = None,
    ) -> CompanySource:
        company_name = " ".join(str(company_name or "").split())
        careers_url = str(careers_url or "").strip()
        source_identifier = _source_identifier_value(
            source_type, source_identifier, careers_url
        )
        if source_type is JobSourceType.GENERIC_JSONLD:
            source_identifier = ""
        elif source_type is JobSourceType.SUCCESSFACTORS:
            source_identifier = ""
            careers_url = successfactors_search_url(careers_url)
        elif source_type is JobSourceType.ORACLE_CLOUD_HCM:
            source_identifier = ""
            careers_url = parse_oracle_cloud_hcm_careers_url(
                careers_url
            ).listing_url
        elif source_type is JobSourceType.ICIMS:
            source_identifier = ""
            careers_url = parse_icims_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.SMARTRECRUITERS:
            source_identifier = ""
            careers_url = parse_smartrecruiters_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.AVATURE:
            source_identifier = ""
            careers_url = parse_avature_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.EIGHTFOLD:
            source_identifier = ""
            careers_url = parse_eightfold_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.TALEO:
            source_identifier = ""
            careers_url = parse_taleo_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.DAYFORCE:
            source_identifier = ""
            careers_url = parse_dayforce_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.TALEMETRY_TTC:
            source_identifier = ""
            careers_url = parse_talemetry_ttc_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.JOBVITE:
            source_identifier = ""
            careers_url = parse_jobvite_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.UKG_PRO:
            source_identifier = ""
            careers_url = parse_ukg_pro_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.PEOPLEADMIN:
            source_identifier = ""
            careers_url = parse_peopleadmin_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.RADANCY_TALENTBREW:
            source_identifier = ""
            careers_url = parse_radancy_talentbrew_careers_url(
                careers_url
            ).listing_url
        elif source_type is JobSourceType.AMAZON_JOBS:
            source_identifier = ""
            careers_url = parse_amazon_jobs_careers_url(careers_url).listing_url
        elif source_type is JobSourceType.BRANDED_REQUISITION:
            source_identifier = ""
            careers_url = parse_branded_requisition_careers_url(
                careers_url
            ).listing_url
        elif source_type is JobSourceType.WORKDAY:
            target = parse_workday_careers_url(
                careers_url,
                site_identifier=source_identifier,
            )
            source_identifier = target.site
            careers_url = target.careers_url
        else:
            careers_url = careers_url or _default_source_url(
                source_type, source_identifier
            )
        return CompanySource(
            id=source_id,
            owner_id=owner_id,
            company_name=company_name,
            careers_url=careers_url,
            source_type=source_type,
            source_identifier=source_identifier,
            enabled=enabled,
            last_checked_at=existing.last_checked_at if existing else "",
            filters=(
                dict(existing.filters)
                if existing
                else {
                    "include_compensation": True,
                    "deactivate_after_missed_scans": 3,
                }
            ),
            revision=existing.revision if existing else 0,
        )

    def _company_source_identity(source: CompanySource) -> tuple[str, str]:
        if source.source_type in {
            JobSourceType.GREENHOUSE,
            JobSourceType.LEVER,
            JobSourceType.ASHBY,
        }:
            locator = source.source_identifier.casefold().strip().strip("/")
        else:
            parsed = urlsplit(source.careers_url)
            normalized_path = "/".join(
                part for part in parsed.path.casefold().split("/") if part
            )
            normalized_query = parsed.query.casefold()
            locator = (
                f"{(parsed.hostname or '').casefold()}|{normalized_path}|{normalized_query}"
            )
        return source.source_type.value, locator

    def _interactive_discovery_source(source: CompanySource) -> CompanySource:
        """Apply browser-safe limits without changing the saved source settings.

        Interactive refreshes run one company per HTTP request. These limits keep
        an individual source below the gateway timeout while the external runner
        remains free to perform a complete scheduled scan.
        """

        filters = dict(source.filters)

        def capped_int(name: str, default: int, maximum: int) -> int:
            try:
                value = int(filters.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(0, min(value, maximum))

        def capped_float(name: str, default: float, maximum: float) -> float:
            try:
                value = float(filters.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(0.0, min(value, maximum))

        if source.source_type is JobSourceType.WORKDAY:
            filters.update(
                {
                    "max_jobs": capped_int("max_jobs", 80, 80),
                    "max_pages": max(1, capped_int("max_pages", 4, 4)),
                    "detail_fetch_limit": capped_int("detail_fetch_limit", 10, 10),
                    "fetch_budget_seconds": capped_float(
                        "fetch_budget_seconds", 18.0, 18.0
                    ),
                    "timeout_seconds": max(
                        1.0, capped_float("timeout_seconds", 5.0, 5.0)
                    ),
                    "min_request_interval_seconds": capped_float(
                        "min_request_interval_seconds", 0.2, 0.2
                    ),
                }
            )
            return replace(source, filters=filters)

        if source.source_type is JobSourceType.SUCCESSFACTORS:
            filters.update(
                {
                    "max_jobs": capped_int("max_jobs", 80, 80),
                    "max_pages": max(1, capped_int("max_pages", 4, 4)),
                    "detail_fetch_limit": capped_int("detail_fetch_limit", 10, 10),
                    "fetch_budget_seconds": capped_float(
                        "fetch_budget_seconds", 18.0, 18.0
                    ),
                    "timeout_seconds": max(
                        1.0, capped_float("timeout_seconds", 5.0, 5.0)
                    ),
                    "min_request_interval_seconds": capped_float(
                        "min_request_interval_seconds", 0.2, 0.2
                    ),
                }
            )
            return replace(source, filters=filters)

        if source.source_type is JobSourceType.ORACLE_CLOUD_HCM:
            filters.update(
                {
                    "max_jobs": capped_int("max_jobs", 80, 80),
                    "max_pages": max(1, capped_int("max_pages", 4, 4)),
                    "detail_fetch_limit": capped_int(
                        "detail_fetch_limit", 10, 10
                    ),
                    "fetch_budget_seconds": capped_float(
                        "fetch_budget_seconds", 18.0, 18.0
                    ),
                    "timeout_seconds": max(
                        1.0, capped_float("timeout_seconds", 5.0, 5.0)
                    ),
                    "min_request_interval_seconds": capped_float(
                        "min_request_interval_seconds", 0.2, 0.2
                    ),
                }
            )
            return replace(source, filters=filters)

        if source.source_type in {
            JobSourceType.ICIMS,
            JobSourceType.SMARTRECRUITERS,
            JobSourceType.AVATURE,
            JobSourceType.EIGHTFOLD,
            JobSourceType.TALEO,
            JobSourceType.DAYFORCE,
            JobSourceType.TALEMETRY_TTC,
            JobSourceType.JOBVITE,
            JobSourceType.UKG_PRO,
            JobSourceType.PEOPLEADMIN,
            JobSourceType.RADANCY_TALENTBREW,
            JobSourceType.AMAZON_JOBS,
        }:
            filters.update(
                {
                    "max_jobs": capped_int("max_jobs", 80, 80),
                    "max_pages": max(1, capped_int("max_pages", 4, 4)),
                    "detail_fetch_limit": capped_int(
                        "detail_fetch_limit", 10, 10
                    ),
                    "fetch_budget_seconds": capped_float(
                        "fetch_budget_seconds", 18.0, 18.0
                    ),
                    "timeout_seconds": max(
                        1.0, capped_float("timeout_seconds", 5.0, 5.0)
                    ),
                    "min_request_interval_seconds": capped_float(
                        "min_request_interval_seconds", 0.2, 0.2
                    ),
                }
            )
            return replace(source, filters=filters)

        if source.source_type is JobSourceType.GENERIC_JSONLD:
            filters.update(
                {
                    "max_pages": max(1, capped_int("max_pages", 3, 3)),
                    "timeout_seconds": max(
                        1.0, capped_float("timeout_seconds", 4.0, 4.0)
                    ),
                    "min_request_interval_seconds": capped_float(
                        "min_request_interval_seconds", 0.15, 0.15
                    ),
                }
            )
            return replace(source, filters=filters)

        if source.source_type is JobSourceType.BRANDED_REQUISITION:
            filters.update(
                {
                    "max_jobs": capped_int("max_jobs", 80, 80),
                    "max_pages": max(1, capped_int("max_pages", 2, 2)),
                    "detail_fetch_limit": capped_int(
                        "detail_fetch_limit", 5, 5
                    ),
                    "fetch_budget_seconds": capped_float(
                        "fetch_budget_seconds", 22.0, 22.0
                    ),
                    "timeout_seconds": max(
                        1.0, capped_float("timeout_seconds", 15.0, 15.0)
                    ),
                    "min_request_interval_seconds": capped_float(
                        "min_request_interval_seconds", 0.5, 0.5
                    ),
                    "retry_attempts": 1,
                    "retry_backoff_seconds": 0.0,
                }
            )
            return replace(source, filters=filters)

        # Greenhouse, Lever, and Ashby each expose the complete board in one
        # bounded public API request, so they can retain their saved settings and
        # still be marked as complete shared-catalog scans.
        return source

    def _discovery_search_preferences(
        owner_id: str, current: WorkflowState
    ) -> DiscoverySearchPreferences:
        stored = discovery_store.get_search_preferences(owner_id)
        if stored is not None:
            return stored

        source_profile = current.confirmed_profile or current.source_profile
        reusable = _load_reusable_career_profile(owner_id)
        target_titles = tuple(
            dict.fromkeys(
                value
                for value in (
                    current.target_title,
                    *reusable.target_titles,
                )
                if value
            )
        )
        locations = tuple(
            dict.fromkeys(
                value
                for value in (
                    *reusable.preferred_locations,
                    source_profile.contact.location,
                )
                if value
            )
        )
        return DiscoverySearchPreferences(
            owner_id=owner_id,
            target_titles=target_titles,
            preferred_locations=locations,
            accepted_workplace_types=reusable.accepted_workplace_types,
            preferred_keywords=tuple(
                dict.fromkeys((*reusable.industry_values, *reusable.skill_values))
            ),
        )

    def _discovery_candidate_profile(
        current: WorkflowState, *, owner_id: str | None = None
    ) -> CandidateJobProfile:
        """Build traceable evidence plus owner-managed search preferences."""

        resolved_owner = owner_id or _application_owner_id()
        preferences = _discovery_search_preferences(resolved_owner, current)
        source_profile = current.confirmed_profile or current.source_profile
        base = CandidateJobProfile.from_resume_workflow(
            source_profile,
            _effective_career_background(current),
            target_title=current.target_title,
        )
        accepted_workplaces = tuple(preferences.accepted_workplace_types)
        reusable = _load_reusable_career_profile(resolved_owner)
        return replace(
            base,
            target_titles=preferences.target_titles or base.target_titles,
            preferred_locations=(
                preferences.preferred_locations or base.preferred_locations
            ),
            accepts_remote=(
                not accepted_workplaces
                or WorkplaceType.REMOTE in accepted_workplaces
            ),
            preferred_employment_types=preferences.preferred_employment_types,
            preferred_keywords=preferences.preferred_keywords,
            required_keywords=preferences.required_keywords,
            accepted_workplace_types=accepted_workplaces,
            minimum_salary=preferences.minimum_salary,
            minimum_salary_currency=preferences.minimum_salary_currency,
            minimum_salary_interval=preferences.minimum_salary_interval,
            excluded_terms=preferences.excluded_terms,
            excluded_title_terms=preferences.excluded_title_terms,
            require_title_match=preferences.require_title_match,
            require_location_match=preferences.require_location_match,
            require_workplace_match=preferences.require_workplace_match,
            require_employment_type_match=(
                preferences.require_employment_type_match
            ),
            requires_sponsorship=reusable.requires_sponsorship,
            work_authorized=reusable.work_authorized,
            eligibility_profile_complete=bool(reusable.work_authorization),
        )

    def _discovery_checked_label(raw: str) -> str:
        value = str(raw or "").strip()
        if not value:
            return "Not refreshed yet"
        try:
            checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if checked.tzinfo is None or checked.utcoffset() is None:
                return "Last refresh time unavailable"
            checked = checked.astimezone(timezone.utc)
        except ValueError:
            return "Last refresh time unavailable"
        return "Last refreshed " + checked.strftime("%b %d, %Y at %H:%M UTC")

    def _discovery_scan_time_label(raw: str, *, prefix: str) -> str:
        value = str(raw or "").strip()
        if not value:
            return ""
        try:
            checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if checked.tzinfo is None or checked.utcoffset() is None:
                return f"{prefix} time unavailable"
            checked = checked.astimezone(timezone.utc)
        except ValueError:
            return f"{prefix} time unavailable"
        return f"{prefix} " + checked.strftime("%b %d, %Y at %H:%M UTC")

    def _discovery_source_scan_status(
        source: CompanySource, statuses_by_key: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the manager-facing result of the latest shared catalog scan.

        Public catalog statuses are persisted independently of each user's
        materialized jobs, so a failed refresh remains visible even when older
        cached postings are still available.
        """

        try:
            status = statuses_by_key.get(public_source_key(source))
        except (TypeError, ValueError) as exc:
            return {
                "state": "issue",
                "label": "Configuration issue",
                "attempt_label": "The source configuration could not be evaluated.",
                "success_label": "",
                "message": str(exc),
                "job_count_label": "",
            }

        if status is None:
            if source.last_checked_at:
                return {
                    "state": "legacy",
                    "label": "Previously refreshed",
                    "attempt_label": _discovery_scan_time_label(
                        source.last_checked_at, prefix="Last refreshed"
                    ),
                    "success_label": "",
                    "message": (
                        "Detailed scan results were not recorded for this earlier refresh. "
                        "Run Refresh jobs for everyone to create a current result."
                    ),
                    "job_count_label": "",
                }
            return {
                "state": "not_scanned",
                "label": "Not scanned",
                "attempt_label": "No scan has been attempted yet.",
                "success_label": "",
                "message": "",
                "job_count_label": "",
            }

        attempt_label = _discovery_scan_time_label(
            status.last_attempt_at, prefix="Last scan"
        ) or "No scan has been attempted yet."
        success_label = ""
        if status.last_error and status.last_success_at:
            success_label = _discovery_scan_time_label(
                status.last_success_at, prefix="Last successful scan"
            )

        if status.last_error:
            normalized_error = str(status.last_error).casefold()
            if "robots.txt disallows" in normalized_error:
                indexed_timeout = (
                    "indexed fallback was unavailable" in normalized_error
                    and any(
                        token in normalized_error
                        for token in (
                            "timeout",
                            "timed out",
                            "502",
                            "503",
                            "504",
                            "temporarily unavailable",
                            "connection",
                        )
                    )
                )
                if indexed_timeout:
                    state = "issue"
                    label = "Retry recommended"
                    message = (
                        "The employer blocks direct listing scans, and the compliant "
                        "search-index fallback temporarily timed out. This is not a "
                        "reason to disable or remove the source. Retry the source scan; "
                        "previously collected jobs remain available. An authorized feed "
                        "or crawler allowlisting is still the best option for a complete "
                        "scan. "
                        f"Technical detail: {status.last_error}"
                    )
                else:
                    state = "permission_required"
                    label = "Permission required"
                    message = (
                        "The employer's robots policy blocks automated discovery for this "
                        "public search path. Career Bridge will not bypass that policy. "
                        "Previously collected jobs remain available while an authorized "
                        "feed, sitemap, allow-rule, or crawler allowlisting is requested. "
                        f"Technical detail: {status.last_error}"
                    )
            else:
                state = "issue"
                label = "Issue"
                message = status.last_error
        elif status.last_attempt_at and status.complete_scan:
            state = "success"
            label = "Successful"
            message = "The source was scanned successfully."
        elif status.last_attempt_at:
            state = "limited"
            label = "Successful · limited"
            message = (
                "The interactive scan completed within browser-safe limits. "
                "The external scheduled runner can perform a complete scan."
            )
        else:
            state = "not_scanned"
            label = "Not scanned"
            message = ""

        job_count_label = ""
        if status.last_success_at:
            noun = "posting" if status.job_count == 1 else "postings"
            job_count_label = (
                f"{status.job_count} active public {noun} stored from the latest "
                "successful scan."
            )

        return {
            "state": state,
            "label": label,
            "attempt_label": attempt_label,
            "success_label": success_label,
            "message": message,
            "job_count_label": job_count_label,
        }

    def _discovery_posted_label(job: Any) -> str:
        raw = str(job.posted_at or job.first_seen_at or "").strip()
        if not raw:
            return "Posting date not available"
        try:
            posted = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if posted.tzinfo is None or posted.utcoffset() is None:
                return "Posting date not available"
            age_days = max(0, (datetime.now(timezone.utc) - posted.astimezone(timezone.utc)).days)
        except ValueError:
            return "Posting date not available"
        if age_days == 0:
            return "Posted today"
        if age_days == 1:
            return "Posted 1 day ago"
        return f"Posted {age_days} days ago"


    def _discovery_scan_schedule(owner_id: str) -> DiscoveryScanSchedule:
        stored = discovery_store.get_scan_schedule(owner_id)
        if stored is not None:
            return stored
        return DiscoveryScanSchedule(
            owner_id=owner_id,
            cadence=DiscoveryScheduleCadence.MANUAL,
            timezone_name=str(
                current_app.config.get("CAREER_BRIDGE_DEFAULT_TIMEZONE") or "UTC"
            ),
        )

    def _discovery_schedule_time_label(value: datetime | None) -> str:
        if value is None:
            return "Manual refresh only"
        return value.astimezone(timezone.utc).strftime("%b %d, %Y at %H:%M UTC")

    _DISCOVERY_RESULT_INDEX_VERSION = "6"
    _DISCOVERY_RESULT_TABS = (
        "recommended",
        "possible",
        "pending",
        "low_match",
        "saved",
        "ignored",
    )
    _DISCOVERY_PAGE_SIZES = (10, 20, 50)
    _DISCOVERY_DEFAULT_PAGE_SIZE = 20
    _DISCOVERY_MINIMUM_FIT_OPTIONS = (0, 50, 60, 70, 80)
    _DISCOVERY_ASSESSMENT_BATCH_DEFAULT = 1
    _DISCOVERY_ASSESSMENT_BATCH_MAX = 1
    _DISCOVERY_ASSESSMENT_RUN_DEFAULT = 25
    _DISCOVERY_ASSESSMENT_RUN_MAX = 100

    def _discovery_result_tab(raw: Any) -> str:
        value = str(raw or "recommended").strip().casefold()
        return value if value in _DISCOVERY_RESULT_TABS else "recommended"

    def _discovery_positive_int(raw: Any, *, default: int) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    def _discovery_page_size(raw: Any) -> int:
        value = _discovery_positive_int(
            raw, default=_DISCOVERY_DEFAULT_PAGE_SIZE
        )
        return (
            value
            if value in _DISCOVERY_PAGE_SIZES
            else _DISCOVERY_DEFAULT_PAGE_SIZE
        )

    def _discovery_assessment_batch_size(raw: Any) -> int:
        configured = current_app.config.get(
            "CAREER_BRIDGE_DISCOVERY_ASSESSMENT_BATCH_SIZE",
            _DISCOVERY_ASSESSMENT_BATCH_DEFAULT,
        )
        try:
            default = int(configured)
        except (TypeError, ValueError):
            default = _DISCOVERY_ASSESSMENT_BATCH_DEFAULT
        default = min(_DISCOVERY_ASSESSMENT_BATCH_MAX, max(1, default))
        try:
            requested = int(raw)
        except (TypeError, ValueError):
            return default
        return min(_DISCOVERY_ASSESSMENT_BATCH_MAX, max(1, requested))

    def _discovery_assessment_run_limit() -> int:
        configured = current_app.config.get(
            "CAREER_BRIDGE_DISCOVERY_ASSESSMENT_RUN_LIMIT",
            _DISCOVERY_ASSESSMENT_RUN_DEFAULT,
        )
        try:
            value = int(configured)
        except (TypeError, ValueError):
            value = _DISCOVERY_ASSESSMENT_RUN_DEFAULT
        return min(_DISCOVERY_ASSESSMENT_RUN_MAX, max(1, value))

    def _discovery_result_filters(values: Any | None = None) -> DiscoveryResultFilters:
        source = values if values is not None else request.values
        raw_minimum_fit = source.get("min_fit", DEFAULT_MINIMUM_FIT)
        try:
            minimum_fit = int(raw_minimum_fit)
        except (TypeError, ValueError):
            minimum_fit = DEFAULT_MINIMUM_FIT
        minimum_fit = min(100, max(0, minimum_fit))
        return DiscoveryResultFilters(
            minimum_fit=minimum_fit,
            confidence_tiers=parse_confidence_query(
                source.get("confidence", ",".join(DEFAULT_CONFIDENCE_TIERS))
            ),
            recommendation_filter=str(
                source.get("recommendation", DEFAULT_RECOMMENDATION_FILTER)
            ),
            sort_mode=str(source.get("sort", DEFAULT_SORT_MODE)),
            # Public Job Discovery now always shows the worldwide catalog.
            # Country and U.S.-state result filters are intentionally ignored.
            country_code="",
            us_state_code="",
        )

    def _discovery_results_url(
        *,
        result_tab: str | None = None,
        page: int | None = None,
        per_page: int | None = None,
        anchor: str = "job-discovery-results",
    ) -> str:
        selected_tab = _discovery_result_tab(
            result_tab if result_tab is not None else request.values.get("result_tab")
        )
        selected_page = _discovery_positive_int(
            page if page is not None else request.values.get("page"),
            default=1,
        )
        selected_size = _discovery_page_size(
            per_page if per_page is not None else request.values.get("per_page")
        )
        filters = _discovery_result_filters(request.values)
        return (
            url_for(
                "application_builder.job_discovery_workspace",
                result_tab=selected_tab,
                page=selected_page,
                per_page=selected_size,
                min_fit=filters.minimum_fit,
                confidence=filters.confidence_query,
                recommendation=filters.recommendation_filter,
                sort=filters.sort_mode,
            )
            + (f"#{anchor}" if anchor else "")
        )

    def _discovery_card_analysis(
        job: Any,
        profile: CandidateJobProfile,
        fit: Any | None = None,
    ) -> dict[str, Any]:
        resolved_fit = fit or discovery_store.get_fit_snapshot(
            job.owner_id,
            job.id,
            profile.fingerprint,
            job.description_fingerprint,
        ) or discovery_store.get_fit_snapshot(
            job.owner_id,
            job.id,
            profile.fingerprint,
        )
        stage_one = evaluate_stage_one(job, profile)
        ranked = (
            ranked_from_snapshot(job, resolved_fit, stage_one=stage_one)
            if resolved_fit is not None and stage_one.passed
            else None
        )
        traceable_strengths = tuple(
            item
            for item in (
                resolved_fit.evidence_matches if resolved_fit is not None else ()
            )
            if item.status == "supported" and item.evidence
        )
        traceable_partial = tuple(
            item
            for item in (
                resolved_fit.evidence_matches if resolved_fit is not None else ()
            )
            if item.status == "partial" and item.evidence
        )
        return {
            "job": job,
            "fit": resolved_fit,
            "stage_one": stage_one,
            "search_priority": ranked.search_priority if ranked else None,
            "preference_score": stage_one.preference_score,
            "freshness_score": stage_one.freshness_score,
            "preference_components": stage_one.preference_components,
            "strongest_matches": traceable_strengths[:3],
            "partial_matches": traceable_partial[:3],
            "important_gaps": (
                resolved_fit.unsupported_requirements[:5] if resolved_fit else ()
            ),
        }

    def _discovery_result_index_preference_fingerprint(
        profile: CandidateJobProfile,
        maximum_posting_age_days: int | None,
        filters: DiscoveryResultFilters,
        allowed_source_ids: tuple[str, ...],
    ) -> str:
        age_value = "any" if maximum_posting_age_days is None else str(maximum_posting_age_days)
        material = "|".join(
            (
                profile.preference_fingerprint,
                f"result_index_version={_DISCOVERY_RESULT_INDEX_VERSION}",
                f"maximum_posting_age_days={age_value}",
                f"minimum_fit={filters.minimum_fit}",
                f"confidence={filters.confidence_query}",
                f"recommendation={filters.recommendation_filter}",
                f"sort={filters.sort_mode}",
                "allowed_sources=" + ",".join(sorted(allowed_source_ids)),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _compact_discovery_job(job: DiscoveredJob) -> DiscoveredJob:
        """Remove detail-heavy fields that are not needed by the result card."""

        return replace(
            job,
            description="",
            skills=(),
            metadata={},
        )

    def _compact_discovery_fit(fit: Any | None) -> Any | None:
        if fit is None:
            return None
        return replace(
            fit,
            supported_requirements=(),
            partial_requirements=(),
            unsupported_requirements=(),
            hard_blockers=(),
            evidence_matches=(),
        )

    def _discovery_index_card(
        record: DiscoveryResultRecord,
    ) -> dict[str, Any]:
        application = (
            application_store.get(
                record.owner_id,
                record.application_id,
                include_resume_bytes=False,
            )
            if record.application_id
            else None
        )
        disposition = record.disposition
        if (
            disposition is DiscoveryJobDisposition.APPLICATION_CREATED
            and application is None
        ):
            # A cached result index can outlive an application that was
            # deleted before the discovery state was repaired. Keep the page
            # usable and let the user create a replacement workspace.
            disposition = DiscoveryJobDisposition.SAVED
        return {
            "job": record.job,
            "fit": record.fit,
            "state": None,
            "disposition": disposition,
            "application": application,
            "stage_one": None,
            "search_priority": record.search_priority,
            "preference_score": record.preference_score,
            "freshness_score": record.freshness_score,
            "posted_label": record.posted_label,
            "result_group": record.result_group,
        }

    def _discovery_pagination(
        total: int,
        *,
        page: int,
        per_page: int,
    ) -> dict[str, Any]:
        total = max(0, int(total))
        total_pages = max(1, (total + per_page - 1) // per_page)
        selected_page = min(_discovery_positive_int(page, default=1), total_pages)
        start = (selected_page - 1) * per_page
        end = min(total, start + per_page)
        return {
            "page": selected_page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "start": start + 1 if total else 0,
            "end": end,
            "offset": start,
            "has_previous": selected_page > 1,
            "has_next": selected_page < total_pages,
            "previous_page": max(1, selected_page - 1),
            "next_page": min(total_pages, selected_page + 1),
        }

    def _discovery_paginate(
        records: list[Any],
        *,
        page: int,
        per_page: int,
    ) -> tuple[list[Any], dict[str, Any]]:
        pagination = _discovery_pagination(
            len(records), page=page, per_page=per_page
        )
        start = pagination["offset"]
        return records[start : start + per_page], pagination

    def _discovery_result_cards(
        owner_id: str,
        profile: CandidateJobProfile,
        *,
        result_tab: str = "recommended",
        page: int = 1,
        per_page: int = _DISCOVERY_DEFAULT_PAGE_SIZE,
        maximum_posting_age_days: int | None = DEFAULT_MAX_POSTING_AGE_DAYS,
        filters: DiscoveryResultFilters | None = None,
        allowed_source_ids: tuple[str, ...] = (),
        rebuild_if_needed: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        """Return one page from the compact materialized discovery result index.

        Normal page requests never rebuild this read model. They return the
        current index, or the last materialized index while a separate bounded
        request refreshes it. Mutation and explicit prebuild requests opt in to
        the expensive full rebuild with ``rebuild_if_needed=True``.
        """

        selected_tab = _discovery_result_tab(result_tab)
        selected_size = _discovery_page_size(per_page)
        selected_filters = filters or DiscoveryResultFilters()
        evidence_fingerprint = profile.fingerprint
        preference_fingerprint = _discovery_result_index_preference_fingerprint(
            profile, maximum_posting_age_days, selected_filters, allowed_source_ids
        )
        revision_token = discovery_store.get_result_revision(owner_id)
        cached_summary = discovery_store.get_result_index_summary(
            owner_id,
            evidence_fingerprint,
            preference_fingerprint,
        )
        if cached_summary is not None and (
            cached_summary.revision_token == revision_token
            or not rebuild_if_needed
        ):
            index_stale = cached_summary.revision_token != revision_token
            selected_total = int(getattr(cached_summary, f"{selected_tab}_count"))
            pagination = _discovery_pagination(
                selected_total, page=page, per_page=selected_size
            )
            page_records = discovery_store.list_result_records_page(
                owner_id,
                evidence_fingerprint,
                preference_fingerprint,
                selected_tab,
                offset=pagination["offset"],
                limit=selected_size,
            )
            page_cards = [_discovery_index_card(item) for item in page_records]
            summary = {
                "recommended_count": cached_summary.recommended_count,
                "possible_count": cached_summary.possible_count,
                "pending_count": cached_summary.pending_count,
                "low_match_count": cached_summary.low_match_count,
                "saved_count": cached_summary.saved_count,
                "ignored_count": cached_summary.ignored_count,
                "filtered_count": cached_summary.filtered_count,
                "quality_filtered_count": cached_summary.quality_filtered_count,
                "age_filtered_count": cached_summary.age_filtered_count,
                "shown_count": len(page_cards),
                "ranked_count": (
                    cached_summary.recommended_count
                    + cached_summary.possible_count
                    + cached_summary.low_match_count
                ),
                "pinned_count": cached_summary.saved_count,
                "top_count": len(page_cards),
                "index_stale": index_stale,
            }
            return page_cards, summary, pagination

        if not rebuild_if_needed:
            pagination = _discovery_pagination(0, page=page, per_page=selected_size)
            return [], {
                "recommended_count": 0,
                "possible_count": 0,
                "pending_count": 0,
                "low_match_count": 0,
                "saved_count": 0,
                "ignored_count": 0,
                "filtered_count": 0,
                "quality_filtered_count": 0,
                "age_filtered_count": 0,
                "shown_count": 0,
                "ranked_count": 0,
                "pinned_count": 0,
                "top_count": 0,
                "index_stale": True,
            }, pagination

        applications = application_store.list_for_owner(owner_id)
        applications_by_id = {item.id: item for item in applications}
        applications_by_source_job = {
            item.source_job_id: item
            for item in applications
            if item.source_job_id
        }
        jobs = discovery_store.list_discovered_jobs(owner_id, active_only=True)
        allowed_source_id_set = set(allowed_source_ids)
        states = {
            (item.source_id, item.job_id): item
            for item in discovery_store.list_job_states(owner_id)
        }
        fits_by_key: dict[tuple[str, str, str], Any] = {}
        for snapshot in discovery_store.list_fit_snapshots(owner_id):
            key = (
                snapshot.job_id,
                snapshot.profile_fingerprint,
                snapshot.description_fingerprint,
            )
            current = fits_by_key.get(key)
            if current is None or snapshot.analyzed_at > current.analyzed_at:
                fits_by_key[key] = snapshot

        groups: dict[str, list[dict[str, Any]]] = {
            name: [] for name in _DISCOVERY_RESULT_TABS
        }
        filtered_count = 0
        quality_filtered_count = 0
        age_filtered_count = 0

        for job in jobs:
            if not job_matches_location_filters(
                job,
                country_code=selected_filters.country_code,
                us_state_code=selected_filters.us_state_code,
            ):
                continue
            fit = fits_by_key.get(
                (job.id, profile.fingerprint, job.description_fingerprint)
            ) or fits_by_key.get((job.id, profile.fingerprint, ""))
            job_state = states.get((job.source_id, job.id))
            application = applications_by_source_job.get(job.id)
            if (
                application is None
                and job_state is not None
                and job_state.disposition
                is DiscoveryJobDisposition.APPLICATION_CREATED
            ):
                # DynamoDB Query is eventually consistent by default. The
                # application-created state may therefore be visible before
                # list_for_owner() includes the new application. Resolve the
                # recorded application ID directly (a strongly consistent
                # read in the production application store) before deciding
                # that the link is stale.
                application = applications_by_id.get(job_state.application_id)
                if application is None:
                    application = application_store.get(
                        owner_id,
                        job_state.application_id,
                        include_resume_bytes=False,
                    )
                if application is not None:
                    applications_by_id[application.id] = application
                    if application.source_job_id:
                        applications_by_source_job[application.source_job_id] = (
                            application
                        )

            stale_application_link = (
                application is None
                and job_state is not None
                and job_state.disposition
                is DiscoveryJobDisposition.APPLICATION_CREATED
            )
            if stale_application_link:
                current_app.logger.warning(
                    "Ignoring stale Job Discovery application link owner=%s "
                    "source=%s job=%s application=%s",
                    owner_id,
                    job.source_id,
                    job.id,
                    job_state.application_id,
                )
            disposition = (
                DiscoveryJobDisposition.APPLICATION_CREATED
                if application is not None
                else DiscoveryJobDisposition.SAVED
                if stale_application_link
                else job_state.disposition
                if job_state is not None
                else None
            )
            if (
                job.source_id not in allowed_source_id_set
                and disposition
                not in {
                    DiscoveryJobDisposition.SAVED,
                    DiscoveryJobDisposition.APPLICATION_CREATED,
                    DiscoveryJobDisposition.IGNORED,
                }
            ):
                continue
            age_decision = evaluate_posting_age(
                job,
                maximum_age_days=maximum_posting_age_days,
            )
            if (
                not age_decision.eligible
                and disposition
                not in {
                    DiscoveryJobDisposition.SAVED,
                    DiscoveryJobDisposition.APPLICATION_CREATED,
                    DiscoveryJobDisposition.IGNORED,
                }
            ):
                age_filtered_count += 1
                continue

            stage_one = evaluate_stage_one(job, profile)
            ranked = (
                ranked_from_snapshot(job, fit, stage_one=stage_one)
                if fit is not None and stage_one.passed
                else None
            )
            card = {
                "job": job,
                "fit": fit,
                "state": job_state,
                "disposition": disposition,
                "application": application,
                "stage_one": stage_one,
                "search_priority": ranked.search_priority if ranked else None,
                "preference_score": stage_one.preference_score,
                "freshness_score": stage_one.freshness_score,
                "posted_label": _discovery_posted_label(job),
                "recommendation_tier": (
                    recommendation_tier(fit.recommendation) if fit is not None else "unassessed"
                ),
                "confidence_tier": (
                    confidence_tier(fit.confidence) if fit is not None else "unassessed"
                ),
            }

            if disposition is DiscoveryJobDisposition.IGNORED:
                card["result_group"] = "ignored"
                groups["ignored"].append(card)
                continue
            if disposition in {
                DiscoveryJobDisposition.SAVED,
                DiscoveryJobDisposition.APPLICATION_CREATED,
            }:
                card["result_group"] = "saved"
                groups["saved"].append(card)
                continue
            if not stage_one.passed:
                filtered_count += 1
                continue
            if fit is None:
                card["result_group"] = "pending"
                groups["pending"].append(card)
                continue

            result_group = assessed_visibility_group(
                fit_score=fit.fit_score,
                recommendation=fit.recommendation,
                confidence=fit.confidence,
                filters=selected_filters,
            )
            if result_group is None:
                quality_filtered_count += 1
                continue
            card["result_group"] = result_group
            groups[result_group].append(card)

        def assessed_card_key(item: dict[str, Any]) -> tuple[object, ...]:
            fit = item.get("fit")
            if fit is None:
                return (0, 0, 0, 0, 0, 0, "")
            return assessed_sort_key(
                fit_score=fit.fit_score,
                recommendation=fit.recommendation,
                confidence=fit.confidence,
                preference_score=item["preference_score"],
                freshness_score=item["freshness_score"],
                posted_at=item["job"].posted_at or item["job"].first_seen_at,
                title=item["job"].title,
                sort_mode=selected_filters.sort_mode,
            )

        for group_name in ("recommended", "possible", "low_match"):
            groups[group_name].sort(key=assessed_card_key, reverse=True)
        groups["pending"].sort(
            key=lambda item: (
                item["preference_score"],
                item["freshness_score"],
                item["job"].posted_at,
                item["job"].title.casefold(),
            ),
            reverse=True,
        )
        groups["saved"].sort(
            key=lambda item: (
                1 if item["application"] is not None else 0,
                *assessed_card_key(item),
            ),
            reverse=True,
        )
        groups["ignored"].sort(
            key=lambda item: (
                item["job"].company.casefold(),
                item["job"].title.casefold(),
            )
        )

        result_records: list[DiscoveryResultRecord] = []
        for group_name, cards in groups.items():
            for ordinal, card in enumerate(cards):
                application = card["application"]
                result_records.append(
                    DiscoveryResultRecord(
                        owner_id=owner_id,
                        evidence_fingerprint=evidence_fingerprint,
                        preference_fingerprint=preference_fingerprint,
                        result_group=group_name,
                        job=_compact_discovery_job(card["job"]),
                        recommendation_tier=card["recommendation_tier"],
                        confidence_tier=card["confidence_tier"],
                        visibility_category=group_name,
                        disposition=card["disposition"],
                        application_id=(
                            application.id if application is not None else ""
                        ),
                        fit=_compact_discovery_fit(card["fit"]),
                        preference_score=card["preference_score"],
                        freshness_score=card["freshness_score"],
                        search_priority=card["search_priority"],
                        posted_label=card["posted_label"],
                        sort_rank=f"{ordinal:08d}",
                    )
                )

        index_summary = DiscoveryResultIndexSummary(
            owner_id=owner_id,
            evidence_fingerprint=evidence_fingerprint,
            preference_fingerprint=preference_fingerprint,
            revision_token=revision_token,
            recommended_count=len(groups["recommended"]),
            possible_count=len(groups["possible"]),
            pending_count=len(groups["pending"]),
            low_match_count=len(groups["low_match"]),
            saved_count=len(groups["saved"]),
            ignored_count=len(groups["ignored"]),
            filtered_count=filtered_count,
            quality_filtered_count=quality_filtered_count,
            age_filtered_count=age_filtered_count,
        )
        discovery_store.replace_result_index(index_summary, result_records)

        page_cards, pagination = _discovery_paginate(
            groups[selected_tab], page=page, per_page=selected_size
        )
        summary = {
            "recommended_count": index_summary.recommended_count,
            "possible_count": index_summary.possible_count,
            "pending_count": index_summary.pending_count,
            "low_match_count": index_summary.low_match_count,
            "saved_count": index_summary.saved_count,
            "ignored_count": index_summary.ignored_count,
            "filtered_count": index_summary.filtered_count,
            "quality_filtered_count": index_summary.quality_filtered_count,
            "age_filtered_count": index_summary.age_filtered_count,
            "shown_count": len(page_cards),
            "ranked_count": (
                index_summary.recommended_count
                + index_summary.possible_count
                + index_summary.low_match_count
            ),
            "pinned_count": index_summary.saved_count,
            "top_count": len(page_cards),
            "index_stale": False,
        }
        return page_cards, summary, pagination

    def _prebuild_discovery_result_index(
        owner_id: str,
        *,
        current: WorkflowState | None = None,
        filters: DiscoveryResultFilters | None = None,
    ) -> dict[str, Any]:
        """Build the common owner-scoped result read model outside page GETs."""

        workflow_state = current or state()
        selected_filters = filters or DiscoveryResultFilters()
        preferences = _discovery_search_preferences(owner_id, workflow_state)
        enabled_sources = discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID, enabled_only=True
        )
        profile = _discovery_candidate_profile(workflow_state, owner_id=owner_id)
        build_kwargs = {
            "result_tab": "recommended",
            "page": 1,
            "per_page": _DISCOVERY_DEFAULT_PAGE_SIZE,
            "maximum_posting_age_days": preferences.maximum_posting_age_days,
            "filters": selected_filters,
            "allowed_source_ids": tuple(source.id for source in enabled_sources),
        }
        _discovery_result_cards(
            owner_id,
            profile,
            **build_kwargs,
            rebuild_if_needed=True,
        )
        # A concurrent discovery mutation can advance the revision while the
        # index is being assembled. Verify that the materialized summary was
        # committed and retry once against the new revision when necessary.
        _, summary, _ = _discovery_result_cards(
            owner_id,
            profile,
            **build_kwargs,
        )
        if summary["index_stale"]:
            _discovery_result_cards(
                owner_id,
                profile,
                **build_kwargs,
                rebuild_if_needed=True,
            )
            _, summary, _ = _discovery_result_cards(
                owner_id,
                profile,
                **build_kwargs,
            )
        return summary

    def _try_prebuild_discovery_result_index(
        owner_id: str,
        *,
        current: WorkflowState | None = None,
        filters: DiscoveryResultFilters | None = None,
    ) -> bool:
        """Best-effort prebuild for mutation paths that must remain successful."""

        try:
            summary = _prebuild_discovery_result_index(
                owner_id,
                current=current,
                filters=filters,
            )
        except Exception:
            current_app.logger.exception(
                "Job Discovery result-index prebuild failed owner=%s", owner_id
            )
            return False
        return not bool(summary.get("index_stale"))

    def _job_action_service() -> DiscoveredJobApplicationService:
        return DiscoveredJobApplicationService(
            discovery_store,
            application_store,
            description_fetcher=posting_description_fetcher,
        )

    @application_builder_bp.get("/job-discovery")
    def job_discovery_workspace():
        current = state()
        owner_id = g.application_owner_id
        discovery_view = (
            "settings" if request.args.get("view") == "settings" else "results"
        )
        discovery_owner_scope = hashlib.sha256(
            owner_id.encode("utf-8")
        ).hexdigest()[:16]
        g.job_discovery_timing_view = discovery_view
        g.job_discovery_timing_owner_scope = discovery_owner_scope
        g.job_discovery_timing_index_state = "not_applicable"

        sources_started_at = perf_counter()
        can_manage_catalog = _current_user_can_manage_job_catalog()
        discovery_sources = discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID
        )
        enabled_discovery_sources = tuple(
            source for source in discovery_sources if source.enabled
        )
        latest_discovery_check = max(
            (
                source.last_checked_at
                for source in discovery_sources
                if source.last_checked_at
            ),
            default="",
        )
        discovery_catalog_version = hashlib.sha256(
            json.dumps(
                [
                    {
                        "id": source.id,
                        "enabled": source.enabled,
                        "revision": source.revision,
                        "last_checked_at": source.last_checked_at,
                    }
                    for source in discovery_sources
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        _record_job_discovery_phase(
            "jd_sources", sources_started_at, "Company source load"
        )

        preferences_started_at = perf_counter()
        discovery_preferences = _discovery_search_preferences(owner_id, current)
        _record_job_discovery_phase(
            "jd_preferences", preferences_started_at, "Search preferences"
        )

        discovery_source_scan_statuses: dict[str, dict[str, Any]] = {}
        if can_manage_catalog and discovery_view == "settings":
            catalog_status_started_at = perf_counter()
            catalog_statuses_by_key = {
                status.source_key: status
                for status in discovery_store.list_public_catalog_statuses()
            }
            discovery_source_scan_statuses = {
                source.id: _discovery_source_scan_status(
                    source, catalog_statuses_by_key
                )
                for source in discovery_sources
            }
            _record_job_discovery_phase(
                "jd_catalog_status",
                catalog_status_started_at,
                "Catalog scan status",
            )

        template_context: dict[str, Any] = {
            "active_tab": "discovery",
            "discovery_view": discovery_view,
            "can_manage_job_catalog": can_manage_catalog,
            "discovery_source_count": len(discovery_sources),
            "enabled_discovery_source_count": len(enabled_discovery_sources),
            "discovery_refresh_sources": tuple(
                {"id": source.id, "company_name": source.company_name}
                for source in enabled_discovery_sources
            ),
            "discovery_assessment_run_limit": _discovery_assessment_run_limit(),
            "discovery_checked_label": _discovery_checked_label(
                latest_discovery_check
            ),
            "discovery_catalog_version": discovery_catalog_version,
            "discovery_owner_scope": discovery_owner_scope,
            "discovery_sources": discovery_sources,
            "discovery_source_scan_statuses": discovery_source_scan_statuses,
            "discovery_source_types": (
                (JobSourceType.GREENHOUSE.value, "Greenhouse"),
                (JobSourceType.LEVER.value, "Lever"),
                (JobSourceType.ASHBY.value, "Ashby"),
                (JobSourceType.WORKDAY.value, "Workday"),
                (JobSourceType.SUCCESSFACTORS.value, "SAP SuccessFactors"),
                (JobSourceType.ORACLE_CLOUD_HCM.value, "Oracle Cloud HCM"),
                (JobSourceType.ICIMS.value, "iCIMS"),
                (JobSourceType.SMARTRECRUITERS.value, "SmartRecruiters"),
                (JobSourceType.AVATURE.value, "Avature"),
                (JobSourceType.EIGHTFOLD.value, "Eightfold"),
                (JobSourceType.TALEO.value, "Taleo"),
                (JobSourceType.DAYFORCE.value, "Dayforce"),
                (JobSourceType.TALEMETRY_TTC.value, "Talemetry / TTC Portals"),
                (JobSourceType.JOBVITE.value, "Jobvite"),
                (JobSourceType.UKG_PRO.value, "UKG Pro / UltiPro"),
                (JobSourceType.PEOPLEADMIN.value, "PeopleAdmin"),
                (
                    JobSourceType.RADANCY_TALENTBREW.value,
                    "Radancy / TalentBrew",
                ),
                (JobSourceType.AMAZON_JOBS.value, "Amazon Jobs"),
                (
                    JobSourceType.BRANDED_REQUISITION.value,
                    "Branded Requisition Portal",
                ),
                (
                    JobSourceType.GENERIC_JSONLD.value,
                    "Manual career-page URL (JSON-LD)",
                ),
            ),
            "discovery_preferences": discovery_preferences,
            "discovery_workplace_types": (
                (WorkplaceType.REMOTE.value, "Remote"),
                (WorkplaceType.HYBRID.value, "Hybrid"),
                (WorkplaceType.ONSITE.value, "Onsite"),
            ),
            "discovery_accepted_workplace_values": tuple(
                item.value
                for item in discovery_preferences.accepted_workplace_types
            ),
        }

        if discovery_view == "settings":
            schedule_started_at = perf_counter()
            discovery_schedule = _discovery_scan_schedule(
                SHARED_CATALOG_SOURCE_OWNER_ID
            )
            try:
                next_run = next_scheduled_run(discovery_schedule)
                schedule_error = ""
            except ValueError as exc:
                next_run = None
                schedule_error = str(exc)
            _record_job_discovery_phase(
                "jd_schedule", schedule_started_at, "Refresh schedule"
            )
            template_context.update(
                discovery_schedule=discovery_schedule,
                discovery_schedule_next_label=_discovery_schedule_time_label(
                    next_run
                ),
                discovery_schedule_error=schedule_error,
                discovery_schedule_cadences=(
                    (DiscoveryScheduleCadence.MANUAL.value, "Manual only"),
                    (DiscoveryScheduleCadence.DAILY.value, "Daily"),
                    (DiscoveryScheduleCadence.WEEKLY.value, "Weekly"),
                ),
                discovery_schedule_timezones=(
                    "America/Los_Angeles",
                    "America/Denver",
                    "America/Chicago",
                    "America/New_York",
                    "UTC",
                ),
                discovery_weekdays=tuple(
                    enumerate(
                        (
                            "Monday",
                            "Tuesday",
                            "Wednesday",
                            "Thursday",
                            "Friday",
                            "Saturday",
                            "Sunday",
                        )
                    )
                ),
            )
        else:
            result_tab = _discovery_result_tab(
                "ignored"
                if request.args.get("show_ignored") == "1"
                else request.args.get("result_tab")
            )
            page = _discovery_positive_int(request.args.get("page"), default=1)
            per_page = _discovery_page_size(request.args.get("per_page"))
            discovery_filters = _discovery_result_filters(request.args)
            discovery_results_inline = request.args.get("render_results") == "1"
            requested_pagination = _discovery_pagination(
                0, page=page, per_page=per_page
            )
            result_context: dict[str, Any] = {
                "discovery_results_inline": discovery_results_inline,
                "discovery_result_tab": result_tab,
                "discovery_result_tabs": (
                    ("recommended", "Recommended"),
                    ("possible", "Possible matches"),
                    ("pending", "Awaiting assessment"),
                    ("low_match", "Low matches"),
                    ("saved", "Saved"),
                    ("ignored", "Ignored"),
                ),
                "discovery_pagination": requested_pagination,
                "discovery_page_sizes": _DISCOVERY_PAGE_SIZES,
                "discovery_filters": discovery_filters,
                "discovery_minimum_fit_options": _DISCOVERY_MINIMUM_FIT_OPTIONS,
                "discovery_confidence_options": (
                    ("high,medium", "High and Medium"),
                    ("high", "High only"),
                    ("medium", "Medium only"),
                    ("low", "Low only"),
                    ("high,medium,low", "All confidence levels"),
                ),
                "discovery_recommendation_options": (
                    ("all_viable", "Strong, Good, and Stretch"),
                    ("strong", "Strong match only"),
                    ("good", "Good match only"),
                    ("stretch", "Stretch opportunities only"),
                    ("all", "All recommendation tiers"),
                ),
                "discovery_sort_options": (
                    ("recommended", "Recommended order"),
                    ("job_fit", "Job Fit"),
                    ("confidence", "Confidence"),
                    ("newest", "Newest posting"),
                ),
                "discovery_results_fallback_url": url_for(
                    "application_builder.job_discovery_workspace",
                    result_tab=result_tab,
                    page=page,
                    per_page=per_page,
                    min_fit=discovery_filters.minimum_fit,
                    confidence=discovery_filters.confidence_query,
                    recommendation=discovery_filters.recommendation_filter,
                    sort=discovery_filters.sort_mode,
                    render_results=1,
                ),
            }

            if discovery_results_inline:
                # Progressive-enhancement fallback for browsers without JavaScript.
                # Normal page requests render only the shell and skeleton; the
                # compact result page is loaded by ``job_discovery_results_json``.
                result_profile_started_at = perf_counter()
                discovery_profile = _discovery_candidate_profile(
                    current,
                    owner_id=owner_id,
                )
                _record_job_discovery_phase(
                    "jd_result_profile",
                    result_profile_started_at,
                    "Candidate profile",
                )
                result_index_started_at = perf_counter()
                (
                    discovery_cards,
                    discovery_result_summary,
                    discovery_pagination,
                ) = _discovery_result_cards(
                    owner_id,
                    discovery_profile,
                    result_tab=result_tab,
                    page=page,
                    per_page=per_page,
                    maximum_posting_age_days=(
                        discovery_preferences.maximum_posting_age_days
                    ),
                    filters=discovery_filters,
                    allowed_source_ids=tuple(
                        source.id for source in enabled_discovery_sources
                    ),
                )
                _record_job_discovery_phase(
                    "jd_result_index",
                    result_index_started_at,
                    "Result index read",
                )
                g.job_discovery_timing_index_state = (
                    "stale"
                    if discovery_result_summary.get("index_stale")
                    else "current"
                )
                result_context.update(
                    discovery_cards=discovery_cards,
                    discovery_dispositions=DiscoveryJobDisposition,
                    discovery_result_summary=discovery_result_summary,
                    discovery_pagination=discovery_pagination,
                )
            else:
                g.job_discovery_timing_index_state = "deferred_json"

            template_context.update(result_context)

        template_started_at = perf_counter()
        rendered_page = render_template(
            "application_builder/job_discovery.html",
            **template_context,
        )
        _record_job_discovery_phase(
            "jd_template", template_started_at, "Template render"
        )
        return rendered_page

    @application_builder_bp.get("/job-discovery/results.json")
    def job_discovery_results_json():
        """Return one compact, private result page after the HTML shell renders."""

        request_started_at = perf_counter()
        owner_id = g.application_owner_id
        current = state(hydrate_documents=False)
        result_tab = _discovery_result_tab(
            "ignored"
            if request.args.get("show_ignored") == "1"
            else request.args.get("result_tab")
        )
        page = _discovery_positive_int(request.args.get("page"), default=1)
        per_page = _discovery_page_size(request.args.get("per_page"))
        discovery_filters = _discovery_result_filters(request.args)

        source_started_at = perf_counter()
        discovery_sources = discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID
        )
        enabled_discovery_sources = tuple(
            source for source in discovery_sources if source.enabled
        )
        source_ms = max(0.0, (perf_counter() - source_started_at) * 1000.0)

        preferences_started_at = perf_counter()
        discovery_preferences = _discovery_search_preferences(owner_id, current)
        preferences_ms = max(
            0.0, (perf_counter() - preferences_started_at) * 1000.0
        )

        profile_started_at = perf_counter()
        discovery_profile = _discovery_candidate_profile(
            current,
            owner_id=owner_id,
        )
        profile_ms = max(0.0, (perf_counter() - profile_started_at) * 1000.0)

        index_started_at = perf_counter()
        (
            discovery_cards,
            discovery_result_summary,
            discovery_pagination,
        ) = _discovery_result_cards(
            owner_id,
            discovery_profile,
            result_tab=result_tab,
            page=page,
            per_page=per_page,
            maximum_posting_age_days=(
                discovery_preferences.maximum_posting_age_days
            ),
            filters=discovery_filters,
            allowed_source_ids=tuple(
                source.id for source in enabled_discovery_sources
            ),
        )
        index_ms = max(0.0, (perf_counter() - index_started_at) * 1000.0)

        template_started_at = perf_counter()
        results_html = render_template(
            "application_builder/_discovery_results_content.html",
            can_manage_job_catalog=_current_user_can_manage_job_catalog(),
            discovery_source_count=len(discovery_sources),
            discovery_cards=discovery_cards,
            discovery_dispositions=DiscoveryJobDisposition,
            discovery_result_summary=discovery_result_summary,
            discovery_result_tab=result_tab,
            discovery_result_tabs=(
                ("recommended", "Recommended"),
                ("possible", "Possible matches"),
                ("pending", "Awaiting assessment"),
                ("low_match", "Low matches"),
                ("saved", "Saved"),
                ("ignored", "Ignored"),
            ),
            discovery_pagination=discovery_pagination,
            discovery_page_sizes=_DISCOVERY_PAGE_SIZES,
            discovery_filters=discovery_filters,
            discovery_minimum_fit_options=_DISCOVERY_MINIMUM_FIT_OPTIONS,
            discovery_confidence_options=(
                ("high,medium", "High and Medium"),
                ("high", "High only"),
                ("medium", "Medium only"),
                ("low", "Low only"),
                ("high,medium,low", "All confidence levels"),
            ),
            discovery_recommendation_options=(
                ("all_viable", "Strong, Good, and Stretch"),
                ("strong", "Strong match only"),
                ("good", "Good match only"),
                ("stretch", "Stretch opportunities only"),
                ("all", "All recommendation tiers"),
            ),
            discovery_sort_options=(
                ("recommended", "Recommended order"),
                ("job_fit", "Job Fit"),
                ("confidence", "Confidence"),
                ("newest", "Newest posting"),
            ),
        )
        template_ms = max(0.0, (perf_counter() - template_started_at) * 1000.0)
        total_ms = max(0.0, (perf_counter() - request_started_at) * 1000.0)

        page_url = (
            url_for(
                "application_builder.job_discovery_workspace",
                result_tab=result_tab,
                page=discovery_pagination["page"],
                per_page=discovery_pagination["per_page"],
                min_fit=discovery_filters.minimum_fit,
                confidence=discovery_filters.confidence_query,
                recommendation=discovery_filters.recommendation_filter,
                sort=discovery_filters.sort_mode,
            )
            + "#job-discovery-results"
        )
        response = jsonify(
            {
                "ok": True,
                "html": results_html,
                "summary": discovery_result_summary,
                "pagination": discovery_pagination,
                "result_tab": result_tab,
                "index_stale": bool(
                    discovery_result_summary.get("index_stale")
                ),
                "page_url": page_url,
            }
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        response.headers["Server-Timing"] = ", ".join(
            (
                f'jd_json_sources;dur={source_ms:.2f};desc="Company sources"',
                f'jd_json_preferences;dur={preferences_ms:.2f};desc="Search preferences"',
                f'jd_json_profile;dur={profile_ms:.2f};desc="Candidate profile"',
                f'jd_json_index;dur={index_ms:.2f};desc="Result index page"',
                f'jd_json_template;dur={template_ms:.2f};desc="Result fragment render"',
                f'jd_json_total;dur={total_ms:.2f};desc="Result JSON total"',
            )
        )
        result_log_method = (
            current_app.logger.warning
            if total_ms >= _job_discovery_slow_request_threshold_ms()
            else current_app.logger.info
        )
        result_log_method(
            "Job Discovery result JSON owner_scope=%s tab=%s page=%s "
            "cards=%s stale=%s total_ms=%.2f index_ms=%.2f",
            hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:16],
            result_tab,
            discovery_pagination["page"],
            len(discovery_cards),
            bool(discovery_result_summary.get("index_stale")),
            total_ms,
            index_ms,
        )
        return response

    @application_builder_bp.get(
        "/discovery/jobs/<source_id>/<job_id>/analysis"
    )
    def discovered_job_analysis(source_id: str, job_id: str):
        owner_id = _application_owner_id()
        job = discovery_store.get_discovered_job(owner_id, source_id, job_id)
        if job is None:
            abort(404)
        profile = _discovery_candidate_profile(state(), owner_id=owner_id)
        fit = discovery_store.get_fit_snapshot(
            owner_id,
            job.id,
            profile.fingerprint,
            job.description_fingerprint,
        ) or discovery_store.get_fit_snapshot(
            owner_id, job.id, profile.fingerprint
        )
        analysis_error = ""
        if fit is None:
            result = JobDiscoveryService(store=discovery_store).assess_existing_jobs(
                [job], profile
            )
            if result.ranked_jobs:
                fit = result.ranked_jobs[0].fit_snapshot
            elif result.analysis_errors:
                analysis_error = result.analysis_errors[0].message
        return render_template(
            "application_builder/_discovery_job_analysis.html",
            analysis=_discovery_card_analysis(job, profile, fit),
            analysis_error=analysis_error,
        )

    @application_builder_bp.get("/")
    def index():
        active_tab = request.args.get("tab", "applications")
        if active_tab not in {"tailoring", "reports", "applications", "configuration"}:
            active_tab = "applications"
        if active_tab == "configuration" and not bool(session.get("is_admin")):
            abort(403, description="Administrator access is required for AI configuration.")

        current = state()
        ensure_recommended_resume_style(current)

        if active_tab == "applications":
            owner_id = g.application_owner_id
            applications, readiness_by_application = (
                _applications_with_calculated_readiness(
                    application_store.list_for_owner(owner_id)
                )
            )
            style_options = resume_style_options()
            return render_template(
                "application_builder/applications.html",
                active_tab=active_tab,
                applications=applications,
                readiness_by_application=readiness_by_application,
                application_metrics=build_application_metrics(applications),
                application_status_options=APPLICATION_STATUS_OPTIONS,
                resume_version_options=RESUME_VERSION_OPTIONS,
                upcoming_event_type_options=UPCOMING_EVENT_TYPE_OPTIONS,
                interview_audience_suggestions=INTERVIEW_AUDIENCE_SUGGESTIONS,
                resume_style_options=style_options,
                resume_style_labels={
                    option["key"]: f'{option["label"]} — {option["audience"]}'
                    for option in style_options
                },
            )

        active_guided_stage = guided_stage_for_state(current)
        selected_workflow_stage = normalize_workflow_step(
            request.args.get("stage"), fallback=active_guided_stage
        )
        selected_workflow_panel = WORKFLOW_PANEL_BY_STEP[selected_workflow_stage]

        report_view_name = request.args.get("report", "initial")
        if report_view_name not in {"initial", "draft", "final", "comparison"}:
            report_view_name = "initial"

        try:
            models = resolve_models(current)
            model_error = ""
        except ValueError as exc:
            models = ActiveModels("", "", None, None)
            model_error = str(exc)

        current_input = input_fingerprint(current, models)
        source_profile = current.source_profile
        application_baseline_frozen = _application_baseline_is_frozen(current)
        application_baseline_status = str(
            getattr(g, "application_baseline_status", "") or ""
        )
        application_baseline_outdated = bool(
            application_baseline_frozen
            and application_baseline_status == "frozen"
        )
        profile = current.confirmed_profile or source_profile
        analysis = current.analysis
        proposal = working_proposal_for_stage(current)
        if analysis is not None and proposal is not None:
            proposal = _apply_confirmed_title_interpretations(
                str(getattr(g, "application_owner_id", "") or ""),
                profile,
                proposal,
            )
        draft_proposal = current.draft_proposal
        final_proposal = current.final_proposal
        input_is_current = bool(
            analysis
            and proposal
            and current.analyzed_input_fingerprint == current_input
        )
        analysis_is_current = bool(
            analysis and current.analysis_input_fingerprint == current_input
        )

        initial_report_is_current = bool(
            current.initial_report
            and current.initial_report_input_fingerprint
            == initial_report_fingerprint(current)
        )
        initial_report_data = None
        if (
            initial_report_is_current
            and current.initial_report
            and current.initial_report_analysis
            and current.initial_report_proposal
        ):
            initial_report_data = report_view(
                current.initial_report,
                profile=source_profile,
                analysis=current.initial_report_analysis,
                proposal=current.initial_report_proposal,
                candidate_answers=None,
            )

        initial_editor_proposal = build_initial_resume_proposal(
            source_profile, current.initial_evidence_proposal
        )
        initial_editor_data = proposal_view_data(
            source_profile,
            analysis,
            initial_editor_proposal,
            current_title=initial_resume_title(source_profile),
        )

        setup_snapshot = current.workflow_step_snapshots.get("initial")
        confirmation_snapshot = current.workflow_step_snapshots.get("confirmation")
        alignment_snapshot = current.workflow_step_snapshots.get("draft")
        edit_setup_snapshot = bool(
            selected_workflow_stage == "setup"
            and current.workflow_stage != "initial"
            and request.args.get("edit") == "setup"
        )

        job_aligned_proposal = (
            alignment_snapshot.proposal
            if current.workflow_stage == "final"
            and alignment_snapshot is not None
            and alignment_snapshot.proposal is not None
            else draft_proposal
        )
        job_aligned_profile = (
            alignment_snapshot.profile
            if current.workflow_stage == "final"
            and alignment_snapshot is not None
            and alignment_snapshot.profile is not None
            else profile
        )

        draft_fingerprint = _proposal_fingerprint(draft_proposal)
        draft_report_is_current = bool(
            current.updated_report
            and current.updated_report_input_fingerprint == current_input
            and current.updated_report_proposal_fingerprint == draft_fingerprint
        )
        draft_report_data = None
        if draft_report_is_current and current.updated_report and draft_proposal:
            draft_report_data = report_view(
                current.updated_report,
                profile=profile,
                analysis=analysis,
                proposal=draft_proposal,
                candidate_answers=current.candidate_answers,
            )

        tailoring_changes = None
        if analysis and job_aligned_proposal and current.confirmation_complete:
            tailoring_changes = summarize_tailoring_changes(
                initial_editor_proposal,
                job_aligned_proposal,
                job_aligned_profile,
                analysis,
                reference_title=initial_resume_title(source_profile),
                current_title=analysis.target_title,
            )
            report_impacts = (
                tailoring_report_impacts(
                    current.initial_report, current.updated_report, analysis
                )
                if initial_report_is_current
                and current.initial_report
                and draft_report_is_current
                and current.updated_report
                else {
                    "available": False,
                    "title": None,
                    "summary": None,
                    "skills": {},
                    "requirements": {},
                }
            )
            tailoring_changes["report_impacts"] = report_impacts
            if report_impacts.get("available"):
                bullet_impacts = attributable_bullet_report_impacts(
                    initial_editor_proposal,
                    job_aligned_proposal,
                    analysis,
                    report_impacts.get("requirements", {}),
                    set(tailoring_changes["bullet_details"]),
                )
                for source_id, detail in tailoring_changes["bullet_details"].items():
                    detail["report_impact"] = bullet_impacts.get(source_id)

        tailor_stage_editor_data = (
            proposal_view_data(
                job_aligned_profile,
                analysis,
                job_aligned_proposal,
                comparison_proposal=initial_editor_proposal,
                comparison_label=INITIAL_RESUME_LABEL,
                current_label=JOB_ALIGNED_RESUME_LABEL,
                comparison_title=initial_resume_title(source_profile),
                current_title=analysis.target_title,
                bullet_tailoring_details=(
                    tailoring_changes["bullet_details"]
                    if tailoring_changes
                    else {}
                ),
            )
            if analysis and job_aligned_proposal and current.confirmation_complete
            else None
        )
        if (
            tailor_stage_editor_data is not None
            and current.workflow_stage == "final"
            and alignment_snapshot is not None
        ):
            tailor_stage_editor_data["workflow_snapshot"] = True
            tailor_stage_editor_data["snapshot_stage"] = "draft"
            tailor_stage_editor_data["snapshot_label"] = (
                "Step 3 · Review Tailored Resume"
            )
            tailor_stage_editor_data["snapshot_captured_at"] = (
                alignment_snapshot.captured_at
            )

        final_reference_proposal = job_aligned_proposal or draft_proposal
        final_comparison_proposal = (
            final_reference_proposal
            if final_reference_proposal is not None
            and final_proposal is not None
            and _proposal_json(final_reference_proposal)
            != _proposal_json(final_proposal)
            else None
        )
        final_resume_title = effective_final_resume_title(current)
        final_editor_data = (
            proposal_view_data(
                profile,
                analysis,
                final_proposal,
                comparison_proposal=final_comparison_proposal,
                comparison_label=JOB_ALIGNED_RESUME_LABEL,
                current_label=FINAL_RESUME_LABEL,
                comparison_title=analysis.target_title,
                current_title=final_resume_title,
                include_comparison_reasons=True,
            )
            if analysis and final_proposal and current.confirmation_complete
            else None
        )

        deterministic_issues = (
            validate_proposal(profile, analysis, proposal)
            if analysis and proposal
            else []
        )
        proposal_data = (
            proposal_view_data(profile, analysis, proposal)
            if analysis and proposal
            else None
        )

        final_fingerprint = _proposal_fingerprint(final_proposal)
        final_report_is_current = bool(
            current.final_report
            and current.final_report_input_fingerprint == current_input
            and current.final_report_proposal_fingerprint == final_fingerprint
        )
        final_report_data = None
        if (
            current.final_report
            and current.final_report_proposal
            and current.final_report_profile
            and analysis
        ):
            final_report_data = report_view(
                current.final_report,
                profile=current.final_report_profile,
                analysis=analysis,
                proposal=current.final_report_proposal,
                candidate_answers=current.final_report_candidate_answers,
            )

        comparisons_data: dict[str, Any] = {}
        if initial_report_is_current and current.initial_report:
            if draft_report_is_current and current.updated_report:
                comparisons_data["initial_draft"] = comparison_view(
                    current.initial_report,
                    current.updated_report,
                    initial_label=INITIAL_RESUME_LABEL,
                    updated_label=JOB_ALIGNED_RESUME_LABEL,
                )
            if current.final_report:
                comparisons_data["initial_final"] = comparison_view(
                    current.initial_report,
                    current.final_report,
                    initial_label=INITIAL_RESUME_LABEL,
                    updated_label=FINAL_RESUME_LABEL,
                )
        if draft_report_is_current and current.updated_report and current.final_report:
            comparisons_data["draft_final"] = comparison_view(
                current.updated_report,
                current.final_report,
                initial_label=JOB_ALIGNED_RESUME_LABEL,
                updated_label=FINAL_RESUME_LABEL,
            )

        requirement_lookup = (
            {item.id: item for item in analysis.requirements} if analysis else {}
        )
        bullet_experience_lookup = {
            bullet.id: experience.id
            for experience in profile.experiences
            for bullet in experience.bullets
        }
        confirmation_rows = []
        if proposal:
            ordered_questions = order_candidate_questions_for_display(
                proposal.candidate_questions
            )
            for display_position, question in enumerate(ordered_questions, start=1):
                confirmation_rows.append(
                    {
                        "question": question,
                        "display_id": candidate_question_display_label(
                            question, display_position
                        ),
                        "requirement": requirement_lookup.get(
                            question.requirement_id
                        ),
                        "choice": current.confirmation_draft.get(
                            f"choice__{question.id}", ""
                        ),
                        "answer": current.confirmation_draft.get(
                            f"answer__{question.id}", ""
                        ),
                        "experience_id": current.confirmation_draft.get(
                            f"experience__{question.id}",
                            bullet_experience_lookup.get(question.source_id, ""),
                        ),
                        "placement": current.confirmation_draft.get(
                            f"placement__{question.id}",
                            "update_existing"
                            if question.id.startswith("FQ")
                            else "auto",
                        ),
                    }
                )

        confirmation_display_answers = (
            confirmation_snapshot.candidate_answers
            if confirmation_snapshot is not None
            else current.candidate_answers
        )
        confirmation_display_profile = (
            confirmation_snapshot.profile
            if confirmation_snapshot is not None
            and confirmation_snapshot.profile is not None
            else current.confirmed_profile
        )
        confirmation_display_proposal = (
            confirmation_snapshot.proposal
            if confirmation_snapshot is not None
            and confirmation_snapshot.proposal is not None
            else proposal
        )
        confirmed_answer_dispositions = confirmation_dispositions(
            confirmation_display_profile,
            confirmation_display_proposal,
            confirmation_display_answers,
        )

        api_ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        ai_ready = api_ready and bool(models.analysis_tailoring_model and models.evidence_review_model)
        blocking_local = any(
            issue.severity == "blocking" for issue in deterministic_issues
        )
        guided_workflow = build_guided_workflow(
            workflow_stage=current.workflow_stage,
            input_is_current=input_is_current,
            confirmation_complete=current.confirmation_complete,
            blocking_local=blocking_local,
            resume_ready=bool(current.final_resume_bytes),
            quality_review_started=getattr(
                current, "quality_review_started", False
            ),
            final_proposal_ready=current.final_proposal is not None,
            application_id=(g.active_application.id if g.active_application else ""),
        )

        if g.active_application is not None:
            if current.final_resume_bytes:
                dashboard_resume_version = FINAL_RESUME_LABEL
            elif current.draft_proposal is not None:
                dashboard_resume_version = (
                    f"Tailored Resume v{max(1, current.draft_revision)}"
                )
            else:
                dashboard_resume_version = INITIAL_RESUME_LABEL
            dashboard_status = g.active_application.status
            if (
                dashboard_status in {"draft", "considering"}
                and guided_workflow["current_key"] != "setup"
            ):
                dashboard_status = "preparing"
            g.active_application = application_store.update_builder_progress(
                g.application_owner_id,
                g.active_application.id,
                workflow_step=guided_workflow["current_key"],
                resume_version=dashboard_resume_version,
                company=(
                    current.analysis.target_company
                    if current.analysis is not None
                    else g.active_application.company
                ),
                role=current.target_title or g.active_application.role,
                job_description=current.job_description,
                status=dashboard_status,
            )
            if g.active_application is not None:
                _persist_resume_findings(
                    g.active_application.id,
                    build_resume_findings_snapshot(
                        current,
                        company=g.active_application.company,
                        role=g.active_application.role,
                        job_description=g.active_application.job_description,
                    ),
                )

        initial_editor_filename = (
            safe_filename(f"{source_profile.name}_Initial_Resume") + ".docx"
        )
        job_aligned_editor_filename = (
            safe_filename(f"{profile.name}_Job_Aligned_Resume") + ".docx"
        )
        final_editor_filename = final_resume_filename(
            profile, final_resume_title, "docx"
        )
        application_records = application_store.list_for_owner(
            g.application_owner_id
        )
        preliminary_fit = (
            preliminary_application_fit(current, application_records)
            if input_is_current
            else None
        )
        application_fit = (
            current_application_fit(current, application_records)
            if input_is_current
            else None
        )
        career_translation_assessment = career_translation_assessment_view(
            proposal
        )
        reusable_profile = getattr(
            g, "reusable_career_profile", ReusableCareerProfile()
        )
        resume_language_choice = _resolved_resume_language(current)
        return render_template(
            "application_builder/index.html",
            state=current,
            active_tab=active_tab,
            guided_workflow=guided_workflow,
            preliminary_application_fit=preliminary_fit,
            application_fit=application_fit,
            career_translation_assessment=career_translation_assessment,
            career_background=_effective_career_background(current),
            country_options=COUNTRY_OPTIONS,
            career_background_additions=_career_background_application_additions(
                current.career_background,
                reusable_profile,
            ),
            resume_language_choice=resume_language_choice,
            resume_language_options=resume_language_options(),
            selected_resume_language=current.career_background.resume_language,
            resume_labels=resume_labels(resume_language_choice.code),
            selected_workflow_stage=selected_workflow_stage,
            selected_workflow_panel=selected_workflow_panel,
            edit_setup_snapshot=edit_setup_snapshot,
            setup_snapshot=setup_snapshot,
            confirmation_snapshot=confirmation_snapshot,
            report_view_name=report_view_name,
            models=models,
            model_error=model_error,
            api_ready=api_ready,
            ai_ready=ai_ready,
            source_profile=source_profile,
            application_baseline_frozen=application_baseline_frozen,
            application_baseline_outdated=application_baseline_outdated,
            application_baseline_status=application_baseline_status,
            profile=profile,
            input_is_current=input_is_current,
            analysis_is_current=analysis_is_current,
            initial_report_is_current=initial_report_is_current,
            initial_report_stale=bool(
                current.initial_report and not initial_report_is_current
            ),
            initial_report=initial_report_data,
            analysis=analysis,
            proposal=proposal,
            proposal_data=proposal_data,
            initial_editor_data=initial_editor_data,
            tailor_stage_editor_data=tailor_stage_editor_data,
            tailor_stage_profile=job_aligned_profile,
            tailor_stage_tailoring_changes=tailoring_changes,
            final_editor_data=final_editor_data,
            initial_editor_filename=initial_editor_filename,
            job_aligned_editor_filename=job_aligned_editor_filename,
            final_editor_filename=final_editor_filename,
            final_resume_title=final_resume_title,
            blocking_local=blocking_local,
            confirmation_rows=confirmation_rows,
            confirmation_experiences=source_profile.experiences,
            confirmation_display_answers=confirmation_display_answers,
            confirmed_answer_dispositions=confirmed_answer_dispositions,
            draft_report=draft_report_data,
            draft_report_stale=bool(
                current.updated_report and not draft_report_is_current
            ),
            final_report=final_report_data,
            optimization_summary=final_optimization_summary(
                current.optimization_report_before,
                current.optimization_report_after,
            ),
            optimization_remaining=final_optimization_recommendations(
                current.optimization_report_after or current.final_report
            ),
            career_stage_options=career_stage_options(),
            resume_format_options=resume_format_options(),
            visual_design_options=visual_design_options(),
            selected_career_stage=normalize_career_stage(current.resume_career_stage),
            selected_resume_format=normalize_resume_format(current.resume_format),
            selected_visual_design=normalize_visual_design(current.resume_visual_design),
            recommended_career_stage=recommend_career_stage(
                current.job_description,
                analysis.target_title if analysis is not None else current.target_title,
                candidate_profile=profile,
                candidate_answers=current.candidate_answers,
            ),
            recommended_resume_format=recommend_resume_format(
                current.job_description,
                analysis.target_title if analysis is not None else current.target_title,
                candidate_profile=profile,
                candidate_answers=current.candidate_answers,
            ),
            recommended_visual_design=recommend_visual_design(
                current.job_description,
                analysis.target_title if analysis is not None else current.target_title,
                resume_format=current.resume_format,
                career_stage=current.resume_career_stage,
                candidate_profile=profile,
                candidate_answers=current.candidate_answers,
            ),
            selected_resume_preference_label=current_resume_preference_label(current),
            final_report_stale=bool(
                current.final_report and not final_report_is_current
            ),
            final_report_created_at=current.final_report_created_at,
            comparisons=comparisons_data,
            initial_resume_title=initial_resume_title,
            profile_stats={
                "skills": len(source_profile.skills.all_non_language_skills()),
                "bullets": len(source_profile.bullet_lookup()),
            },
        )

    @application_builder_bp.post("/configuration")
    def configuration():
        if not bool(session.get("is_admin")):
            abort(403, description="Administrator access is required for AI configuration.")
        current = state()
        try:
            old_models = resolve_models(current)
        except ValueError:
            old_models = ActiveModels("", "", None, None)

        mode = request.form.get("processing_mode", current.processing_mode)
        if mode not in PROCESSING_MODE_ORDER:
            flash("Unknown processing mode.", "error")
            return redirect(url_for("application_builder.index", tab="configuration"))
        current.processing_mode = mode
        current.custom_analysis_tailoring_model = request.form.get(
            "custom_analysis_tailoring_model", current.custom_analysis_tailoring_model
        ).strip()
        current.custom_evidence_review_model = request.form.get(
            "custom_evidence_review_model", current.custom_evidence_review_model
        ).strip()
        analysis_tailoring_effort = request.form.get(
            "custom_analysis_tailoring_reasoning_effort", "automatic"
        )
        evidence_review_effort = request.form.get(
            "custom_evidence_review_reasoning_effort", "automatic"
        )
        current.custom_analysis_tailoring_reasoning_effort = (
            None
            if analysis_tailoring_effort == "automatic"
            else analysis_tailoring_effort
        )
        current.custom_evidence_review_reasoning_effort = (
            None
            if evidence_review_effort == "automatic"
            else evidence_review_effort
        )

        try:
            new_models = resolve_models(current)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("application_builder.index", tab="configuration"))

        old_analysis_tailoring = (
            old_models.analysis_tailoring_model,
            old_models.analysis_tailoring_reasoning_effort,
        )
        new_analysis_tailoring = (
            new_models.analysis_tailoring_model,
            new_models.analysis_tailoring_reasoning_effort,
        )
        old_evidence_review = (
            old_models.evidence_review_model,
            old_models.evidence_review_reasoning_effort,
        )
        new_evidence_review = (
            new_models.evidence_review_model,
            new_models.evidence_review_reasoning_effort,
        )
        if old_analysis_tailoring != new_analysis_tailoring:
            current.clear_results()
            flash("AI configuration updated. Cached analysis and proposals were cleared.", "success")
        elif old_evidence_review != new_evidence_review:
            current.clear_tailoring_results()
            flash(
                "Evidence-review configuration updated. Job-aligned and final resume versions were cleared because Step 3 must be verified again.",
                "success",
            )
        else:
            flash("Configuration saved.", "success")
        return redirect(url_for("application_builder.index", tab="configuration"))

    @application_builder_bp.post("/profile/upload")
    def upload_profile():
        uploaded = request.files.get("profile_file")
        import_strategy = str(request.form.get("import_strategy") or "").strip().casefold()
        return_to = str(request.form.get("return_to") or "").strip().casefold()
        is_career_translation = return_to == "career_translation"
        redirect_target = (
            url_for("application_builder.career_translation_workspace")
            if is_career_translation
            else (
                url_for("application_builder.index", tab="tailoring", stage="setup") + "#resume-import"
                if return_to == "setup"
                else url_for("application_builder.index", tab="configuration") + "#candidate-profile"
            )
        )
        current = state()
        existing_creation_method = _baseline_creation_method(current)
        manual_profile_to_merge: CandidateProfile | None = None

        if not is_career_translation:
            flash(
                "Application Baseline is managed in Foundation. Update the Baseline Resume there; applications that have not started tailoring will sync automatically.",
                "info",
            )
            return redirect(
                url_for("application_builder.career_translation_workspace")
            )

        if is_career_translation:
            reusable_profile = getattr(
                g, "reusable_career_profile", ReusableCareerProfile()
            )
            current.career_background.target_country = (
                reusable_profile.target_country if reusable_profile.enabled else ""
            )
            current.career_background.resume_language = " ".join(
                str(request.form.get("resume_language") or "").split()
            )
            current.career_background.target_role = normalize_target_title(
                str(request.form.get("target_role") or "")
            )

        if not uploaded or not uploaded.filename:
            if not is_career_translation:
                flash("Choose a PDF, Word, text, Markdown, or Verified Resume Evidence JSON file.", "error")
                return redirect(redirect_target)
            if not current.source_profile.all_source_text().strip():
                flash(
                    "Baseline Resume preferences saved. Import a resume to generate the Baseline Resume.",
                    "success",
                )
                return redirect(redirect_target)
            try:
                models = resolve_models(current)
                translation_ai = ResumeAI(
                    models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
                )
                _ensure_target_language_profile(current, translation_ai)
                if (
                    _baseline_creation_method(current) == "mixed"
                    and current.manual_source_profile is not None
                ):
                    current.source_profile = merge_candidate_profiles(
                        current.source_profile,
                        current.manual_source_profile,
                    )
            except (ResumeAIError, ValueError, RuntimeError) as exc:
                flash(
                    "Baseline Resume preferences were saved, but the resume could not be regenerated: "
                    + str(exc),
                    "warning",
                )
            else:
                roles_synced = _sync_baseline_roles_to_evidence_library(current)
                choice = _resolved_resume_language(current)
                source_language = _source_resume_language_code(current)
                if source_language and source_language == choice.code:
                    flash(
                        f"Baseline Resume saved in {choice.name}. The imported resume was already in {choice.name}, so no translation was needed.",
                        "success",
                    )
                else:
                    flash(
                        f"Baseline Resume saved. The reusable resume is now in {choice.name}.",
                        "success",
                    )
                if not roles_synced:
                    flash(
                        "The Baseline Resume was saved, but its employment roles could not be synchronized to Career Evidence Library. Regenerate the Baseline Resume to retry.",
                        "warning",
                    )
            return redirect(redirect_target)

        if (
            existing_creation_method in {"manual", "mixed"}
            and current.source_profile.all_source_text().strip()
        ):
            if import_strategy not in {"replace", "merge"}:
                flash(
                    "Choose whether the imported resume should replace the manually entered Baseline Resume or merge new information for review.",
                    "error",
                )
                return redirect(redirect_target)
            if import_strategy == "merge":
                manual_profile_to_merge = (
                    current.manual_source_profile.model_copy(deep=True)
                    if current.manual_source_profile is not None
                    else current.source_profile.model_copy(deep=True)
                )

        filename = uploaded.filename
        data = uploaded.read()
        replacing_existing_baseline = bool(
            current.source_resume_key
            or current.source_resume_fingerprint
            or current.profile_upload_name
            or current.source_profile.all_source_text().strip()
        )
        translation_ai: ResumeAI | None = None
        import_adjustments: list[str] = []
        detected_import_language = ""
        try:
            if resume_extension(filename) == ".json":
                profile = load_candidate_profile_bytes(data)
                detected_import_language = detect_text_language(
                    profile.all_source_text()
                )
            else:
                resume_text = extract_resume_text(data, filename)
                detected_import_language = detect_text_language(resume_text)
                models = resolve_models(current)
                translation_ai = ResumeAI(
                    models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
                )
                profile = translation_ai.create_candidate_profile_from_resume(
                    resume_text=resume_text,
                    filename=filename,
                )
                import_adjustments = list(
                    getattr(translation_ai, "last_resume_import_adjustments", [])
                    or []
                )
        except (ResumeAIError, ValueError, RuntimeError) as exc:
            flash(f"Could not import the resume: {exc}", "error")
            return redirect(redirect_target)
        except Exception as exc:
            current_app.logger.exception("Unexpected resume import failure")
            flash(f"Could not import the resume: {exc}", "error")
            return redirect(redirect_target)

        source_fingerprint = hashlib.sha256(data).hexdigest()
        source_object_key = workflow_object_key(
            current_app.config,
            str(getattr(g, "application_owner_id", "") or ""),
            str(getattr(g, "workflow_key", "") or "scratch"),
            "original-resume",
            filename,
            source_fingerprint,
        )
        document_store.put(
            source_object_key,
            data,
            uploaded.mimetype or "application/octet-stream",
            metadata={
                "artifact-type": "original-resume",
                "source-fingerprint": source_fingerprint,
            },
        )
        previous_source_key = current.source_resume_key
        current.original_source_profile = profile.model_copy(deep=True)
        current.source_profile = profile
        current.source_resume_language = (
            detected_import_language
            or detect_text_language(profile.all_source_text())
        )
        current.source_profile_language = ""
        current.source_profile_translation_fingerprint = ""
        current.profile_upload_name = filename
        current.source_resume_key = source_object_key
        current.source_resume_fingerprint = source_fingerprint
        current.source_resume_contact_links_fingerprint = source_fingerprint
        current.baseline_creation_method = "import"
        current.manual_source_profile = None

        translation_warning = ""
        translated_choice = _resolved_resume_language(current)
        try:
            if translation_ai is None:
                models = resolve_models(current)
                translation_ai = ResumeAI(
                    models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
                )
            _ensure_target_language_profile(current, translation_ai)
        except (ResumeAIError, ValueError, RuntimeError) as exc:
            # Importing the Imported Resume must still succeed when the AI
            # provider is temporarily unavailable. The workflow start route will
            # retry the same target-language conversion before analysis.
            current.source_profile = profile
            current.source_profile_language = ""
            current.source_profile_translation_fingerprint = ""
            translation_warning = str(exc)
            current_app.logger.warning(
                "Resume imported but target-language conversion was deferred: %s",
                exc,
            )
        except Exception as exc:
            current.source_profile = profile
            current.source_profile_language = ""
            current.source_profile_translation_fingerprint = ""
            translation_warning = "An unexpected translation error occurred."
            current_app.logger.exception(
                "Unexpected target-language conversion failure after resume import"
            )

        if manual_profile_to_merge is not None:
            current.source_profile = merge_candidate_profiles(
                current.source_profile,
                manual_profile_to_merge,
            )
            current.manual_source_profile = manual_profile_to_merge
            current.baseline_creation_method = "mixed"

        active_application = getattr(g, "active_application", None)
        if active_application is not None:
            application_store.update_builder_progress(
                str(getattr(g, "application_owner_id", "") or ""),
                active_application.id,
                workflow_step=active_application.workflow_step,
                original_resume_key=source_object_key,
            )
        current.clear_results()

        # Commit the replacement before removing the document referenced by the
        # previously saved baseline. This prevents the redirect from reloading an
        # older profile and avoids leaving the stored workflow pointed at a file
        # that was already deleted when a concurrent update wins.
        try:
            _persist_workflow_state_now()
        except WorkflowConflictError as exc:
            if source_object_key != previous_source_key:
                document_store.delete(source_object_key)
            return workflow_conflict_response(exc)
        except Exception:
            if source_object_key != previous_source_key:
                document_store.delete(source_object_key)
            raise

        if previous_source_key and previous_source_key != source_object_key:
            try:
                document_store.delete(previous_source_key)
            except Exception:
                current_app.logger.exception(
                    "Could not remove the replaced Baseline Resume source document"
                )

        roles_synced = True
        if not translation_warning:
            roles_synced = _sync_baseline_roles_to_evidence_library(current)

        # A revision-specific redirect also prevents a browser or intermediary
        # from presenting the pre-import preview after a successful replacement.
        redirect_target = (
            url_for(
                "application_builder.career_translation_workspace",
                baseline_revision=source_fingerprint[:12],
            )
            + "#initial-resume"
        )
        if translation_warning:
            flash(
                "The resume was imported successfully, but its target-language version could not be generated yet. "
                f"Baseline Resume generation will retry before creating the {CAREER_BASELINE_RESUME_LABEL}. "
                f"Details: {translation_warning}",
                "warning",
            )
        else:
            destination = (
                f" for {translated_choice.country}"
                if translated_choice.country
                else (
                    " for your Baseline Resume"
                    if is_career_translation
                    else " for this application"
                )
            )
            source_language = _source_resume_language_code(current)
            no_translation_needed = bool(
                source_language and source_language == translated_choice.code
            )
            preserved_message = (
                "The verified original was preserved. New application workspaces can reuse this baseline."
                if is_career_translation
                else "The verified original was preserved and previous analysis results were cleared."
            )
            if manual_profile_to_merge is not None:
                import_summary = (
                    "The resume was imported and merged with the manually entered Baseline Resume for review"
                )
            else:
                import_summary = (
                    "The new resume replaced the existing Baseline Resume"
                    if replacing_existing_baseline
                    else "The resume was imported as the Baseline Resume"
                )
            if no_translation_needed:
                flash(
                    f"{import_summary} in {translated_choice.name}. It already matches the Baseline Resume language, so no translation was needed. "
                    + preserved_message,
                    "success",
                )
            else:
                flash(
                    f"{import_summary} and translated into {translated_choice.name}{destination}. "
                    + preserved_message,
                    "success",
                )
            if import_adjustments:
                flash(
                    "The new resume was accepted. A small amount of extractor-generated wording that was not explicit in the uploaded file was omitted. Review the extracted summary, skills, education, and employment fields before using the baseline.",
                    "warning",
                )
            if not roles_synced:
                flash(
                    "The Baseline Resume was created, but its employment roles could not be synchronized to Career Evidence Library. Regenerate the Baseline Resume to retry.",
                    "warning",
                )
        return redirect(redirect_target)

    @application_builder_bp.post("/profile/default")
    def restore_default_profile():
        if getattr(g, "active_application", None) is not None:
            flash(
                "Application Baseline is managed in Foundation. Clear or replace the Baseline Resume there instead.",
                "info",
            )
            return redirect(
                url_for("application_builder.career_translation_workspace")
            )
        current = state()
        previous_source_key = current.source_resume_key
        current.source_profile = _empty_candidate_profile()
        current.original_source_profile = None
        current.baseline_creation_method = ""
        current.manual_source_profile = None
        current.source_resume_language = ""
        current.source_profile_language = ""
        current.source_profile_translation_fingerprint = ""
        current.profile_upload_name = ""
        current.source_resume_key = ""
        current.source_resume_fingerprint = ""
        current.source_resume_contact_links_fingerprint = ""
        current.foundation_baseline_fingerprint = ""
        active_application = getattr(g, "active_application", None)
        if active_application is not None:
            application_store.update_builder_progress(
                str(getattr(g, "application_owner_id", "") or ""),
                active_application.id,
                workflow_step=active_application.workflow_step,
                original_resume_key="",
            )
        current.clear_results()
        if previous_source_key:
            document_store.delete(previous_source_key)
        flash("Verified Resume Evidence cleared. No sample candidate data is loaded.", "success")
        return redirect(url_for("application_builder.index", tab="configuration"))

    @application_builder_bp.post("/reset")
    def reset_workflow():
        current = state()
        current.clear_results()
        flash("Workflow results were reset. Your configuration and current inputs were preserved.", "success")
        return redirect(url_for("application_builder.index", tab="configuration"))

    @application_builder_bp.post(
        "/applications/<application_id>/baseline/refresh"
    )
    def refresh_application_baseline(application_id: str):
        active_application = getattr(g, "active_application", None)
        if active_application is None or active_application.id != application_id:
            abort(404)

        current = state()
        previous_source_key = current.source_resume_key
        status = _sync_application_from_foundation(
            _application_owner_id(), current, force=True
        )
        if status == "missing":
            flash(
                "Create the Foundation Baseline Resume before refreshing this application.",
                "error",
            )
            return redirect(
                url_for("application_builder.career_translation_workspace")
            )

        application_store.update_builder_progress(
            _application_owner_id(),
            active_application.id,
            workflow_step="setup",
            original_resume_key="",
        )
        if previous_source_key:
            document_store.delete(previous_source_key)
        flash(
            "Application Baseline refreshed from the current Foundation Baseline Resume. Previous tailoring results were cleared.",
            "success",
        )
        return redirect(
            url_for(
                "application_builder.index",
                tab="tailoring",
                stage="setup",
                application_id=active_application.id,
            )
            + "#resume-import"
        )

    @application_builder_bp.post("/workflow/start")
    def start_workflow():
        current = state()
        update_job_fields()
        action = request.form.get("action", "")
        tailoring_started = False
        if not current.source_profile.all_source_text().strip():
            flash("Create the Foundation Baseline Resume before starting this application.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="setup")
                + "#resume-import"
            )
        if not current.job_description.strip():
            flash("Paste or upload a job description first.", "error")
            return redirect(url_for("application_builder.index", tab="tailoring"))
        try:
            models = resolve_models(current)
            ai = None
            if action in {"initial_report", "tailor"}:
                ai = ResumeAI(
                    model=models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
                )
                _ensure_target_language_profile(current, ai)
            current_input = input_fingerprint(current, models)
            analysis_is_current = bool(
                current.analysis and current.analysis_input_fingerprint == current_input
            )
            evidence_is_current = bool(
                current.initial_evidence_proposal
                and current.initial_evidence_input_fingerprint == current_input
            )

            if action == "save_inputs":
                flash("Job description and target title saved.", "success")

            elif action == "initial_report":
                if analysis_is_current:
                    analysis = current.analysis
                else:
                    ai = ResumeAI(
                        model=models.analysis_tailoring_model,
                        reasoning_effort=models.analysis_tailoring_reasoning_effort,
                    )
                    analysis = ai.analyze_job(
                        current.job_description, current.target_title
                    )
                    current.analysis = analysis
                    current.analysis_input_fingerprint = current_input
                    ensure_recommended_resume_style(current)
                evidence_source = (
                    current.initial_evidence_proposal
                    if evidence_is_current
                    else (
                        ai
                        or ResumeAI(
                            model=models.analysis_tailoring_model,
                            reasoning_effort=models.analysis_tailoring_reasoning_effort,
                        )
                    ).create_proposal(
                        current.source_profile,
                        analysis,
                        _effective_career_background(current),
                    )
                )
                evidence_source = repair_missing_bullet_proposals(
                    current.source_profile, evidence_source
                )
                evidence_source = prioritize_candidate_questions(evidence_source, analysis)
                evidence_source = ensure_career_translation_assessment(
                    current.source_profile,
                    analysis,
                    evidence_source,
                    _effective_career_background(current),
                )
                evidence_source = _apply_confirmed_title_interpretations(
                    str(getattr(g, "application_owner_id", "") or ""),
                    current.source_profile,
                    evidence_source,
                )
                current.initial_evidence_proposal = evidence_source.model_copy(
                    deep=True
                )
                current.initial_evidence_input_fingerprint = current_input
                created = _refresh_initial_resume_report(
                    current, analysis, evidence_source, force=True
                )
                if created:
                    flash(
                        f"{APPLICATION_BASELINE_LABEL} Report refreshed successfully.", "success"
                    )
                else:
                    flash(
                        f"The {APPLICATION_BASELINE_LABEL} Report could not be refreshed: "
                        + (current.initial_report_error or "Unknown report error."),
                        "warning",
                    )

            elif action == "tailor":
                existing_analysis = current.analysis if analysis_is_current else None
                existing_evidence = current.initial_evidence_proposal if evidence_is_current else None
                current.clear_tailoring_results()
                capture_workflow_step_snapshot(
                    current,
                    "initial",
                    profile=current.source_profile,
                )
                if ai is None:  # Defensive fallback for nonstandard callers.
                    ai = ResumeAI(
                        model=models.analysis_tailoring_model,
                        reasoning_effort=models.analysis_tailoring_reasoning_effort,
                    )
                    _ensure_target_language_profile(current, ai)
                analysis = existing_analysis or ai.analyze_job(
                    current.job_description, current.target_title
                )
                proposal = (
                    existing_evidence.model_copy(deep=True)
                    if existing_evidence is not None
                    else ai.create_proposal(
                        current.source_profile, analysis, _effective_career_background(current)
                    )
                )
                proposal = repair_missing_bullet_proposals(
                    current.source_profile, proposal
                )
                proposal = prioritize_candidate_questions(proposal, analysis)
                proposal = ensure_career_translation_assessment(
                    current.source_profile,
                    analysis,
                    proposal,
                    _effective_career_background(current),
                )
                proposal = _apply_confirmed_title_interpretations(
                    str(getattr(g, "application_owner_id", "") or ""),
                    current.source_profile,
                    proposal,
                )
                proposal, reused_profile, reused_answers, reusable_draft = (
                    _reuse_library_confirmation_answers(
                        str(getattr(g, "application_owner_id", "") or ""),
                        current.source_profile,
                        analysis,
                        proposal,
                    )
                )
                current.analysis = analysis
                current.analysis_input_fingerprint = current_input
                ensure_recommended_resume_style(current)
                current.workflow_stage = "draft"
                current.draft_proposal = None
                current.previous_draft_proposal = None
                current.draft_revision = 0
                current.previous_draft_revision = None
                current.draft_last_change_label = ""
                current.draft_last_changed_at = ""
                current.final_proposal = None
                current.provisional_proposal = proposal.model_copy(deep=True)
                current.analyzed_input_fingerprint = current_input
                current.confirmation_complete = False
                current.candidate_answers = [
                    answer.model_copy(deep=True) for answer in reused_answers
                ]
                current.confirmation_draft = dict(reusable_draft)
                current.confirmed_profile = (
                    reused_profile.model_copy(deep=True)
                    if reused_profile is not None
                    else None
                )
                current.reused_library_evidence_count = len(reused_answers)
                current.initial_evidence_proposal = proposal.model_copy(deep=True)
                current.initial_evidence_input_fingerprint = current_input
                # Step 2 should appear as soon as the tailoring proposal is ready.
                # The Application Baseline Report is generated by an automatic follow-up
                # request after the page loads, so Word rendering does not block navigation.
                current.initial_report = None
                current.initial_report_input_fingerprint = None
                current.initial_report_analysis = None
                current.initial_report_proposal = None
                current.initial_report_created_at = ""
                current.initial_report_error = ""
                tailoring_started = True
                prefilled_count = sum(
                    1 for key in reusable_draft if key.startswith("answer__")
                )
                if reused_answers or prefilled_count:
                    remaining_count = len(proposal.candidate_questions)
                    reuse_summary = (
                        f"{len(reused_answers)} previous answer"
                        f"{'s were' if len(reused_answers) != 1 else ' was'} reused"
                    )
                    if prefilled_count:
                        reuse_summary += (
                            f"; {prefilled_count} related answer"
                            f"{'s were' if prefilled_count != 1 else ' was'} prefilled for missing detail"
                        )
                    flash(
                        f"Job analysis completed. {reuse_summary}; {remaining_count} "
                        f"question{'s remain' if remaining_count != 1 else ' remains'}. "
                        "The Application Baseline Report is generating automatically.",
                        "success",
                    )
                else:
                    flash(
                        "Job analysis and Target-Market Review completed. Confirm the high-value experience questions next; the Application Baseline Report is generating automatically without blocking the workflow.",
                        "success",
                    )
            else:
                flash("Unknown workflow action.", "error")
        except (ResumeAIError, TemplateError, ValueError) as exc:
            flash(str(exc), "error")
        if tailoring_started:
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="confirmation")
                + "#confirmation-stage"
            )
        return redirect(url_for("application_builder.index", tab="tailoring"))


    @application_builder_bp.post("/reports/initial")
    def run_initial_report():
        """Manual recovery action; normal workflow generation is automatic."""
        current = state()
        if not current.job_description.strip():
            flash(
                "Save a job description in Application and Job Setup before retrying the report.",
                "error",
            )
            return redirect(url_for("application_builder.index", tab="reports", report="initial"))
        try:
            models = resolve_models(current)
            ai = ResumeAI(
                model=models.analysis_tailoring_model,
                reasoning_effort=models.analysis_tailoring_reasoning_effort,
            )
            _ensure_target_language_profile(current, ai)
            current_input = input_fingerprint(current, models)
            analysis_is_current = bool(
                current.analysis
                and current.analysis_input_fingerprint == current_input
            )
            evidence_is_current = bool(
                current.initial_evidence_proposal
                and current.initial_evidence_input_fingerprint == current_input
            )
            if analysis_is_current:
                analysis = current.analysis
            else:
                ai = ResumeAI(
                    model=models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
                )
                analysis = ai.analyze_job(
                    current.job_description, current.target_title
                )
                current.analysis = analysis
                current.analysis_input_fingerprint = current_input
                ensure_recommended_resume_style(current)
            evidence_source = (
                current.initial_evidence_proposal
                if evidence_is_current
                else (
                    ai
                    or ResumeAI(
                        model=models.analysis_tailoring_model,
                        reasoning_effort=models.analysis_tailoring_reasoning_effort,
                    )
                ).create_proposal(
                    current.source_profile,
                    analysis,
                    _effective_career_background(current),
                )
            )
            evidence_source = repair_missing_bullet_proposals(
                current.source_profile, evidence_source
            )
            evidence_source = prioritize_candidate_questions(evidence_source, analysis)
            evidence_source = ensure_career_translation_assessment(
                current.source_profile,
                analysis,
                evidence_source,
                _effective_career_background(current),
            )
            evidence_source = _apply_confirmed_title_interpretations(
                str(getattr(g, "application_owner_id", "") or ""),
                current.source_profile,
                evidence_source,
            )
            current.initial_evidence_proposal = evidence_source.model_copy(
                deep=True
            )
            current.initial_evidence_input_fingerprint = current_input
            if _refresh_initial_resume_report(
                current, analysis, evidence_source, force=True
            ):
                flash(f"{APPLICATION_BASELINE_LABEL} Report refreshed.", "success")
            else:
                raise ValueError(
                    current.initial_report_error
                    or f"The {APPLICATION_BASELINE_LABEL} Report could not be refreshed."
                )
        except (ResumeAIError, TemplateError, ValueError) as exc:
            current.initial_report_error = str(exc)
            flash(
                "Automatic report retry failed. The workflow remains available: "
                + str(exc),
                "warning",
            )
        return redirect(
            url_for("application_builder.index", tab="reports", report="initial")
            + "#reports-initial"
        )

    @application_builder_bp.post("/reports/draft")
    def run_draft_report():
        """Manual recovery action; Step 3 normally refreshes this report automatically."""
        current = state()
        draft = current.draft_proposal
        if current.analysis is None or draft is None or not current.confirmation_complete:
            flash(
                "Complete the Job-Aligned Resume before retrying its report.",
                "error",
            )
            return redirect(url_for("application_builder.index", tab="reports", report="draft"))
        try:
            models = resolve_models(current)
            current_input = input_fingerprint(current, models)
            if current.analyzed_input_fingerprint != current_input:
                raise ValueError(
                    "The job description or analysis model changed. Return to Application and Job Setup and select Start tailoring again."
                )
            profile = current.confirmed_profile or current.source_profile
            if _refresh_job_aligned_resume_report(
                current, profile, draft, force=True
            ):
                flash("Job-Aligned Resume Report refreshed.", "success")
            else:
                raise ValueError(
                    current.updated_report_error
                    or "The Job-Aligned Resume Report could not be refreshed."
                )
        except (TemplateError, ValueError) as exc:
            current.updated_report_error = str(exc)
            flash(
                "Automatic report retry failed. The workflow remains available: "
                + str(exc),
                "warning",
            )
        return redirect(
            url_for("application_builder.index", tab="reports", report="draft")
            + "#reports-draft"
        )

    @application_builder_bp.post("/reports/auto/<report_name>")
    def run_automatic_report(report_name: str):
        """Generate a milestone report after the next workflow screen is visible."""
        current = state()
        try:
            if report_name == "initial":
                if current.analysis is None or current.initial_evidence_proposal is None:
                    return jsonify(ok=False, message="Initial report inputs are not ready."), 409
                _refresh_initial_resume_report(
                    current,
                    current.analysis,
                    current.initial_evidence_proposal,
                    force=False,
                )
                report = current.initial_report
                error = current.initial_report_error
                label = f"{APPLICATION_BASELINE_LABEL} Report"
            elif report_name == "draft":
                proposal = current.draft_proposal
                if (
                    current.analysis is None
                    or proposal is None
                    or not current.confirmation_complete
                ):
                    return jsonify(ok=False, message="Job-Aligned report inputs are not ready."), 409
                profile = current.confirmed_profile or current.source_profile
                _refresh_job_aligned_resume_report(
                    current, profile, proposal, force=False
                )
                report = current.updated_report
                error = current.updated_report_error
                label = "Job-Aligned Resume Report"
            elif report_name == "final":
                proposal = current.final_proposal
                if (
                    current.analysis is None
                    or proposal is None
                    or current.final_resume_bytes is None
                ):
                    return jsonify(ok=False, message="Final report inputs are not ready."), 409
                profile = current.confirmed_profile or current.source_profile
                _build_final_report_snapshot(
                    current, profile, proposal, current.final_resume_bytes
                )
                current.optimization_report_after = current.final_report
                report = current.final_report
                error = current.final_report_error
                label = "Final Resume Report"
            else:
                abort(404)

            if report is None:
                return jsonify(ok=False, message=error or f"{label} could not be generated."), 500
            return jsonify(
                ok=True,
                label=label,
                score=round(report.overall_score(), 1),
                message=f"{label} ready.",
            )
        except (TemplateError, ValueError) as exc:
            if report_name == "initial":
                current.initial_report_error = str(exc)
            elif report_name == "draft":
                current.updated_report_error = str(exc)
            else:
                current.final_report_error = str(exc)
                current.final_report_exact = False
            return jsonify(ok=False, message=str(exc)), 500

    @application_builder_bp.post("/reports/final")
    def run_final_report():
        """Retry only the Final Resume Report without rerunning optimization."""
        current = state()
        proposal = current.final_proposal
        if current.analysis is None or proposal is None:
            flash(
                "Complete Improve Resume Quality before retrying the Final Resume Report.",
                "error",
            )
            return redirect(url_for("application_builder.index", tab="reports", report="final"))
        try:
            models = resolve_models(current)
            if current.analyzed_input_fingerprint != input_fingerprint(current, models):
                raise ValueError(
                    "The job description or analysis model changed. Return to Application and Job Setup and select Start tailoring again."
                )
            profile = current.confirmed_profile or current.source_profile
            _build_final_report_snapshot(
                current, profile, proposal, current.final_resume_bytes
            )
            current.optimization_report_after = current.final_report
            flash("Final Resume Report refreshed.", "success")
        except (TemplateError, ValueError) as exc:
            current.final_report_error = str(exc)
            current.final_report_exact = False
            flash(
                "Automatic report retry failed. The Final Resume export remains available: "
                + str(exc),
                "warning",
            )
        return redirect(
            url_for("application_builder.index", tab="reports", report="final")
            + "#reports-final"
        )

    @application_builder_bp.post("/confirmation/apply")
    def apply_confirmation():
        current = state()
        if current.analysis is None or current.provisional_proposal is None:
            flash("Analyze the job and resume before confirming relevant experience.", "error")
            return redirect(url_for("application_builder.index", tab="tailoring", stage="initial"))

        redirect_stage = "draft"
        redirect_anchor = "#tailored-resume"
        try:
            models = resolve_models(current)
            if current.analyzed_input_fingerprint != input_fingerprint(current, models):
                raise ValueError(
                    "The job description changed. Return to Application and Job Setup and select Start tailoring again."
                )

            questions = current.provisional_proposal.candidate_questions
            answers, draft = collect_candidate_answers(questions, request.form)
            # Library persistence is an explicit, separate Step 2 action. Older
            # workflow states may still contain this legacy flag from the removed
            # checkbox/button behavior, so clear it rather than saving implicitly
            # while creating the tailored resume.
            current.save_confirmation_to_library = False
            current.confirmation_draft = draft
            errors = validate_candidate_answers(questions, answers)
            if errors:
                for error in errors:
                    flash(error, "error")
                return redirect(
                    url_for("application_builder.index", tab="tailoring", stage="confirmation")
                    + "#confirmation"
                )

            base_profile = current.confirmed_profile or current.source_profile
            confirmed_profile = (
                build_profile_with_candidate_answers(
                    base_profile,
                    current.analysis,
                    questions,
                    answers,
                )
                if questions
                else base_profile.model_copy(deep=True)
            )
            all_answers = _merge_candidate_answers(current.candidate_answers, answers)

            proposal_for_refinement = current.provisional_proposal.model_copy(deep=True)
            proposal_for_refinement.candidate_questions = []

            # Creating the tailored resume must remain comfortably below the web
            # gateway timeout. The provisional proposal is already grounded in the
            # Application Baseline and job analysis. Candidate answers become
            # first-class verified evidence in ``confirmed_profile`` above, so apply
            # them locally and let deterministic validation/selection build Step 3.
            #
            # Do not call ``refine_proposal`` or the independent evidence auditor
            # inside this interactive request. Those two sequential model calls were
            # the source of the Target-Market Review HTTP 504 errors.
            refined = apply_final_follow_up_answers_locally(
                confirmed_profile,
                proposal_for_refinement,
                questions,
                answers,
            )
            refined = repair_missing_bullet_proposals(confirmed_profile, refined)
            refined.skills = balance_skill_categories(
                confirmed_profile, current.analysis, refined.skills
            )
            refined = ensure_confirmed_answers_visible(confirmed_profile, refined)
            refined.candidate_questions = []
            refined = ensure_career_translation_assessment(
                confirmed_profile,
                current.analysis,
                refined,
                _effective_career_background(current),
            )
            refined = _apply_confirmed_title_interpretations(
                str(getattr(g, "application_owner_id", "") or ""),
                confirmed_profile,
                refined,
            )
            refined, _ = apply_all_until_valid(
                confirmed_profile, current.analysis, refined
            )

            current.confirmed_profile = confirmed_profile
            current.candidate_answers = all_answers
            current.save_confirmed_profile = False
            current.workflow_stage = "draft"
            current.quality_review_started = False
            current.provisional_proposal = refined.model_copy(deep=True)
            current.draft_proposal = None
            current.previous_draft_proposal = None
            current.draft_revision = 0
            current.previous_draft_revision = None
            current.draft_last_change_label = ""
            current.draft_last_changed_at = ""
            current.final_proposal = None
            current.confirmation_draft = {}
            current.clear_draft_report()
            current.clear_final_report()

            # Step 2 now completes in one bounded request. The deterministic
            # selector resolves inclusion and exclusion; the UI does not create a
            # second AI-generated follow-up round before Review Tailored Resume.
            current.provisional_proposal = refined.model_copy(deep=True)
            current.draft_proposal = refined.model_copy(deep=True)
            current.draft_revision = 1
            current.draft_last_change_label = (
                "Tailored resume created from confirmed evidence"
            )
            current.draft_last_changed_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            current.confirmation_complete = True
            current.confirmation_follow_up_count = 0
            capture_workflow_step_snapshot(
                current,
                "confirmation",
                proposal=refined,
                profile=confirmed_profile,
            )
            # Review Tailored Resume opens immediately. Its report is generated
            # automatically after the page becomes interactive.
            current.clear_draft_report()
            flash(
                "Experience confirmation is complete and the Job-Aligned Resume is ready for review. "
                "Its Resume Report is generating automatically without blocking Step 3.",
                "success",
            )
        except (ResumeAIError, ValueError) as exc:
            flash(str(exc), "error")
            redirect_stage = "confirmation"
            redirect_anchor = "#confirmation"

        return redirect(
            url_for("application_builder.index", tab="tailoring", stage=redirect_stage) + redirect_anchor
        )

    @application_builder_bp.post("/confirmation/save-to-library")
    def save_confirmation_to_library():
        """Persist Step 2 answers without creating or changing the tailored resume."""
        current = state()
        submitted_answers = any(
            key.startswith(("choice__", "answer__", "experience__", "placement__"))
            for key in request.form
        )

        answers_to_save = [
            answer.model_copy(deep=True) for answer in current.candidate_answers
        ]
        if submitted_answers:
            if current.analysis is None or current.provisional_proposal is None:
                flash(
                    "Analyze the job and resume before saving confirmation answers.",
                    "error",
                )
                return redirect(
                    url_for(
                        "application_builder.index",
                        tab="tailoring",
                        stage="initial",
                    )
                )

            questions = current.provisional_proposal.candidate_questions
            submitted, draft = collect_candidate_answers(questions, request.form)
            current.confirmation_draft = draft
            errors = validate_candidate_answers(questions, submitted)
            if errors:
                for error in errors:
                    flash(error, "error")
                return redirect(
                    url_for(
                        "application_builder.index",
                        tab="tailoring",
                        stage="confirmation",
                    )
                    + "#confirmation"
                )
            answers_to_save = _merge_candidate_answers(
                current.candidate_answers,
                submitted,
            )
            # Keep the entered answers available on this application, but do not
            # complete Step 2 or generate a proposal. The primary action remains
            # the only path that advances to Review Tailored Resume.
            current.candidate_answers = [
                answer.model_copy(deep=True) for answer in answers_to_save
            ]

        if not answers_to_save:
            flash("There are no confirmation answers to save yet.", "warning")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="confirmation")
                + "#confirmation"
            )

        try:
            saved_count = _save_confirmation_answers_to_library(
                str(getattr(g, "application_owner_id", "") or ""),
                current,
                answers_to_save,
            )
            # Preserve the field for backward-compatible workflow serialization,
            # but never use it to trigger an implicit save during resume creation.
            current.save_confirmation_to_library = False
            current.saved_library_evidence_count = max(
                current.saved_library_evidence_count, saved_count
            )
            flash(
                f"{saved_count} confirmation answer{'s were' if saved_count != 1 else ' was'} saved to Career Evidence Library. You remain in Confirm Relevant Experience.",
                "success",
            )
        except Exception as exc:
            current_app.logger.exception(
                "Could not save confirmation answers to Career Evidence Library"
            )
            flash(
                "The confirmation answers could not be saved to Career Evidence Library: "
                + str(exc),
                "error",
            )
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="confirmation")
            + "#confirmation"
        )


    @application_builder_bp.post("/confirmation/reopen")
    def reopen_confirmation():
        current = state()
        if current.provisional_proposal is not None:
            confirmation_snapshot = current.workflow_step_snapshots.get("confirmation")
            saved_answers = (
                confirmation_snapshot.candidate_answers
                if confirmation_snapshot is not None
                else current.candidate_answers
            )
            restored_draft: dict[str, str] = {}
            for answer in saved_answers:
                if answer.yes_no is True:
                    choice = "yes"
                elif answer.yes_no is False:
                    choice = "no"
                else:
                    choice = "yes" if answer.text.strip() else ""
                restored_draft[f"choice__{answer.question_id}"] = choice
                restored_draft[f"answer__{answer.question_id}"] = answer.text
                restored_draft[f"experience__{answer.question_id}"] = (
                    answer.experience_id
                )
                restored_draft[f"placement__{answer.question_id}"] = answer.placement

            reopened = current.provisional_proposal.model_copy(deep=True)
            current.workflow_stage = "draft"
            current.quality_review_started = False
            current.provisional_proposal = reopened
            current.draft_proposal = None
            current.previous_draft_proposal = None
            current.draft_revision = 0
            current.previous_draft_revision = None
            current.draft_last_change_label = ""
            current.draft_last_changed_at = ""
            current.final_proposal = None
            current.confirmation_complete = False
            current.candidate_answers = []
            current.confirmed_profile = None
            current.confirmation_draft = restored_draft
            current.confirmation_follow_up_round = 0
            current.confirmation_follow_up_count = 0
            discard_workflow_step_snapshots_after(
                current, "confirmation", include_stage=True
            )
            current.clear_draft_report()
            current.clear_final_report()
            flash("Confirmation questions reopened. Confirm the answers again to create a new tailored resume.", "success")
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="confirmation")
            + "#confirmation"
        )

    @application_builder_bp.post("/workflow/reopen/<stage>")
    def reopen_workflow_stage(stage: str):
        """Restore the completed tailored-resume snapshot and invalidate later outputs."""
        internal_stage = "draft" if stage in {"draft", "review"} else stage
        if internal_stage != "draft":
            abort(404)
        current = state()
        snapshot = current.workflow_step_snapshots.get("draft")
        if snapshot is None or snapshot.proposal is None:
            flash("The saved Job-Aligned Resume is no longer available.", "warning")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage=guided_stage_for_state(current))
            )

        restored = snapshot.proposal.model_copy(deep=True)
        current.workflow_stage = "draft"
        current.quality_review_started = False
        current.draft_proposal = restored
        current.final_proposal = None
        current.confirmed_profile = (
            snapshot.profile.model_copy(deep=True)
            if snapshot.profile is not None
            else (current.confirmed_profile or current.source_profile).model_copy(deep=True)
        )
        current.candidate_answers = [
            answer.model_copy(deep=True) for answer in snapshot.candidate_answers
        ]
        current.draft_revision = max(1, snapshot.draft_revision or 1)
        current.previous_draft_proposal = None
        current.previous_draft_revision = None
        current.draft_last_change_label = snapshot.change_label
        current.draft_last_changed_at = snapshot.changed_at
        discard_workflow_step_snapshots_after(current, "draft", include_stage=True)
        current.clear_draft_report()
        current.clear_final_report()
        flash(
            "Review Tailored Resume was reopened. Quality, finalization, and export results were cleared; the Tailored Resume Report will refresh automatically.",
            "warning",
        )
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="review") + "#tailored-resume"
        )



    @application_builder_bp.post("/resume-style")
    def select_resume_style():
        current = state()
        before_preferences = (
            normalize_career_stage(current.resume_career_stage),
            normalize_resume_format(current.resume_format),
            normalize_visual_design(current.resume_visual_design),
        )
        changed_dimensions: list[str] = []
        supplied_dimension = False

        if "career_stage" in request.form:
            supplied_dimension = True
            raw_stage = request.form.get("career_stage", "").strip().casefold()
            allowed = {option["key"] for option in career_stage_options()}
            if raw_stage not in allowed:
                flash("Choose one of the available career stages.", "error")
                return redirect(
                    url_for("application_builder.index", tab="tailoring", stage="final")
                    + "#resume-style-selector"
                )
            if normalize_career_stage(current.resume_career_stage) != raw_stage:
                changed_dimensions.append("career stage")
            current.resume_career_stage = raw_stage
            current.resume_career_stage_explicit = True

        if "resume_format" in request.form:
            supplied_dimension = True
            raw_format = request.form.get("resume_format", "").strip().casefold()
            allowed = {option["key"] for option in resume_format_options()}
            if raw_format not in allowed:
                flash("Choose one of the available resume formats.", "error")
                return redirect(
                    url_for("application_builder.index", tab="tailoring", stage="final")
                    + "#resume-style-selector"
                )
            if normalize_resume_format(current.resume_format) != raw_format:
                changed_dimensions.append("resume format")
            current.resume_format = raw_format
            current.resume_format_explicit = True

        if "visual_design" in request.form:
            supplied_dimension = True
            raw_design = request.form.get("visual_design", "").strip().casefold()
            allowed = {option["key"] for option in visual_design_options()}
            if raw_design not in allowed:
                flash("Choose one of the available visual designs.", "error")
                return redirect(
                    url_for("application_builder.index", tab="tailoring", stage="final")
                    + "#resume-style-selector"
                )
            if normalize_visual_design(current.resume_visual_design) != raw_design:
                changed_dimensions.append("visual design")
            current.resume_visual_design = raw_design
            current.resume_visual_design_explicit = True

        if "resume_style" in request.form and not supplied_dimension:
            supplied_dimension = True
            raw_legacy = request.form.get("resume_style", "").strip().casefold()
            if raw_legacy not in RESUME_STYLE_THEMES:
                flash("Choose one of the available career stages.", "error")
                return redirect(
                    url_for("application_builder.index", tab="tailoring", stage="final")
                    + "#resume-style-selector"
                )
            current.resume_career_stage = normalize_career_stage(raw_legacy)
            current.resume_career_stage_explicit = True

        if not supplied_dimension:
            flash("Choose a career stage, resume format, or visual design.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="final")
                + "#resume-style-selector"
            )

        # Recompute dependent recommendations (for example Technical → Modern)
        # only when the user has not explicitly chosen that dimension.
        ensure_recommended_resume_style(current)
        after_preferences = (
            normalize_career_stage(current.resume_career_stage),
            normalize_resume_format(current.resume_format),
            normalize_visual_design(current.resume_visual_design),
        )
        dimension_names = ("career stage", "resume format", "visual design")
        changed_dimensions = [
            name
            for name, before, after in zip(
                dimension_names, before_preferences, after_preferences
            )
            if before != after
        ]
        preference_label = current_resume_preference_label(current)

        if changed_dimensions and current.analysis is not None:
            profile = current.confirmed_profile or current.source_profile
            try:
                if current.initial_evidence_proposal is not None:
                    _refresh_initial_resume_report(
                        current,
                        current.analysis,
                        current.initial_evidence_proposal,
                        force=True,
                    )
                if current.draft_proposal is not None:
                    _refresh_job_aligned_resume_report(
                        current, profile, current.draft_proposal, force=True
                    )
                if current.final_proposal is not None:
                    _store_optimized_final_export(
                        current, profile, current.final_proposal
                    )
                    current.optimization_report_before = current.updated_report
                    current.optimization_report_after = current.final_report
            except (TemplateError, ValueError) as exc:
                flash(
                    f"{preference_label} was selected, but the resume export could not be refreshed: {exc}",
                    "warning",
                )
            else:
                report_errors = [
                    error
                    for error in (
                        current.initial_report_error,
                        current.updated_report_error,
                        current.final_report_error,
                    )
                    if error
                ]
                changed_label = ", ".join(changed_dimensions)
                if report_errors:
                    flash(
                        f"Updated {changed_label}: {preference_label}. The resume export was refreshed, but one or more Resume Reports need retry: {report_errors[0]}",
                        "warning",
                    )
                else:
                    flash(
                        f"Updated {changed_label}: {preference_label}. The resume export and Resume Reports were refreshed without rerunning optimization.",
                        "success",
                    )
        elif changed_dimensions:
            flash(f"Resume preferences updated: {preference_label}.", "success")
        else:
            flash(f"{preference_label} is already selected.", "info")

        target_stage = (
            "final"
            if current.workflow_stage == "final"
            else guided_stage_for_state(current)
        )
        return redirect(
            url_for("application_builder.index", tab="tailoring", stage=target_stage)
            + ("#resume-style-selector" if target_stage == "final" else "")
        )


    @application_builder_bp.post("/workflow/start-final")
    def start_final_stage():
        """Run one score-guarded quality pass and open Step 4 quickly."""
        route_started_at = perf_counter()
        current = state()
        job_aligned = current.draft_proposal
        working = (
            current.final_proposal
            if current.workflow_stage == "final" and current.final_proposal is not None
            else job_aligned
        )
        if current.analysis is None or job_aligned is None or working is None or not current.confirmation_complete:
            flash("Complete the Job-Aligned Resume before running final optimization.", "error")
            return redirect(url_for("application_builder.index", tab="tailoring", stage="draft") + "#tailored-resume")

        try:
            if current.workflow_stage != "final":
                current.final_resume_title = current.analysis.target_title
            models = resolve_models(current)
            if not os.environ.get("OPENAI_API_KEY", "").strip():
                raise ValueError("Configure an OpenAI API key before optimizing the resume.")
            if current.analyzed_input_fingerprint != input_fingerprint(current, models):
                raise ValueError(
                    "The job description or tailoring model changed. Return to Application and Job Setup and select Start tailoring again."
                )

            profile = current.confirmed_profile or current.source_profile
            ensure_recommended_resume_style(current)
            if "professional_summary" in request.form:
                working = proposal_from_form(working, request.form, profile)
            working = repair_missing_bullet_proposals(profile, working)
            working, _ = apply_all_until_valid(profile, current.analysis, working)
            working.candidate_questions = []

            # Step 3 remains the fixed comparison baseline. Rerunning Step 4 starts
            # from the saved Final Resume so manual edits are preserved.
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            capture_workflow_step_snapshot(
                current,
                "draft",
                proposal=job_aligned,
                profile=profile,
            )
            current.draft_proposal = job_aligned.model_copy(deep=True)

            report_filename = safe_filename(
                f"{profile.name}_{current.analysis.target_title}_Resume"
            ) + ".docx"
            job_aligned_fingerprint = _proposal_fingerprint(job_aligned)
            if (
                current.updated_report is not None
                and current.updated_report_input_fingerprint
                == current.analyzed_input_fingerprint
                and current.updated_report_proposal_fingerprint
                == job_aligned_fingerprint
            ):
                job_aligned_report = current.updated_report
            else:
                # Fast scoring avoids exact document rendering during navigation.
                job_aligned_report = _build_optimization_report(
                    current,
                    profile,
                    job_aligned,
                    report_filename,
                    exact_page_count=False,
                )

            working_fingerprint = _proposal_fingerprint(working)
            if working_fingerprint == job_aligned_fingerprint:
                report_before = job_aligned_report
            elif (
                current.final_report is not None
                and current.final_report_input_fingerprint
                == current.analyzed_input_fingerprint
                and current.final_report_proposal_fingerprint == working_fingerprint
            ):
                report_before = current.final_report
            else:
                report_before = _build_optimization_report(
                    current,
                    profile,
                    working,
                    report_filename,
                    exact_page_count=False,
                )

            # Never optimize from a manually edited version that already scores below
            # the saved Job-Aligned Resume.
            baseline_safe, _ = final_optimization_score_guard(
                job_aligned_report, report_before
            )
            baseline_rolled_back = not baseline_safe
            if baseline_rolled_back:
                working = job_aligned.model_copy(deep=True)
                report_before = job_aligned_report

            # All actionable report findings are sent in one structured request.
            # The previous implementation made one sequential request per three-item
            # batch, which was the largest Step 3 → Step 4 latency source.
            # Keep the interactive request small and predictable. Applying every
            # open recommendation in one structured-output request regularly
            # exceeds reverse-proxy limits for two-page resumes. One focused
            # category batch can finish quickly; remaining recommendations stay
            # visible and can be handled by a later explicit rerun.
            report_issue_batches = final_optimization_actionable_issue_batches(
                report_before
            )
            report_issues = report_issue_batches[0] if report_issue_batches else []
            optimized = working.model_copy(deep=True)
            report_after_scoring = report_before
            accepted_issues: list[AuditIssue] = []
            accepted_batch_count = 0
            rejected_batch_count = 0
            rejected_issue_count = 0
            unchanged_batch_count = 0
            optimization_status = "not_needed" if not report_issues else "pending"
            optimization_notice = ""
            current_validation_count = len(
                validate_proposal(profile, current.analysis, optimized)
            )

            if report_issues:
                optimization_timeout = _final_optimization_ai_timeout_seconds(
                    route_started_at
                )
                if optimization_timeout <= 0:
                    optimization_status = "timed_out"
                    optimization_notice = (
                        "The optional AI refinement did not finish within the "
                        "interactive time limit. No unreviewed changes were applied; "
                        "the approved Job-Aligned Resume remains the Final Resume."
                    )
                else:
                    # One bounded provider attempt prevents the reverse proxy from
                    # returning HTTP 504. A timeout is non-fatal because the reviewed
                    # Job-Aligned Resume is already a valid, score-safe final version.
                    optimizer = ResumeAI(
                        model=models.analysis_tailoring_model,
                        reasoning_effort=models.analysis_tailoring_reasoning_effort,
                        max_attempts=1,
                        request_timeout_seconds=optimization_timeout,
                    )
                    try:
                        candidate = optimizer.apply_suggested_fixes(
                            profile,
                            current.analysis,
                            optimized,
                            report_issues,
                            _effective_career_background(current),
                        )
                    except ResumeAIError as exc:
                        error_detail = str(exc).casefold()
                        if "timed out" in error_detail or "timeout" in error_detail:
                            optimization_status = "timed_out"
                            optimization_notice = (
                                "The optional AI refinement did not finish within the "
                                "interactive time limit. No unreviewed changes were "
                                "applied; the approved Job-Aligned Resume remains the "
                                "Final Resume."
                            )
                        else:
                            optimization_status = "unavailable"
                            optimization_notice = (
                                "The optional AI refinement was temporarily unavailable. "
                                "No unreviewed changes were applied; the approved "
                                "Job-Aligned Resume remains the Final Resume."
                            )
                        current_app.logger.warning(
                            "Optional final resume optimization was skipped: %s", exc
                        )
                    else:
                        candidate = repair_missing_bullet_proposals(profile, candidate)
                        candidate = ensure_career_translation_assessment(
                            profile,
                            current.analysis,
                            candidate,
                            _effective_career_background(current),
                        )
                        candidate = _apply_confirmed_title_interpretations(
                            str(getattr(g, "application_owner_id", "") or ""),
                            profile,
                            candidate,
                        )
                        candidate, _ = apply_all_until_valid(
                            profile, current.analysis, candidate
                        )
                        candidate.candidate_questions = []
                        if _proposal_json(candidate) == _proposal_json(optimized):
                            unchanged_batch_count = 1
                        else:
                            candidate_validation_count = len(
                                validate_proposal(profile, current.analysis, candidate)
                            )
                            if candidate_validation_count > current_validation_count:
                                rejected_batch_count = 1
                                rejected_issue_count = len(report_issues)
                            else:
                                candidate_report = _build_optimization_report(
                                    current,
                                    profile,
                                    candidate,
                                    report_filename,
                                    exact_page_count=False,
                                )
                                score_safe, _ = final_optimization_score_guard(
                                    report_before, candidate_report
                                )
                                if score_safe:
                                    optimized = candidate
                                    report_after_scoring = candidate_report
                                    current_validation_count = candidate_validation_count
                                    accepted_issues = report_issues
                                    accepted_batch_count = 1
                                    optimization_status = "applied"
                                else:
                                    rejected_batch_count = 1
                                    rejected_issue_count = len(report_issues)
                                    optimization_status = "completed"

                        if optimization_status == "pending":
                            optimization_status = "completed"

            optimization_baseline = working.model_copy(deep=True)
            current.quality_review_started = True
            current.workflow_stage = "final"
            current.final_proposal = optimized.model_copy(deep=True)
            current.clear_final_report()

            # Export immediately and retain the fast score-safe report. Exact page
            # rendering is queued automatically after Step 4 becomes interactive.
            _store_optimized_final_export(
                current,
                profile,
                optimized,
                build_exact_report=False,
            )
            _store_fast_final_report_snapshot(
                current,
                profile,
                optimized,
                report_after_scoring,
            )
            report_after = report_after_scoring

            changed_by_optimization = (
                _proposal_json(optimized)
                != _proposal_json(optimization_baseline)
            )
            current.updated_report = job_aligned_report
            current.updated_report_input_fingerprint = (
                current.analyzed_input_fingerprint
            )
            current.updated_report_proposal_fingerprint = (
                job_aligned_fingerprint
            )
            current.updated_report_created_at = now
            current.updated_report_error = ""
            current.optimization_report_before = report_before
            current.optimization_report_after = report_after
            current.optimization_started_at = now
            current.optimization_applied_issue_count = (
                len(accepted_issues) if changed_by_optimization else 0
            )
            current.optimization_accepted_batch_count = accepted_batch_count
            current.optimization_rejected_batch_count = rejected_batch_count
            current.optimization_rejected_issue_count = rejected_issue_count
            current.optimization_unchanged_batch_count = unchanged_batch_count
            current.optimization_baseline_rolled_back = baseline_rolled_back
            current.optimization_status = optimization_status
            current.optimization_notice = optimization_notice

            remaining_recommendations = final_optimization_recommendations(
                report_after
            )
            if baseline_rolled_back:
                flash(
                    "The current Final Resume scored below the saved Job-Aligned Resume, so the weaker working copy was rolled back before optimization.",
                    "warning",
                )
            score_change = (
                f"Overall score {report_before.overall_score():.1f}% → "
                f"{report_after.overall_score():.1f}%."
            )
            background_note = (
                " Exact page-count verification is finishing automatically while you review Step 4."
            )
            if optimization_status in {"timed_out", "unavailable"}:
                flash(
                    "The Final Resume is ready to review and export. The approved "
                    "Job-Aligned Resume was kept unchanged because the optional "
                    "refinement did not finish.",
                    "success",
                )
            elif changed_by_optimization:
                flash(
                    f"Applied {len(accepted_issues)} safe quality improvement(s) in one consolidated pass. "
                    f"Discarded {rejected_issue_count} proposed change(s) that would have reduced resume quality. "
                    f"{score_change} {len(remaining_recommendations)} optional recommendation(s) remain. The Final Resume is ready to download."
                    + background_note,
                    "success",
                )
            else:
                flash(
                    f"The Job-Aligned Resume was already the strongest score-safe version, so no content change was required. "
                    f"{score_change} {len(remaining_recommendations)} optional recommendation(s) remain. The Final Resume is ready to download."
                    + background_note,
                    "success",
                )
        except (ResumeAIError, TemplateError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="draft") + "#tailored-resume"
            )

        return redirect(
            url_for("application_builder.index", tab="tailoring", stage="final") + "#final-review"
        )

    @application_builder_bp.post("/resume/save/<version>")
    def save_resume_version(version: str):
        if version not in {"draft", "final"}:
            abort(404)
        current = state()
        anchor = "#tailored-resume" if version == "draft" else "#final-resume-editor"
        if current.analysis is None or not current.confirmation_complete:
            flash("Complete the tailoring workflow before editing this resume.", "error")
            return redirect(url_for("application_builder.index", tab="tailoring", stage=version) + anchor)
        if version != current.workflow_stage:
            flash(f"The {version.title()} resume is view-only at this stage.", "warning")
            return redirect(url_for("application_builder.index", tab="tailoring", stage=version) + anchor)

        base = current.draft_proposal if version == "draft" else current.final_proposal
        if base is None:
            flash(f"The {version.title()} resume has not been created yet.", "error")
            return redirect(url_for("application_builder.index", tab="tailoring", stage=version) + anchor)

        changed = False
        try:
            models = resolve_models(current)
            if current.analyzed_input_fingerprint != input_fingerprint(current, models):
                raise ValueError(
                    "The job description or tailoring model changed. Return to Application and Job Setup and start tailoring again."
                )
            profile = current.confirmed_profile or current.source_profile
            edited = proposal_from_form(base, request.form, profile)
            proposal_changed = _proposal_json(edited) != _proposal_json(base)
            profile_changed = False
            title_changed = False
            if version == "final":
                edited_profile = profile_with_education_from_form(profile, request.form)
                edited_title = normalize_target_title(
                    request.form.get("target_title", effective_final_resume_title(current))
                )
                if not edited_title:
                    raise ValueError("The final resume job title cannot be blank.")
                profile_changed = (
                    edited_profile.model_dump(mode="json")
                    != profile.model_dump(mode="json")
                )
                title_changed = edited_title != effective_final_resume_title(current)
                current.confirmed_profile = edited_profile
                current.final_resume_title = edited_title
                profile = edited_profile
            changed = proposal_changed or profile_changed or title_changed
            store_working_proposal(
                current,
                edited,
                invalidate=changed,
                previous_proposal=base,
                change_label=f"Manual {version.title()} edits",
            )
            if changed and version == "final":
                _store_optimized_final_export(current, profile, edited)
                current.optimization_report_after = current.final_report

            if not changed:
                flash(f"No changes were made to the {version.title()} resume.", "info")
            elif version == "final":
                flash("Final Resume changes saved and the PDF/Word export source was refreshed.", "success")
                if current.final_report_error:
                    flash(
                        "The Final Resume Report could not be refreshed automatically: "
                        + current.final_report_error,
                        "warning",
                    )
            else:
                flash(
                    "Job-Aligned Resume changes saved. Its report will refresh automatically.",
                    "success",
                )
        except (TemplateError, ValueError) as exc:
            flash(str(exc), "error")

        redirect_args = {"tab": "tailoring", "stage": version}
        if version == "draft" and changed:
            redirect_args["compare"] = "previous"
        return redirect(url_for("application_builder.index", **redirect_args) + anchor)














    @application_builder_bp.get("/download/workflow-snapshot/<stage>")
    def download_workflow_snapshot(stage: str):
        """Download the exact resume stored for a completed workflow step."""
        if stage not in {"draft", "final"}:
            abort(404)
        current = state()
        snapshot = current.workflow_step_snapshots.get(stage)
        if snapshot is None or snapshot.proposal is None or snapshot.profile is None:
            abort(404)
        title = snapshot.target_title or (
            current.analysis.target_title if current.analysis is not None else ""
        )
        try:
            approved = _approved_resume_from_proposal(
                snapshot.profile, title, snapshot.proposal, current.analysis
            )
            document_bytes = export_resume_docx(
                resume_template_path(current.resume_career_stage),
                snapshot.profile,
                approved,
                **resume_export_kwargs(current),
            )
        except (TemplateError, ValueError) as exc:
            abort(409, description=str(exc))

        filename = (
            safe_filename(f"{snapshot.profile.name}_Job_Aligned_Resume") + ".docx"
            if stage == "draft"
            else final_resume_filename(snapshot.profile, title, "docx")
        )
        return send_file(
            BytesIO(document_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


    @application_builder_bp.get("/download/resume-version/<version>")
    def download_resume_version(version: str):
        """Download the Application Baseline, Job-Aligned, or Final resume version."""
        if version not in {"initial", "draft", "final"}:
            abort(404)
        current = state()
        document_bytes = None
        if version == "initial":
            profile = current.source_profile
            proposal = build_initial_resume_proposal(profile, current.initial_evidence_proposal)
            title = initial_resume_title(profile)
            suffix = "Initial_Resume"
        elif version == "draft":
            if current.analysis is None or current.draft_proposal is None:
                abort(404)
            profile = current.confirmed_profile or current.source_profile
            proposal = current.draft_proposal
            title = current.analysis.target_title
            suffix = "Job_Aligned_Resume"
        else:
            if current.analysis is None or current.final_proposal is None:
                abort(404)
            profile = current.confirmed_profile or current.source_profile
            proposal = current.final_proposal
            title = effective_final_resume_title(current)
            suffix = ""
            if (
                current.final_resume_bytes is not None
                and current.final_report_proposal_fingerprint
                == _proposal_fingerprint(proposal)
            ):
                document_bytes = current.final_resume_bytes

        try:
            # Validate the proposal even when a cached DOCX already exists.
            approved = _approved_resume_from_proposal(
                profile, title, proposal, current.analysis
            )
            if document_bytes is None:
                document_bytes = export_resume_docx(
                    resume_template_path(current.resume_career_stage),
                    profile,
                    approved,
                    **resume_export_kwargs(current),
                )
        except (TemplateError, ValueError) as exc:
            abort(409, description=str(exc))

        download_name = (
            final_resume_filename(profile, title, "docx")
            if version == "final"
            else safe_filename(f"{profile.name}_{suffix}") + ".docx"
        )
        return send_file(
            BytesIO(document_bytes),
            as_attachment=True,
            download_name=download_name,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


    @application_builder_bp.get("/download/source-profile")
    def download_source_profile():
        current = state()
        source_profile = current.original_source_profile or current.source_profile
        if not source_profile.all_source_text().strip():
            abort(404)
        payload = json.dumps(source_profile.model_dump(), ensure_ascii=False, indent=2)
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": 'attachment; filename="candidate_profile.json"'},
        )

    @application_builder_bp.get("/download/confirmed-profile")
    def download_confirmed_profile():
        current = state()
        if not current.save_confirmed_profile or current.confirmed_profile is None:
            abort(404)
        payload = json.dumps(current.confirmed_profile.model_dump(), ensure_ascii=False, indent=2)
        return Response(
            payload,
            mimetype="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="candidate_profile_with_confirmed_evidence.json"'
            },
        )

    @application_builder_bp.get("/download/proposal")
    def download_proposal():
        current = state()
        proposal = current.draft_proposal
        if proposal is None:
            abort(404)
        profile = current.confirmed_profile or current.source_profile
        payload = json.dumps(
            {
                "proposal": proposal.model_dump(),
                "candidate_answers": [answer.model_dump() for answer in current.candidate_answers],
                "supplemental_evidence": [
                    item.model_dump() for item in profile.supplemental_evidence
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": 'attachment; filename="tailoring_proposal.json"'},
        )



    def _application_owner_id() -> str:
        return str(getattr(g, "application_owner_id", session.get("workflow_sid", "")))

    def _optional_score(form_key: str) -> float | None:
        raw_value = request.form.get(form_key, "").strip()
        if not raw_value:
            return None
        try:
            return max(0.0, min(100.0, float(raw_value)))
        except ValueError:
            return None

    def _applications_with_calculated_readiness(applications):
        application_list = list(applications)
        try:
            from meeting_assistant.services.interview_readiness_service import (
                InterviewReadinessService,
            )

            assessments = InterviewReadinessService(
                application_store=application_store
            ).build_for_applications(_application_owner_id(), application_list)
        except Exception:
            current_app.logger.exception(
                "Could not calculate automatic interview readiness"
            )
            assessments = {}
        enriched = [
            replace(
                application,
                interview_readiness=(
                    assessments[application.id].score
                    if application.id in assessments
                    else None
                ),
            )
            for application in application_list
        ]
        return enriched, assessments

    def _workflow_state_for_application(application_id: str) -> WorkflowState:
        workflow_key = f"{_application_owner_id()}:application:{application_id}"
        if str(getattr(g, "workflow_key", "") or "") == workflow_key:
            return g.workflow_state
        return store.get(workflow_key)

    def _resume_findings_for_application(application_id: str) -> ResumeFindingsSnapshot:
        application = application_store.get(_application_owner_id(), application_id)
        workflow_state = _workflow_state_for_application(application_id)
        snapshot_state = workflow_state
        if application is not None and (
            normalize_job_description(workflow_state.job_description)
            != normalize_job_description(application.job_description)
            or normalize_target_title(workflow_state.target_title)
            != normalize_target_title(application.role)
        ):
            snapshot_state = WorkflowState(source_profile=workflow_state.source_profile)
            snapshot_state.job_description = application.job_description
            snapshot_state.target_title = application.role
        live_snapshot = build_resume_findings_snapshot(
            snapshot_state,
            company=application.company if application is not None else "",
            role=application.role if application is not None else "",
            job_description=(
                application.job_description if application is not None else workflow_state.job_description
            ),
        )
        if live_snapshot.has_findings():
            return live_snapshot

        stored = application_store.get_resume_findings(
            _application_owner_id(), application_id
        )
        if stored is not None:
            try:
                stored_snapshot = ResumeFindingsSnapshot.model_validate_json(
                    stored.snapshot_json
                )
                if (
                    stored_snapshot.application_context_fingerprint
                    == live_snapshot.application_context_fingerprint
                ):
                    return stored_snapshot
            except Exception:
                pass
        return live_snapshot

    def _persist_resume_findings(
        application_id: str, snapshot: ResumeFindingsSnapshot
    ) -> str:
        fingerprint = resume_findings_fingerprint(snapshot)
        existing = application_store.get_resume_findings(
            _application_owner_id(), application_id
        )
        if existing is not None and existing.fingerprint == fingerprint:
            return fingerprint
        application_store.save_resume_findings(
            _application_owner_id(),
            application_id,
            snapshot_json=snapshot.model_dump_json(),
            fingerprint=fingerprint,
        )
        return fingerprint

    def _selected_interview_application():
        applications = application_store.list_for_owner(_application_owner_id())
        requested_id = (
            str(request.args.get("application_id") or "").strip()
            or str(request.form.get("application_id") or "").strip()
            or str(session.get("active_application_id") or "").strip()
        )
        selected = (
            application_store.get(_application_owner_id(), requested_id)
            if requested_id
            else None
        )
        if selected is None and applications:
            selected = applications[0]
        if selected is not None:
            session["active_application_id"] = selected.id
            g.active_application = selected
        return applications, selected

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
        return render_template(
            "application_builder/career_translation.html",
            active_tab="career_translation",
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

    @application_builder_bp.get("/interview-preparation")
    def interview_preparation_workspace():
        applications, application = _selected_interview_application()
        preparation = None
        preparation_record = None
        evidence = None
        evidence_lookup: dict[str, str] = {}
        preparation_is_stale = False
        preparation_load_error = ""
        resume_findings = None
        resume_findings_fingerprint_value = ""
        reusable_profile = _load_reusable_career_profile(_application_owner_id())

        if application is not None:
            workflow_state = _workflow_state_for_application(application.id)
            evidence = build_verified_evidence_bundle(
                workflow_state, submitted_resume_bytes=application.resume_bytes
            )
            evidence_lookup = {item.id: item.text for item in evidence.items}
            resume_findings = _resume_findings_for_application(application.id)
            resume_findings_fingerprint_value = resume_findings_fingerprint(
                resume_findings
            )
            preparation_record = application_store.get_interview_preparation(
                _application_owner_id(), application.id
            )
            if preparation_record is not None:
                try:
                    saved_evidence = json.loads(
                        preparation_record.evidence_snapshot_json or "{}"
                    )
                    if isinstance(saved_evidence, dict):
                        for evidence_id, evidence_text in saved_evidence.items():
                            evidence_lookup.setdefault(
                                str(evidence_id), str(evidence_text)
                            )
                except (TypeError, json.JSONDecodeError):
                    pass
                try:
                    preparation = InterviewPreparationWorkspace.model_validate_json(
                        preparation_record.content_json
                    )
                except Exception:
                    preparation_load_error = (
                        "The saved interview preparation could not be read. Regenerate it "
                        "from the current job description and verified evidence."
                    )
                preparation_is_stale = bool(
                    preparation_record.job_description_fingerprint
                    != job_description_fingerprint(
                        application.job_description,
                        company=application.company,
                        role=application.role,
                        interview_audience=application.interview_audience,
                        career_profile_fingerprint=reusable_profile.fingerprint,
                    )
                    or preparation_record.evidence_fingerprint != evidence.fingerprint
                    or preparation_record.resume_findings_fingerprint
                    != resume_findings_fingerprint_value
                )

        return render_template(
            "application_builder/interview_preparation.html",
            active_tab="interview_preparation",
            career_section="interview_preparation",
            applications=applications,
            selected_application=application,
            preparation=preparation,
            preparation_record=preparation_record,
            preparation_is_stale=preparation_is_stale,
            preparation_load_error=preparation_load_error,
            evidence=evidence,
            evidence_lookup=evidence_lookup,
            resume_findings=resume_findings,
            reusable_career_profile=reusable_profile.as_prompt_dict(),
        )

    @application_builder_bp.post("/interview-preparation/generate")
    def generate_interview_preparation():
        _, application = _selected_interview_application()
        if application is None:
            flash("Create a job application before generating interview preparation.", "error")
            return redirect(url_for("application_builder.interview_preparation_workspace"))
        if not application.job_description.strip():
            flash(
                "Add the target job description to this application before generating interview preparation.",
                "error",
            )
            return redirect(
                url_for(
                    "application_builder.interview_preparation_workspace",
                    application_id=application.id,
                )
            )

        workflow_state = _workflow_state_for_application(application.id)
        evidence = build_verified_evidence_bundle(
            workflow_state, submitted_resume_bytes=application.resume_bytes
        )
        if not evidence.items:
            flash(
                "Verified candidate evidence is required. Complete Confirm Relevant Experience "
                "or attach the evidence-reviewed Final Resume to this application first.",
                "error",
            )
            return redirect(
                url_for(
                    "application_builder.interview_preparation_workspace",
                    application_id=application.id,
                )
            )

        resume_findings = _resume_findings_for_application(application.id)
        resume_findings_fingerprint_value = _persist_resume_findings(
            application.id, resume_findings
        )
        reusable_profile = _load_reusable_career_profile(_application_owner_id())

        try:
            models = resolve_models(workflow_state)
            ai = ResumeAI(
                models.analysis_tailoring_model,
                reasoning_effort=models.analysis_tailoring_reasoning_effort,
            )
            preparation = ai.create_interview_preparation(
                company=application.company,
                role=application.role,
                interview_audience=application.interview_audience,
                job_description=application.job_description,
                evidence=evidence,
                resume_findings=resume_findings,
                career_profile_context=reusable_profile.as_prompt_dict(),
            )
            preparation = restrict_workspace_to_evidence(
                preparation,
                evidence.ids,
                submitted_resume_ids=evidence.submitted_resume_ids,
                evidence_by_id={item.id: item.text for item in evidence.items},
            )
            application_store.save_interview_preparation(
                _application_owner_id(),
                application.id,
                content_json=preparation.model_dump_json(),
                job_description_fingerprint=job_description_fingerprint(
                    application.job_description,
                    company=application.company,
                    role=application.role,
                    interview_audience=application.interview_audience,
                    career_profile_fingerprint=reusable_profile.fingerprint,
                ),
                evidence_fingerprint=evidence.fingerprint,
                evidence_source_label=evidence.source_label,
                evidence_snapshot_json=json.dumps(
                    {item.id: item.text for item in evidence.items},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                resume_findings_fingerprint=resume_findings_fingerprint_value,
                resume_findings_snapshot_json=resume_findings.model_dump_json(),
                model_name=models.analysis_tailoring_model,
            )
        except (ResumeAIError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash(
                "Interview preparation generated from the job description, verified candidate evidence, and saved resume findings.",
                "success",
            )

        return redirect(
            url_for(
                "application_builder.interview_preparation_workspace",
                application_id=application.id,
            )
            + "#interview-workspace"
        )

    @application_builder_bp.post("/discovery/sources")
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

    @application_builder_bp.post("/discovery/sources/import")
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

    @application_builder_bp.get("/discovery/sources/import-template.csv")
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

    @application_builder_bp.get("/discovery/sources/import-template.json")
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

    @application_builder_bp.post("/discovery/sources/<source_id>/update")
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

    @application_builder_bp.post("/discovery/sources/<source_id>/toggle")
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

    @application_builder_bp.post("/discovery/sources/<source_id>/delete")
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

    @application_builder_bp.post("/discovery/sources/delete-all")
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

    @application_builder_bp.post("/discovery/preferences")
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

    @application_builder_bp.post("/discovery/schedule")
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

    def _run_discovery_source_refresh(
        owner_id: str, sources: list[CompanySource]
    ):
        discovery_service = (
            JobDiscoveryService(store=discovery_store)
            .enable_shared_public_catalog()
        )
        result = discovery_service.discover(
            sources,
            candidate_profile=None,
            analyze_new_jobs=False,
            source_fetch_transform=_interactive_discovery_source,
        )
        discovery_service.hydrate_owner_from_shared_catalog(owner_id, sources)
        for error in result.errors:
            current_app.logger.warning(
                "Job discovery source refresh failed catalog_owner=%s actor=%s source=%s type=%s error=%s",
                SHARED_CATALOG_SOURCE_OWNER_ID,
                owner_id,
                error.source_id,
                error.source_type.value,
                error.message,
            )
        for error in result.analysis_errors:
            current_app.logger.warning(
                "Job discovery analysis failed catalog_owner=%s actor=%s source=%s job=%s error=%s",
                SHARED_CATALOG_SOURCE_OWNER_ID,
                owner_id,
                error.source_id,
                error.job_id,
                error.message,
            )
        return result

    def _discovery_source_refresh_payload(
        source: CompanySource, result
    ) -> dict[str, Any]:
        issues = [error.message for error in result.errors]
        issues.extend(error.message for error in result.analysis_errors)
        if result.shared_catalog_hits:
            outcome = "reused"
            message = f"Reused recently collected {source.company_name} jobs."
        elif result.shared_catalog_refreshes:
            outcome = "refreshed"
            message = f"Refreshed {source.company_name} for the shared catalog."
        elif result.shared_refreshes_in_progress:
            outcome = "in_progress"
            message = (
                f"A {source.company_name} refresh was already running; "
                "cached public jobs were used."
            )
        elif issues:
            normalized_issues = [issue.casefold() for issue in issues]
            robots_issue = any(
                "robots.txt disallows" in issue for issue in normalized_issues
            )
            transient_index_issue = any(
                "indexed fallback was unavailable" in issue
                and any(
                    token in issue
                    for token in (
                        "timeout",
                        "timed out",
                        "502",
                        "503",
                        "504",
                        "temporarily unavailable",
                        "connection",
                    )
                )
                for issue in normalized_issues
            )
            if robots_issue and transient_index_issue:
                outcome = "error"
                message = (
                    f"{source.company_name}'s direct listing is blocked, and the "
                    "compliant fallback temporarily failed. Retry the scan."
                )
            elif robots_issue:
                outcome = "permission_required"
                message = (
                    f"{source.company_name} requires an authorized feed or crawler "
                    "permission before automated discovery can run."
                )
            else:
                outcome = "error"
                message = f"{source.company_name} could not be refreshed."
        else:
            outcome = "completed"
            message = f"Checked {source.company_name}."
        return {
            "ok": not issues,
            "source_id": source.id,
            "company_name": source.company_name,
            "outcome": outcome,
            "message": message,
            "jobs_available": len(result.jobs),
            "posting_age_filtered": len(result.age_filtered_jobs),
            "issues": issues,
        }

    def _pending_discovery_assessment_jobs(
        owner_id: str,
        profile: CandidateJobProfile,
        *,
        skip_job_keys: set[str] | None = None,
    ) -> list[DiscoveredJob]:
        """Return visible owner jobs that still need a profile-specific fit snapshot."""

        skipped = skip_job_keys or set()
        enabled_source_ids = {
            source.id
            for source in discovery_store.list_company_sources(
                SHARED_CATALOG_SOURCE_OWNER_ID,
                enabled_only=True,
            )
        }
        preferences = discovery_store.get_search_preferences(owner_id)
        maximum_posting_age_days = (
            preferences.maximum_posting_age_days
            if preferences is not None
            else DEFAULT_MAX_POSTING_AGE_DAYS
        )
        fit_snapshots = discovery_store.list_fit_snapshots(owner_id)
        fits = {
            (
                item.job_id,
                item.profile_fingerprint,
                item.description_fingerprint,
            )
            for item in fit_snapshots
        }
        legacy_fits = {
            (item.job_id, item.profile_fingerprint)
            for item in fit_snapshots
            if not item.description_fingerprint
        }
        states = {
            (item.source_id, item.job_id): item
            for item in discovery_store.list_job_states(owner_id)
        }
        pending: list[DiscoveredJob] = []
        for job in discovery_store.list_discovered_jobs(owner_id, active_only=True):
            job_key = f"{job.source_id}:{job.id}"
            if job_key in skipped:
                continue
            if enabled_source_ids and job.source_id not in enabled_source_ids:
                continue
            if (
                (
                    job.id,
                    profile.fingerprint,
                    job.description_fingerprint,
                )
                in fits
                or (job.id, profile.fingerprint) in legacy_fits
            ):
                continue
            state_record = states.get((job.source_id, job.id))
            if state_record is not None and state_record.disposition in {
                DiscoveryJobDisposition.SAVED,
                DiscoveryJobDisposition.IGNORED,
                DiscoveryJobDisposition.APPLICATION_CREATED,
            }:
                continue
            if not evaluate_posting_age(
                job,
                maximum_age_days=maximum_posting_age_days,
            ).eligible:
                continue
            if not evaluate_stage_one(job, profile).passed:
                continue
            pending.append(job)

        pending.sort(
            key=lambda item: (
                item.posted_at or item.first_seen_at,
                item.company.casefold(),
                item.title.casefold(),
                item.id,
            ),
            reverse=True,
        )
        return pending

    def _assessment_request_payload() -> tuple[dict[str, Any], bool]:
        wants_json = request.is_json or "application/json" in str(
            request.headers.get("Accept") or ""
        )
        source = request.get_json(silent=True) if request.is_json else request.form
        return dict(source or {}), wants_json

    @application_builder_bp.post("/discovery/assess/pending")
    def assess_pending_discovered_jobs():
        """Assess already-collected jobs for the signed-in user in bounded batches."""

        owner_id = _application_owner_id()
        payload, wants_json = _assessment_request_payload()
        raw_skipped = payload.get("skip_job_keys") or []
        if isinstance(raw_skipped, str):
            raw_skipped = [raw_skipped]
        skip_job_keys = {
            str(value).strip()
            for value in list(raw_skipped)[:2000]
            if str(value).strip()
        }
        batch_size = _discovery_assessment_batch_size(payload.get("batch_size"))
        profile = _discovery_candidate_profile(state(), owner_id=owner_id)

        if not (
            profile.target_titles
            or profile.verified_skills
            or profile.evidence_statements
            or profile.evidence_references
        ):
            message = (
                "Complete your Career Profile before assessing jobs so the ranking "
                "has a target role and verified evidence to use."
            )
            if wants_json:
                return jsonify({"ok": False, "message": message}), 409
            flash(message, "warning")
            return redirect(_discovery_results_url(result_tab="pending"))

        all_pending = _pending_discovery_assessment_jobs(owner_id, profile)
        available = [
            job
            for job in all_pending
            if f"{job.source_id}:{job.id}" not in skip_job_keys
        ]
        selected = available[:batch_size]
        if not selected:
            unresolved_count = len(all_pending)
            result_payload = {
                "ok": unresolved_count == 0,
                "complete": True,
                "attempted_count": 0,
                "assessed_count": 0,
                "pending_before": unresolved_count,
                "remaining_count": unresolved_count,
                "unresolved_count": unresolved_count,
                "failed_job_keys": [],
                "issues": [],
                "message": (
                    "All eligible pending jobs have been assessed."
                    if unresolved_count == 0
                    else "No additional jobs can be assessed in this run because the remaining jobs previously failed."
                ),
            }
            if wants_json:
                return jsonify(result_payload)
            flash(
                result_payload["message"],
                "success" if unresolved_count == 0 else "warning",
            )
            return redirect(_discovery_results_url())

        try:
            result = JobDiscoveryService(store=discovery_store).assess_existing_jobs(
                selected,
                profile,
            )
        except Exception as exc:
            current_app.logger.exception(
                "Pending job assessment batch failed owner=%s jobs=%s",
                owner_id,
                len(selected),
            )
            message = f"The pending-job assessment batch failed: {exc}"
            if wants_json:
                return jsonify({"ok": False, "message": message}), 500
            flash(message, "error")
            return redirect(_discovery_results_url(result_tab="pending"))

        selected_by_id = {job.id: job for job in selected}
        failed_job_keys: list[str] = []
        issues: list[str] = []
        for error in result.analysis_errors:
            job = selected_by_id.get(error.job_id)
            source_id = job.source_id if job is not None else error.source_id
            failed_job_keys.append(f"{source_id}:{error.job_id}")
            label = (
                f"{job.company} · {job.title}" if job is not None else error.job_id
            )
            issues.append(f"{label}: {error.message}")
            current_app.logger.warning(
                "Pending job assessment failed owner=%s source=%s job=%s error=%s",
                owner_id,
                source_id,
                error.job_id,
                error.message,
            )

        # Avoid a second full DynamoDB-backed pending-queue scan in the same
        # request. The initial queue is authoritative for this bounded batch:
        # successful jobs leave it, failed/skipped jobs remain unresolved, and
        # unselected jobs remain actionable for the browser's next request.
        assessed_count = len(result.ranked_jobs)
        remaining_count = max(0, len(all_pending) - assessed_count)
        actionable_remaining_count = max(0, len(available) - len(selected))
        unresolved_count = max(0, remaining_count - actionable_remaining_count)
        complete = actionable_remaining_count == 0
        message = (
            f"Assessed {assessed_count} job{'s' if assessed_count != 1 else ''}."
        )
        if actionable_remaining_count:
            message += f" {actionable_remaining_count} remain in this run."
        elif unresolved_count:
            message += f" {unresolved_count} could not be assessed and can be retried later."
        else:
            message += " The assessment queue is complete."
        response_payload = {
            "ok": not issues,
            "complete": complete,
            "attempted_count": len(selected),
            "assessed_count": assessed_count,
            "pending_before": len(all_pending),
            "remaining_count": remaining_count,
            "actionable_remaining_count": actionable_remaining_count,
            "unresolved_count": unresolved_count,
            "failed_job_keys": failed_job_keys,
            "issues": issues,
            "message": message,
        }
        if wants_json:
            return jsonify(response_payload)
        if assessed_count:
            _try_prebuild_discovery_result_index(
                owner_id,
                current=state(),
                filters=_discovery_result_filters(request.form),
            )
        flash(message, "warning" if issues else "success")
        return redirect(_discovery_results_url())

    @application_builder_bp.post("/discovery/result-index/prebuild")
    def prebuild_discovery_result_index():
        """Materialize the selected result view outside the initial page GET."""

        owner_id = _application_owner_id()
        payload = request.get_json(silent=True) if request.is_json else request.form
        try:
            summary = _prebuild_discovery_result_index(
                owner_id,
                filters=_discovery_result_filters(payload or {}),
            )
        except Exception as exc:
            current_app.logger.exception(
                "Job Discovery result-index prebuild failed owner=%s", owner_id
            )
            return jsonify(
                {
                    "ok": False,
                    "changed": False,
                    "message": str(exc),
                }
            ), 500
        return jsonify(
            {
                "ok": True,
                "changed": True,
                "recommended_count": summary["recommended_count"],
                "possible_count": summary["possible_count"],
                "pending_count": summary["pending_count"],
                "low_match_count": summary["low_match_count"],
                "saved_count": summary["saved_count"],
                "ignored_count": summary["ignored_count"],
            }
        )

    @application_builder_bp.post("/discovery/catalog/hydrate")
    def hydrate_discovered_jobs_from_shared_catalog():
        """Synchronize shared postings after the results page has rendered.

        Keeping this work in a separate request lets ``GET /job-discovery``
        return its existing durable read model without waiting for catalog
        queries or owner-scoped job writes.
        """

        owner_id = _application_owner_id()
        catalog_sources = discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID
        )
        if not catalog_sources:
            return jsonify(
                {
                    "ok": True,
                    "changed": False,
                    "hydrated_job_count": 0,
                }
            )

        revision_before = discovery_store.get_result_revision(owner_id)
        try:
            hydrated_job_count = (
                JobDiscoveryService(store=discovery_store)
                .enable_shared_public_catalog()
                .hydrate_owner_from_shared_catalog(owner_id, catalog_sources)
            )
        except Exception as exc:
            current_app.logger.exception(
                "Deferred shared catalog hydration failed owner=%s",
                owner_id,
            )
            return jsonify(
                {
                    "ok": False,
                    "changed": False,
                    "hydrated_job_count": 0,
                    "message": str(exc),
                }
            ), 500

        revision_after = discovery_store.get_result_revision(owner_id)
        changed = revision_after != revision_before
        if changed:
            # This request is already outside the initial page render. Build the
            # default read model now so the subsequent reload remains index-only.
            result_index_prebuilt = _try_prebuild_discovery_result_index(owner_id)
        else:
            result_index_prebuilt = True
        return jsonify(
            {
                "ok": True,
                "changed": changed,
                "hydrated_job_count": hydrated_job_count,
                "result_index_prebuilt": result_index_prebuilt,
            }
        )

    @application_builder_bp.post("/discovery/refresh/source")
    def refresh_discovered_job_source():
        _require_job_catalog_manager()
        owner_id = _application_owner_id()
        payload = request.get_json(silent=True) if request.is_json else request.form
        source_id = str((payload or {}).get("source_id") or "").strip()
        source = discovery_store.get_company_source(
            SHARED_CATALOG_SOURCE_OWNER_ID, source_id
        )
        if source is None or not source.enabled:
            return jsonify(
                {
                    "ok": False,
                    "message": "The selected company source is unavailable or disabled.",
                    "source_id": source_id,
                }
            ), 404
        try:
            result = _run_discovery_source_refresh(owner_id, [source])
        except Exception as exc:
            current_app.logger.exception(
                "Interactive company refresh failed actor=%s source=%s",
                owner_id,
                source_id,
            )
            return jsonify(
                {
                    "ok": False,
                    "source_id": source.id,
                    "company_name": source.company_name,
                    "outcome": "error",
                    "message": f"{source.company_name} could not be refreshed.",
                    "issues": [str(exc)],
                }
            ), 500
        return jsonify(_discovery_source_refresh_payload(source, result))

    @application_builder_bp.post("/discovery/refresh")
    def refresh_discovered_jobs():
        """No-JavaScript fallback that refreshes one source per request.

        The normal browser flow calls ``refresh_discovered_job_source`` once for
        each source and displays progress. Keeping this route bounded prevents a
        bulk form submission from exceeding the gateway timeout.
        """

        _require_job_catalog_manager()
        owner_id = _application_owner_id()
        return_to_settings = str(request.form.get("return_to") or "").strip() == "settings"
        redirect_url = (
            url_for("application_builder.job_discovery_workspace", view="settings")
            + "#job-discovery-settings"
            if return_to_settings
            else _discovery_results_url()
        )
        sources = discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID, enabled_only=True
        )
        if not sources:
            flash(
                "No enabled job sources are configured. Add a company source before refreshing jobs.",
                "warning",
            )
            return redirect(redirect_url)

        requested_source_id = str(request.form.get("source_id") or "").strip()
        selected_source = next(
            (source for source in sources if source.id == requested_source_id),
            None,
        )
        if requested_source_id and selected_source is None:
            flash(
                "The selected company source is unavailable or disabled. Enable it before scanning.",
                "warning",
            )
            return redirect(redirect_url)
        if selected_source is None:
            selected_source = min(
                sources,
                key=lambda source: (source.last_checked_at or "", source.company_name.casefold()),
            )

        result = _run_discovery_source_refresh(owner_id, [selected_source])
        _try_prebuild_discovery_result_index(
            owner_id,
            current=state(),
            filters=_discovery_result_filters(request.form),
        )
        payload = _discovery_source_refresh_payload(selected_source, result)
        message = payload["message"]
        if len(sources) > 1 and not requested_source_id:
            message += (
                " This fallback refresh processes one company at a time; "
                "click Refresh jobs again for the next company."
            )
        if payload["issues"]:
            message += f" {len(payload['issues'])} issue{'s' if len(payload['issues']) != 1 else ''} need review."
        flash(message, "warning" if payload["issues"] else "success")
        return redirect(redirect_url)

    @application_builder_bp.post(
        "/discovery/jobs/<source_id>/<job_id>/save"
    )
    def save_discovered_job(source_id: str, job_id: str):
        owner_id = _application_owner_id()
        try:
            _job_action_service().save(owner_id, source_id, job_id)
        except LookupError:
            abort(404)
        _try_prebuild_discovery_result_index(
            owner_id,
            current=state(),
            filters=_discovery_result_filters(request.form),
        )
        flash("Job saved for later review.", "success")
        return redirect(_discovery_results_url(anchor=f"discovered-job-{job_id}"))

    @application_builder_bp.post(
        "/discovery/jobs/<source_id>/<job_id>/ignore"
    )
    def ignore_discovered_job(source_id: str, job_id: str):
        owner_id = _application_owner_id()
        try:
            _job_action_service().ignore(owner_id, source_id, job_id)
        except LookupError:
            abort(404)
        _try_prebuild_discovery_result_index(
            owner_id,
            current=state(),
            filters=_discovery_result_filters(request.form),
        )
        flash("Job ignored. You can save it later to restore it.", "success")
        return redirect(_discovery_results_url(anchor=f"discovered-job-{job_id}"))

    @application_builder_bp.post(
        "/discovery/jobs/<source_id>/<job_id>/create-application"
    )
    def create_application_from_discovered_job(source_id: str, job_id: str):
        try:
            result = _job_action_service().create_application_workspace(
                _application_owner_id(), source_id, job_id
            )
        except LookupError:
            abort(404)
        session["active_application_id"] = result.application.id
        if result.previous_job_description:
            previous_fingerprint = hashlib.sha256(
                normalize_job_description(
                    result.previous_job_description
                ).encode("utf-8")
            ).hexdigest()
            session["pending_application_job_description_refresh"] = {
                "application_id": result.application.id,
                "previous_fingerprint": previous_fingerprint,
            }
        if result.description_refreshed:
            flash(
                "The full job description was loaded from the employer posting "
                "and added to Application and Job Setup.",
                "success",
            )
        else:
            flash(
                "Application workspace created from the discovered posting."
                if result.created
                else "This discovered posting already has an application workspace.",
                "success" if result.created else "info",
            )
        if result.description_fetch_error:
            current_app.logger.info(
                "Posting detail lookup kept stored summary owner=%s source=%s "
                "job=%s error=%s",
                _application_owner_id(),
                source_id,
                job_id,
                result.description_fetch_error,
            )
            flash(
                "The employer site did not allow the complete description to be "
                "retrieved, so the available posting details were kept.",
                "warning",
            )
        return redirect(
            url_for(
                "application_builder.open_application_builder",
                application_id=result.application.id,
            )
        )

    @application_builder_bp.get("/applications/<application_id>/builder")
    def open_application_builder(application_id: str):
        application = application_store.get(_application_owner_id(), application_id)
        if application is None:
            abort(404)
        session["active_application_id"] = application.id
        current = state()
        if not current.target_title:
            current.target_title = application.role
        if not current.job_description and application.job_description:
            current.job_description = application.job_description
        return redirect(
            url_for(
                "application_builder.index",
                tab="tailoring",
                stage=application.workflow_step or "setup",
                application_id=application.id,
            )
        )

    @application_builder_bp.post("/applications/<application_id>/activate")
    def activate_application_builder(application_id: str):
        application = application_store.get(_application_owner_id(), application_id)
        if application is None:
            abort(404)
        session["active_application_id"] = application.id
        return redirect(
            url_for(
                "application_builder.open_application_builder",
                application_id=application.id,
            )
        )

    @application_builder_bp.post("/applications/from-final")
    def save_final_as_application():
        current = state()
        if current.final_resume_bytes is None or current.final_proposal is None:
            flash("Create the Final Resume before saving an application.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="finalize")
                + "#finalize-resume"
            )

        analysis = current.analysis
        if analysis is None:
            flash("Run job analysis before saving an application.", "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="finalize")
                + "#finalize-resume"
            )
        profile = current.confirmed_profile or current.source_profile
        try:
            _approved_resume_from_proposal(
                profile,
                effective_final_resume_title(current),
                current.final_proposal,
                analysis,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(
                url_for("application_builder.index", tab="tailoring", stage="finalize")
                + "#finalize-resume"
            )
        company = (analysis.target_company if analysis is not None else "").strip()
        role = effective_final_resume_title(current).strip()
        report = current.final_report or current.optimization_report_after or current.updated_report
        fit_assessment = current_application_fit(
            current, application_store.list_for_owner(_application_owner_id())
        )
        resume_fingerprint = hashlib.sha256(current.final_resume_bytes).hexdigest()
        active = getattr(g, "active_application", None)
        alignment_score = (
            fit_assessment.score
            if fit_assessment is not None
            else report.job_match_score()
            if report is not None
            else None
        )
        overall_score = report.overall_score() if report is not None else None
        resume_filename = (
            current.final_report_filename
            or current_final_resume_filename(current, "docx")
        )

        if active is not None:
            saved = application_store.attach_resume_snapshot(
                _application_owner_id(),
                active.id,
                resume_version=FINAL_RESUME_LABEL,
                resume_style=normalize_resume_style(current.resume_style),
                alignment_score=alignment_score,
                overall_score=overall_score,
                resume_filename=resume_filename,
                resume_bytes=current.final_resume_bytes,
                resume_fingerprint=resume_fingerprint,
                resume_pdf_filename=current_final_resume_filename(current, "pdf"),
                resume_pdf_bytes=current.final_resume_pdf_bytes,
            )
            if saved is None:
                abort(404)
            application_store.save_impact_snapshot(
                _application_owner_id(),
                saved.id,
                build_workflow_impact_snapshot(current),
            )
            _persist_resume_findings(
                saved.id,
                build_resume_findings_snapshot(
                    current,
                    company=saved.company,
                    role=saved.role,
                    job_description=saved.job_description,
                ),
            )
            flash("Final Resume saved to this job application.", "success")
            return redirect(
                url_for("application_builder.index", tab="applications")
                + f"#application-{saved.id}"
            )

        existing = application_store.find_snapshot(
            _application_owner_id(),
            resume_fingerprint=resume_fingerprint,
            company=company or "Company not specified",
            role=role or "Role not specified",
        )
        if existing is not None:
            flash("This Final Resume is already attached to an application.", "info")
            return redirect(
                url_for("application_builder.index", tab="applications")
                + f"#application-{existing.id}"
            )

        created = application_store.create(
            _application_owner_id(),
            company=company or "Company not specified",
            role=role or "Role not specified",
            application_date="",
            status="ready_to_apply",
            resume_version=FINAL_RESUME_LABEL,
            resume_style=normalize_resume_style(current.resume_style),
            alignment_score=alignment_score,
            overall_score=overall_score,
            notes="Created from the completed Application Builder workflow.",
            next_action="",
            workflow_step="evidence_export",
            job_description=current.job_description,
            resume_filename=resume_filename,
            resume_bytes=current.final_resume_bytes,
            resume_fingerprint=resume_fingerprint,
            resume_pdf_filename=current_final_resume_filename(current, "pdf"),
            resume_pdf_bytes=current.final_resume_pdf_bytes,
        )
        application_store.save_impact_snapshot(
            _application_owner_id(),
            created.id,
            build_workflow_impact_snapshot(current),
        )
        _persist_resume_findings(
            created.id,
            build_resume_findings_snapshot(
                current,
                company=created.company,
                role=created.role,
                job_description=created.job_description,
            ),
        )
        session["active_application_id"] = created.id
        flash("Application created and Final Resume attached.", "success")
        return redirect(
            url_for("application_builder.index", tab="applications") + f"#application-{created.id}"
        )

    @application_builder_bp.post("/applications/create")
    def create_application_record():
        company = request.form.get("company", "").strip()
        role = request.form.get("role", "").strip()
        if not company or not role:
            flash("Company and job title are required.", "error")
            return redirect(url_for("application_builder.index", tab="applications") + "#new-application")

        raw_job_url = request.form.get("job_url", "").strip()
        job_url = normalize_job_url(raw_job_url)
        job_description = request.form.get("job_description", "").strip()
        if raw_job_url and not job_url:
            flash("Enter a valid HTTP or HTTPS job posting link.", "error")
            return redirect(url_for("application_builder.index", tab="applications") + "#new-application")
        if not job_url and not job_description:
            flash("Add a job posting link or paste the job description.", "error")
            return redirect(url_for("application_builder.index", tab="applications") + "#new-application")

        created = application_store.create(
            _application_owner_id(),
            company=company,
            role=role,
            job_url=job_url,
            interview_audience="",
            application_date="",
            status="draft",
            resume_version="Not started",
            resume_style="",
            alignment_score=None,
            notes="",
            next_action="",
            next_follow_up_date="",
            upcoming_event_date="",
            upcoming_event_type="",
            job_description=job_description,
            workflow_step="setup",
        )
        session["active_application_id"] = created.id
        flash("Job application created. Continue with Application and Job Setup.", "success")
        if request.form.get("start_builder") == "1":
            return redirect(
                url_for("application_builder.open_application_builder", application_id=created.id)
            )
        return redirect(
            url_for("application_builder.index", tab="applications") + f"#application-{created.id}"
        )

    @application_builder_bp.post("/applications/<application_id>/update")
    def update_application_record(application_id: str):
        updated = application_store.update(
            _application_owner_id(),
            application_id,
            company=request.form.get("company", ""),
            role=request.form.get("role", ""),
            job_url=request.form.get("job_url", ""),
            application_date=request.form.get("application_date", ""),
            status=request.form.get("status", "draft"),
            screening_received=request.form.get("screening_received") == "on",
            interview_received=request.form.get("interview_received") == "on",
            offer_received=request.form.get("offer_received") == "on",
            notes=request.form.get("notes", ""),
            next_follow_up_date=request.form.get("next_follow_up_date", ""),
            next_action=request.form.get("next_action", ""),
            upcoming_event_date=request.form.get("upcoming_event_date", ""),
            upcoming_event_type=request.form.get("upcoming_event_type", ""),
            job_description=request.form.get("job_description"),
            interview_audience=request.form.get("interview_audience", ""),
        )
        if updated is None:
            abort(404)
        flash("Application updated.", "success")
        return redirect(
            url_for("application_builder.index", tab="applications") + f"#application-{updated.id}"
        )

    @application_builder_bp.post("/applications/<application_id>/delete")
    def delete_application_record(application_id: str):
        owner_id = _application_owner_id()
        workflow_key = f"{owner_id}:application:{application_id}"
        workflow_state = store.peek(workflow_key)
        application = application_store.get(
            owner_id,
            application_id,
            include_resume_bytes=False,
        )
        if application is None or not application_store.delete(
            owner_id, application_id
        ):
            abort(404)
        if application.source_job_id:
            try:
                for discovery_state in discovery_store.list_job_states(owner_id):
                    if (
                        discovery_state.job_id == application.source_job_id
                        and discovery_state.disposition
                        is DiscoveryJobDisposition.APPLICATION_CREATED
                        and discovery_state.application_id == application_id
                    ):
                        discovery_store.put_job_state(
                            replace(
                                discovery_state,
                                disposition=DiscoveryJobDisposition.SAVED,
                                application_id="",
                                updated_at=utc_now_iso(),
                            )
                        )
            except Exception:
                # The application has already been deleted. Do not turn a
                # best-effort discovery-link repair into a failed deletion;
                # the Job Discovery read path also tolerates stale links.
                current_app.logger.warning(
                    "Could not repair Job Discovery state after deleting "
                    "application owner=%s application=%s source_job=%s",
                    owner_id,
                    application_id,
                    application.source_job_id,
                    exc_info=True,
                )
        if workflow_state is not None:
            _delete_workflow_document_objects(
                workflow_state,
                include_source=(
                    configured_application_backend(current_app.config) != "dynamodb"
                ),
            )
        store.delete(workflow_key)
        if str(getattr(g, "workflow_key", "")) == workflow_key:
            g.workflow_state_deleted = True
        if session.get("active_application_id") == application_id:
            session.pop("active_application_id", None)
        flash("Application removed.", "success")
        return redirect(url_for("application_builder.index", tab="applications"))

    @application_builder_bp.get("/applications/<application_id>/resume")
    def download_application_resume(application_id: str):
        application = application_store.get(_application_owner_id(), application_id)
        if application is None or not application.resume_bytes:
            abort(404)
        return send_file(
            BytesIO(application.resume_bytes),
            as_attachment=True,
            download_name=application.resume_filename or "Submitted_Resume.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


    @application_builder_bp.get("/download/final-resume")
    def download_final_resume():
        """Download the final resume as PDF, the recommended default format."""
        current = state()
        if current.final_resume_bytes is None:
            abort(404)
        if current.analysis is None or current.final_proposal is None:
            abort(404)
        profile = (
            current.final_report_profile
            or current.confirmed_profile
            or current.source_profile
        )
        try:
            _approved_resume_from_proposal(
                profile,
                effective_final_resume_title(current),
                current.final_proposal,
                current.analysis,
            )
        except ValueError as exc:
            abort(409, description=str(exc))
        if current.final_resume_pdf_bytes is None:
            try:
                approved = _approved_resume_from_proposal(
                    profile,
                    effective_final_resume_title(current),
                    current.final_proposal,
                    current.analysis,
                )
                current.final_resume_pdf_bytes = export_resume_pdf(
                    profile,
                    approved,
                    **resume_export_kwargs(current),
                )
                current.final_resume_pdf_error = ""
                active_application = getattr(g, "active_application", None)
                if active_application is not None and current.final_resume_bytes is not None:
                    application_store.attach_resume_snapshot(
                        _application_owner_id(),
                        active_application.id,
                        resume_version=active_application.resume_version,
                        resume_style=active_application.resume_style,
                        alignment_score=active_application.alignment_score,
                        overall_score=active_application.overall_score,
                        resume_filename=(
                            active_application.resume_filename
                            or current_final_resume_filename(current, "docx")
                        ),
                        resume_bytes=current.final_resume_bytes,
                        resume_fingerprint=(
                            active_application.resume_fingerprint
                            or hashlib.sha256(current.final_resume_bytes).hexdigest()
                        ),
                        resume_pdf_filename=current_final_resume_filename(current, "pdf"),
                        resume_pdf_bytes=current.final_resume_pdf_bytes,
                    )
            except (PdfConversionError, ValueError) as exc:
                current.final_resume_pdf_error = str(exc)
                flash(
                    "The PDF could not be generated. The Word download remains available while "
                    "you retry or review the final resume.",
                    "error",
                )
                return redirect(
                    url_for("application_builder.index", tab="tailoring", stage="final")
                    + "#final-resume-actions"
                )
        return send_file(
            BytesIO(current.final_resume_pdf_bytes),
            as_attachment=True,
            download_name=current_final_resume_filename(current, "pdf"),
            mimetype="application/pdf",
        )

    @application_builder_bp.get("/download/final-resume-word")
    def download_final_resume_word():
        """Download the editable Word alternative when an employer requests DOCX."""
        current = state()
        if current.final_resume_bytes is None:
            abort(404)
        if current.analysis is None or current.final_proposal is None:
            abort(404)
        profile = (
            current.final_report_profile
            or current.confirmed_profile
            or current.source_profile
        )
        try:
            _approved_resume_from_proposal(
                profile,
                effective_final_resume_title(current),
                current.final_proposal,
                current.analysis,
            )
        except ValueError as exc:
            abort(409, description=str(exc))
        return send_file(
            BytesIO(current.final_resume_bytes),
            as_attachment=True,
            download_name=current_final_resume_filename(current, "docx"),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )



    return None


_register_application_builder_routes()
