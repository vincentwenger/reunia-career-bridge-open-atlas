from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4
from urllib.parse import urlsplit

from dotenv import load_dotenv
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

from career_bridge.profile_context import ReusableCareerProfile
from job_discovery.application_conversion import DiscoveredJobApplicationService
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
)
from job_discovery.posting_age import evaluate_posting_age
from job_discovery.public_catalog import SHARED_CATALOG_SOURCE_OWNER_ID
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
from job_discovery.sources.workday import parse_workday_careers_url
from job_discovery.scheduling import next_scheduled_run
from job_discovery.storage import (
    DiscoveryOptimisticLockError,
    DiscoveryStore,
    InMemoryDiscoveryStore,
)

from resume_tailor.ai import ResumeAI, ResumeAIError
from resume_tailor.application_tracker import (
    APPLICATION_STATUS_OPTIONS,
    INTERVIEW_AUDIENCE_SUGGESTIONS,
    RESUME_VERSION_OPTIONS,
    UPCOMING_EVENT_TYPE_OPTIONS,
    build_application_metrics,
    normalize_application_status,
    normalize_iso_date,
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
from resume_tailor.career_translation import ensure_career_translation_assessment
from resume_tailor.confirmation_followup import (
    MAX_TARGETED_FOLLOW_UP_ROUNDS,
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
    EducationItem,
    NewcomerCareerProfile,
    ProposalAudit,
    SkillSet,
    TailoringProposal,
)
from resume_tailor.profile_io import (
    candidate_bullet_text,
    load_candidate_profile,
    load_candidate_profile_bytes,
)
from resume_tailor.resume_import import extract_resume_text, resume_extension
from resume_tailor.optimization import (
    FINAL_OPTIMIZATION_SECTIONS,
    final_optimization_actionable_issues,
    final_optimization_score_guard,
)
from resume_tailor.proposal_changes import summarize_tailoring_changes
from resume_tailor.proposal_integrity import repair_missing_bullet_proposals
from resume_tailor.question_prioritization import prioritize_candidate_questions
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
DEFAULT_PROFILE_PATH = BASE_DIR / "data" / "candidate_profile.json"
RESUME_TEMPLATE_PATHS = {
    "early_career": BASE_DIR / "data" / "resume_template_early_career.docx",
    "professional": BASE_DIR / "data" / "resume_template_professional.docx",
    "executive": BASE_DIR / "data" / "resume_template_executive.docx",
}
DEFAULT_JOB_PATH = BASE_DIR / "data" / "job_description_example.txt"

try:
    RESUME_PAGE_LIMIT = max(1, int(os.getenv("RESUME_PAGE_LIMIT", "2")))
except ValueError:
    RESUME_PAGE_LIMIT = 2


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
    """Return request-time profile context without persisting it into the application."""

    reusable = (
        getattr(g, "reusable_career_profile", ReusableCareerProfile())
        if has_app_context()
        else ReusableCareerProfile()
    )
    return _career_background_with_profile(
        state.career_background,
        reusable,
        target_role=state.target_title,
    )

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
    # The Career Translation Assessment is advisory workflow metadata. It does
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
INITIAL_RESUME_LABEL = "Initial Resume"
JOB_ALIGNED_RESUME_LABEL = "Job-Aligned Resume"
FINAL_RESUME_LABEL = "Final Resume"

CAREER_TRANSLATION_CATEGORY_LABELS = {
    "job_title_translation": "Job titles that may be misunderstood",
    "credential_explanation": "Credentials requiring explanation",
    "regional_terminology": "Region-specific professional terminology",
    "hidden_accomplishment": "Accomplishments hidden by unfamiliar language",
    "transferable_skill": "Transferable skills",
    "unsupported_requirement": "Unsupported target-job requirements",
    "missing_evidence": "Important evidence missing from the resume",
}
CAREER_TRANSLATION_CATEGORY_ORDER = tuple(CAREER_TRANSLATION_CATEGORY_LABELS)
CAREER_EVIDENCE_DISPOSITION_LABELS = {
    "confirmed_experience": "Confirmed experience",
    "reasonable_rephrasing": "Reasonable rephrasing",
    "user_clarification_required": "User clarification required",
    "unsupported_claim": "Unsupported claim",
    "recommended_learning_or_future_action": "Recommended learning or future action",
}
CAREER_EVIDENCE_DISPOSITION_DESCRIPTIONS = {
    "confirmed_experience": "Directly supported by traceable resume or confirmed-profile evidence.",
    "reasonable_rephrasing": "Facts stay unchanged while wording is translated for the target market.",
    "user_clarification_required": "Potentially useful, but the system needs a factual answer before using it.",
    "unsupported_claim": "Not supported by current evidence and excluded from the resume.",
    "recommended_learning_or_future_action": "A genuine gap to address later, never presented as current experience.",
}


def career_translation_assessment_view(
    proposal: TailoringProposal | None,
) -> dict[str, Any] | None:
    if proposal is None:
        return None
    assessment = proposal.career_translation_assessment
    if not assessment.summary and not assessment.findings:
        return None

    groups: list[dict[str, Any]] = []
    counts = {key: 0 for key in CAREER_EVIDENCE_DISPOSITION_LABELS}
    for category in CAREER_TRANSLATION_CATEGORY_ORDER:
        findings = []
        for finding in assessment.findings:
            if finding.category != category:
                continue
            counts[finding.disposition] = counts.get(finding.disposition, 0) + 1
            findings.append(
                {
                    "finding": finding,
                    "disposition_label": CAREER_EVIDENCE_DISPOSITION_LABELS[
                        finding.disposition
                    ],
                    "disposition_description": CAREER_EVIDENCE_DISPOSITION_DESCRIPTIONS[
                        finding.disposition
                    ],
                }
            )
        if findings:
            groups.append(
                {
                    "key": category,
                    "label": CAREER_TRANSLATION_CATEGORY_LABELS[category],
                    "findings": findings,
                }
            )
    return {
        "summary": assessment.summary,
        "target_country": assessment.target_country,
        "target_role": assessment.target_role,
        "groups": groups,
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
            proposal_lookup[bullet.id].proposed_text.strip()
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




def _default_state() -> WorkflowState:
    profile = load_candidate_profile(DEFAULT_PROFILE_PATH)
    job_description = DEFAULT_JOB_PATH.read_text(encoding="utf-8") if DEFAULT_JOB_PATH.exists() else ""
    return WorkflowState(
        source_profile=profile,
        career_background=NewcomerCareerProfile(
            languages=list(profile.skills.languages)
        ),
        job_description=job_description,
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
    evidence decisions plus confirmed Candidate Profile facts. This prevents a
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
        headline = "Current stage: Career and Job Setup"
        guidance = "Add the target job and source resume, then analyze the match."
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
            else "The job inputs changed. Return to Career and Job Setup before continuing."
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
        ("setup", "Career and Job Setup", "Add the target job, source resume, and career context.", "#job-input"),
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
                    "matched_requirements": [
                        f"{rid}: {requirement_lookup[rid].requirement}"
                        for rid in item.matched_requirement_ids
                        if rid in requirement_lookup
                    ],
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
                "retained_count": status_counts["unchanged"] + status_counts["modified"],
                "rewritten_count": status_counts["rewritten"],
                "changed_count": status_counts["modified"],
                "added_count": status_counts["added"],
                "excluded_count": status_counts["excluded"],
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
        item.include = form.get(f"include__{item.source_bullet_id}") == "on"
        item.proposed_text = form.get(
            f"text__{item.source_bullet_id}", item.proposed_text
        ).strip()
        if item.include and item.evidence_note.startswith(rewritten_prefix):
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


store: WorkflowStore = LocalProxy(
    lambda: current_app.extensions["career_bridge_workflow_store"]
)
application_store: ApplicationStore = LocalProxy(
    lambda: current_app.extensions["career_bridge_application_store"]
)
discovery_store: DiscoveryStore = LocalProxy(
    lambda: current_app.extensions["career_bridge_job_discovery_store"]
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
    """Return non-secret storage capabilities for operational health checks."""

    workflow_backend = configured_workflow_backend(current_app.config)
    application_backend = configured_application_backend(current_app.config)
    document_backend = configured_document_backend(current_app.config)
    fully_persistent = (
        workflow_backend == "dynamodb"
        and application_backend == "dynamodb"
        and document_backend == "s3"
    )
    return {
        "workflow_storage": workflow_backend,
        "application_storage": application_backend,
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

    warning_key = "career_bridge_application_builder_persistence_warning_logged"
    if not app.extensions.get(warning_key):
        fully_persistent = (
            workflow_backend == "dynamodb"
            and application_backend == "dynamodb"
            and document_backend == "s3"
        )
        log = app.logger.info if fully_persistent else app.logger.warning
        log(
            "Application Builder storage configured: workflow=%s, "
            "applications=%s, documents=%s%s",
            workflow_backend,
            application_backend,
            document_backend,
            "" if fully_persistent else "; workflow or document storage is not fully durable",
        )
        app.extensions[warning_key] = True


def _register_application_builder_routes() -> None:
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
        retry_url = (
            url_for(
                "application_builder.index",
                tab="tailoring",
                application_id=active_application.id,
            )
            if active_application is not None
            else url_for("application_builder.index", tab="applications")
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
                    active_tab="tailoring",
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

    @application_builder_bp.before_request
    def load_workflow_state() -> Response | None:
        if current_app.config.get("CAREER_BRIDGE_REQUIRE_AUTH") and not session.get(
            "user_id"
        ):
            return redirect(
                str(
                    current_app.config.get("CAREER_BRIDGE_LOGIN_URL")
                    or "/login.html"
                )
            )

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

        requested_application_id = (
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
            f"{owner_id}:application:{requested_application_id}"
            if requested_application_id
            else f"{owner_id}:application:scratch"
        )
        session["active_workflow_key"] = workflow_key
        g.application_owner_id = owner_id
        g.active_application = application
        g.workflow_key = workflow_key
        g.workflow_state_deleted = False
        g.workflow_request_id = normalize_workflow_request_id(
            getattr(g, "request_id", "")
        )
        loaded_workflow = store.load(workflow_key)
        g.workflow_state = loaded_workflow.state
        g.workflow_initial_version = loaded_workflow.version
        g.workflow_initial_fingerprint = loaded_workflow.fingerprint
        g.workflow_initial_updated_at = loaded_workflow.updated_at
        g.workflow_initial_updated_by_request = loaded_workflow.updated_by_request
        if application is not None:
            if not g.workflow_state.source_resume_key and application.original_resume_key:
                g.workflow_state.source_resume_key = application.original_resume_key
            if not g.workflow_state.target_title:
                g.workflow_state.target_title = application.role
            if not g.workflow_state.career_background.target_role:
                g.workflow_state.career_background.target_role = (
                    g.workflow_state.target_title or application.role
                )
            if not g.workflow_state.job_description and application.job_description:
                g.workflow_state.job_description = application.job_description

        g.reusable_career_profile = _load_reusable_career_profile(owner_id)
        return None

    @application_builder_bp.after_request
    def persist_workflow_state(response: Response) -> Response:
        """Persist only changed state using optimistic version checking."""

        workflow_key = str(getattr(g, "workflow_key", "") or "")
        workflow_state = getattr(g, "workflow_state", None)
        if (
            workflow_key
            and workflow_state is not None
            and not bool(getattr(g, "workflow_state_deleted", False))
        ):
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
                return response
            try:
                saved = store.save(
                    workflow_key,
                    workflow_state,
                    expected_version=int(
                        getattr(g, "workflow_initial_version", 0) or 0
                    ),
                    updated_by_request=str(
                        getattr(g, "workflow_request_id", "") or ""
                    ),
                )
            except WorkflowConflictError as exc:
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
            g.workflow_initial_version = saved.version
            g.workflow_initial_fingerprint = saved.fingerprint
            g.workflow_initial_updated_at = saved.updated_at
            g.workflow_initial_updated_by_request = saved.updated_by_request
        return response

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

    def state() -> WorkflowState:
        workflow_state = g.workflow_state
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

    def _interactive_discovery_source(source: CompanySource) -> CompanySource:
        """Apply browser-safe limits without changing the saved source settings."""

        if source.source_type is not JobSourceType.WORKDAY:
            return source
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

        filters.update(
            {
                "max_jobs": capped_int("max_jobs", 80, 80),
                "max_pages": max(1, capped_int("max_pages", 4, 4)),
                "detail_fetch_limit": capped_int("detail_fetch_limit", 10, 10),
                "fetch_budget_seconds": capped_float(
                    "fetch_budget_seconds", 18.0, 18.0
                ),
                "timeout_seconds": max(1.0, capped_float("timeout_seconds", 5.0, 5.0)),
                "min_request_interval_seconds": capped_float(
                    "min_request_interval_seconds", 0.2, 0.2
                ),
            }
        )
        return replace(source, filters=filters)

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

    _DISCOVERY_RESULT_INDEX_VERSION = "3"
    _DISCOVERY_RESULT_TABS = (
        "recommended",
        "possible",
        "pending",
        "low_match",
        "saved",
        "ignored",
    )
    _DISCOVERY_PAGE_SIZES = (10, 20, 50)
    _DISCOVERY_MINIMUM_FIT_OPTIONS = (0, 50, 60, 70, 80)

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
        value = _discovery_positive_int(raw, default=10)
        return value if value in _DISCOVERY_PAGE_SIZES else 10

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
        return {
            "job": record.job,
            "fit": record.fit,
            "state": None,
            "disposition": record.disposition,
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
        per_page: int = 10,
        maximum_posting_age_days: int | None = DEFAULT_MAX_POSTING_AGE_DAYS,
        filters: DiscoveryResultFilters | None = None,
        allowed_source_ids: tuple[str, ...] = (),
    ) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
        """Return one page from a compact materialized discovery result index.

        A stale or missing index is rebuilt once from jobs, states, and fit
        snapshots. The index key includes the result-quality filters and sort
        mode so DynamoDB can return the exact pre-ranked page directly.
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
        if (
            cached_summary is not None
            and cached_summary.revision_token == revision_token
        ):
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
            }
            return page_cards, summary, pagination

        applications = application_store.list_for_owner(owner_id)
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
            fit = fits_by_key.get(
                (job.id, profile.fingerprint, job.description_fingerprint)
            ) or fits_by_key.get((job.id, profile.fingerprint, ""))
            job_state = states.get((job.source_id, job.id))
            application = applications_by_source_job.get(job.id)
            disposition = (
                DiscoveryJobDisposition.APPLICATION_CREATED
                if application is not None
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
        }
        return page_cards, summary, pagination

    def _job_action_service() -> DiscoveredJobApplicationService:
        return DiscoveredJobApplicationService(discovery_store, application_store)

    @application_builder_bp.get("/job-discovery")
    def job_discovery_workspace():
        current = state()
        owner_id = g.application_owner_id
        discovery_view = (
            "settings" if request.args.get("view") == "settings" else "results"
        )
        can_manage_catalog = _current_user_can_manage_job_catalog()
        discovery_sources = discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID
        )
        if discovery_view == "results" and discovery_sources:
            try:
                (
                    JobDiscoveryService(store=discovery_store)
                    .enable_shared_public_catalog()
                    .hydrate_owner_from_shared_catalog(owner_id, discovery_sources)
                )
            except Exception as exc:
                current_app.logger.warning(
                    "Shared public job catalog hydration failed owner=%s error=%s",
                    owner_id,
                    exc,
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
        discovery_preferences = _discovery_search_preferences(owner_id, current)

        template_context: dict[str, Any] = {
            "active_tab": "discovery",
            "discovery_view": discovery_view,
            "can_manage_job_catalog": can_manage_catalog,
            "discovery_source_count": len(discovery_sources),
            "enabled_discovery_source_count": len(enabled_discovery_sources),
            "discovery_checked_label": _discovery_checked_label(
                latest_discovery_check
            ),
            "discovery_sources": discovery_sources,
            "discovery_source_checked_labels": {
                source.id: _discovery_checked_label(source.last_checked_at)
                for source in discovery_sources
            },
            "discovery_source_types": (
                (JobSourceType.GREENHOUSE.value, "Greenhouse"),
                (JobSourceType.LEVER.value, "Lever"),
                (JobSourceType.ASHBY.value, "Ashby"),
                (JobSourceType.WORKDAY.value, "Workday"),
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
            discovery_schedule = _discovery_scan_schedule(
                SHARED_CATALOG_SOURCE_OWNER_ID
            )
            try:
                next_run = next_scheduled_run(discovery_schedule)
                schedule_error = ""
            except ValueError as exc:
                next_run = None
                schedule_error = str(exc)
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
            discovery_profile = _discovery_candidate_profile(
                current,
                owner_id=owner_id,
            )
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
            template_context.update(
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

        return render_template(
            "application_builder/job_discovery.html",
            **template_context,
        )

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
        current = state()
        ensure_recommended_resume_style(current)

        active_tab = request.args.get("tab", "applications")
        if active_tab not in {"tailoring", "reports", "applications", "configuration"}:
            active_tab = "applications"

        if active_tab == "applications":
            owner_id = g.application_owner_id
            applications = application_store.list_for_owner(owner_id)
            style_options = resume_style_options()
            return render_template(
                "application_builder/applications.html",
                active_tab=active_tab,
                applications=applications,
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
                default_application_date=datetime.now().date().isoformat(),
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
        profile = current.confirmed_profile or source_profile
        analysis = current.analysis
        proposal = working_proposal_for_stage(current)
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
            for question in proposal.candidate_questions:
                confirmation_rows.append(
                    {
                        "question": question,
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
            current.provisional_proposal or proposal
        )
        return render_template(
            "application_builder/index.html",
            state=current,
            active_tab=active_tab,
            guided_workflow=guided_workflow,
            preliminary_application_fit=preliminary_fit,
            application_fit=application_fit,
            career_translation_assessment=career_translation_assessment,
            career_background=_effective_career_background(current),
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
        return_to = str(request.form.get("return_to") or "").strip().casefold()
        redirect_target = (
            url_for("application_builder.index", tab="tailoring", stage="setup") + "#resume-import"
            if return_to == "setup"
            else url_for("application_builder.index", tab="configuration") + "#candidate-profile"
        )
        if not uploaded or not uploaded.filename:
            flash("Choose a PDF, Word, text, Markdown, or Candidate Profile JSON file.", "error")
            return redirect(redirect_target)

        filename = uploaded.filename
        data = uploaded.read()
        current = state()
        try:
            if resume_extension(filename) == ".json":
                profile = load_candidate_profile_bytes(data)
            else:
                resume_text = extract_resume_text(data, filename)
                models = resolve_models(current)
                profile = ResumeAI(
                    models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
                ).create_candidate_profile_from_resume(
                    resume_text=resume_text,
                    filename=filename,
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
        current.source_profile = profile
        current.profile_upload_name = filename
        current.source_resume_key = source_object_key
        current.source_resume_fingerprint = source_fingerprint
        active_application = getattr(g, "active_application", None)
        if active_application is not None:
            application_store.update_builder_progress(
                str(getattr(g, "application_owner_id", "") or ""),
                active_application.id,
                workflow_step=active_application.workflow_step,
                original_resume_key=source_object_key,
            )
        current.clear_results()
        if previous_source_key and previous_source_key != source_object_key:
            document_store.delete(previous_source_key)
        flash(
            "International resume imported into the verified Candidate Profile. Previous analysis results were cleared.",
            "success",
        )
        return redirect(redirect_target)

    @application_builder_bp.post("/profile/default")
    def restore_default_profile():
        current = state()
        previous_source_key = current.source_resume_key
        current.source_profile = load_candidate_profile(DEFAULT_PROFILE_PATH)
        current.profile_upload_name = ""
        current.source_resume_key = ""
        current.source_resume_fingerprint = ""
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
        flash("The bundled Candidate Profile was restored.", "success")
        return redirect(url_for("application_builder.index", tab="configuration"))

    @application_builder_bp.post("/reset")
    def reset_workflow():
        current = state()
        current.clear_results()
        flash("Workflow results were reset. Your configuration and current inputs were preserved.", "success")
        return redirect(url_for("application_builder.index", tab="configuration"))

    @application_builder_bp.post("/workflow/start")
    def start_workflow():
        current = state()
        update_job_fields()
        action = request.form.get("action", "")
        tailoring_started = False
        if not current.job_description.strip():
            flash("Paste or upload a job description first.", "error")
            return redirect(url_for("application_builder.index", tab="tailoring"))
        try:
            models = resolve_models(current)
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
                ai = None
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
                current.initial_evidence_proposal = evidence_source.model_copy(
                    deep=True
                )
                current.initial_evidence_input_fingerprint = current_input
                created = _refresh_initial_resume_report(
                    current, analysis, evidence_source, force=True
                )
                if created:
                    flash(
                        "Initial Resume Report refreshed successfully.", "success"
                    )
                else:
                    flash(
                        "The Initial Resume Report could not be refreshed: "
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
                ai = ResumeAI(
                    model=models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
                )
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
                current.confirmed_profile = None
                current.initial_evidence_proposal = proposal.model_copy(deep=True)
                current.initial_evidence_input_fingerprint = current_input
                # Step 2 should appear as soon as the tailoring proposal is ready.
                # The Initial Resume Report is generated by an automatic follow-up
                # request after the page loads, so Word rendering does not block navigation.
                current.initial_report = None
                current.initial_report_input_fingerprint = None
                current.initial_report_analysis = None
                current.initial_report_proposal = None
                current.initial_report_created_at = ""
                current.initial_report_error = ""
                tailoring_started = True
                flash(
                    "Job analysis and Career Translation Assessment completed. Confirm the high-value experience questions next; the Initial Resume Report is generating automatically without blocking the workflow.",
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
                "Save a job description in Career and Job Setup before retrying the report.",
                "error",
            )
            return redirect(url_for("application_builder.index", tab="reports", report="initial"))
        try:
            models = resolve_models(current)
            current_input = input_fingerprint(current, models)
            analysis_is_current = bool(
                current.analysis
                and current.analysis_input_fingerprint == current_input
            )
            evidence_is_current = bool(
                current.initial_evidence_proposal
                and current.initial_evidence_input_fingerprint == current_input
            )
            ai = None
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
            current.initial_evidence_proposal = evidence_source.model_copy(
                deep=True
            )
            current.initial_evidence_input_fingerprint = current_input
            if _refresh_initial_resume_report(
                current, analysis, evidence_source, force=True
            ):
                flash("Initial Resume Report refreshed.", "success")
            else:
                raise ValueError(
                    current.initial_report_error
                    or "The Initial Resume Report could not be refreshed."
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
                    "The job description or analysis model changed. Return to Career and Job Setup and select Start tailoring again."
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
                label = "Initial Resume Report"
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
                    "The job description or analysis model changed. Return to Career and Job Setup and select Start tailoring again."
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
        processing_warnings: list[str] = []
        try:
            models = resolve_models(current)
            if current.analyzed_input_fingerprint != input_fingerprint(current, models):
                raise ValueError(
                    "The job description changed. Return to Career and Job Setup and select Start tailoring again."
                )

            questions = current.provisional_proposal.candidate_questions
            answers, draft = collect_candidate_answers(questions, request.form)
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
            all_questions_declined = bool(questions) and all(
                answer.yes_no is False and not answer.text.strip()
                for answer in answers
            )
            if questions and not all_questions_declined:
                try:
                    ai = ResumeAI(
                        model=models.analysis_tailoring_model,
                        reasoning_effort=models.analysis_tailoring_reasoning_effort,
                    )
                    refined = ai.refine_proposal(
                        confirmed_profile,
                        current.analysis,
                        proposal_for_refinement,
                        answers,
                        _effective_career_background(current),
                    )
                except ResumeAIError as exc:
                    # Do not send the candidate back through the same completed
                    # questions when the optional refinement request fails. The
                    # provisional proposal already contains source-backed decisions;
                    # confirmed answers are added deterministically below.
                    current_app.logger.warning(
                        "Confirmation refinement failed; using the safe provisional proposal: %s",
                        exc,
                    )
                    refined = proposal_for_refinement.model_copy(deep=True)
                    processing_warnings.append(
                        "The optional AI wording refinement could not complete, so the app kept the existing job-aligned wording and applied your confirmed evidence safely."
                    )
            else:
                # When every candidate question is explicitly declined, there is no
                # new evidence for the model to incorporate. Reuse the proposal and
                # let the bounded evidence review remove or soften unsupported text.
                refined = proposal_for_refinement

            refined = repair_missing_bullet_proposals(confirmed_profile, refined)
            refined.skills = balance_skill_categories(
                confirmed_profile, current.analysis, refined.skills
            )
            refined = ensure_confirmed_answers_visible(confirmed_profile, refined)
            try:
                refined, review_audit, candidate_needed = _run_post_confirmation_evidence_review(
                    models,
                    confirmed_profile,
                    current.analysis,
                    refined,
                    _effective_career_background(current),
                    allow_candidate_questions=(
                        current.confirmation_follow_up_round
                        < MAX_TARGETED_FOLLOW_UP_ROUNDS
                    ),
                )
            except (ResumeAIError, ValueError) as exc:
                # A failed independent review previously redirected back to Step 2
                # with every prior answer still filled, which looked like the resume
                # had not been created. Fall back to the conservative pre-refinement
                # proposal, add confirmed evidence, and run deterministic validation
                # so the completed Step 2 can still advance to Step 3.
                current_app.logger.warning(
                    "Post-confirmation evidence review failed; using deterministic fallback: %s",
                    exc,
                )
                refined = repair_missing_bullet_proposals(
                    confirmed_profile,
                    proposal_for_refinement.model_copy(deep=True),
                )
                refined.skills = balance_skill_categories(
                    confirmed_profile, current.analysis, refined.skills
                )
                refined = ensure_confirmed_answers_visible(
                    confirmed_profile, refined
                )
                refined, _ = apply_all_until_valid(
                    confirmed_profile, current.analysis, refined
                )
                review_audit = ProposalAudit(
                    passed=False,
                    issues=[],
                    verified_strengths=[],
                )
                candidate_needed = []
                processing_warnings.append(
                    "The independent evidence review could not complete, so the app used conservative source-backed wording and deterministic validation. Review the Step 3 comparison before optimizing."
                )

            current.confirmed_profile = confirmed_profile
            current.candidate_answers = all_answers
            current.save_confirmed_profile = (
                current.save_confirmed_profile
                or request.form.get("save_confirmed_profile") == "on"
            )
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

            if candidate_needed:
                next_round = min(
                    current.confirmation_follow_up_round + 1,
                    MAX_TARGETED_FOLLOW_UP_ROUNDS,
                )
                follow_up_questions = build_targeted_follow_up_questions(
                    candidate_needed,
                    refined,
                    round_number=next_round,
                )
                refined.candidate_questions = follow_up_questions
                refined = ensure_career_translation_assessment(
                    confirmed_profile,
                    current.analysis,
                    refined,
                    _effective_career_background(current),
                )
                current.provisional_proposal = refined.model_copy(deep=True)
                current.confirmation_complete = False
                current.confirmation_follow_up_round = next_round
                current.confirmation_follow_up_count = len(follow_up_questions)
                redirect_stage = "confirmation"
                redirect_anchor = "#confirmation"
                count = len(follow_up_questions)
                flash(
                    f"Your answers were applied and the transformed resume was checked. "
                    f"{count} final targeted follow-up question{'s are' if count != 1 else ' is'} needed "
                    "before Review Tailored Resume. This is the only follow-up round; any "
                    "remaining uncertainty will use safer source-backed wording automatically.",
                    "warning",
                )
            else:
                refined.candidate_questions = []
                refined = ensure_career_translation_assessment(
                    confirmed_profile,
                    current.analysis,
                    refined,
                    _effective_career_background(current),
                )
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
                # Review Tailored Resume should open immediately. Its report is
                # generated automatically after the page becomes interactive.
                current.clear_draft_report()
                completion_message = (
                    "The targeted follow-up was applied. Any remaining uncertainty was removed or rewritten with safer source-backed wording."
                    if current.confirmation_follow_up_round
                    else
                    "Experience confirmation is complete and the Job-Aligned Resume is ready for review."
                )
                flash(
                    completion_message
                    + " Its Resume Report is generating automatically without blocking Step 3.",
                    "success",
                )
            for warning in processing_warnings:
                flash(warning, "warning")
        except (ResumeAIError, ValueError) as exc:
            flash(str(exc), "error")
            redirect_stage = "confirmation"
            redirect_anchor = "#confirmation"

        return redirect(
            url_for("application_builder.index", tab="tailoring", stage=redirect_stage) + redirect_anchor
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
                    "The job description or tailoring model changed. Return to Career and Job Setup and select Start tailoring again."
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
            report_issues = final_optimization_actionable_issues(report_before)
            optimized = working.model_copy(deep=True)
            report_after_scoring = report_before
            accepted_issues: list[AuditIssue] = []
            accepted_batch_count = 0
            rejected_batch_count = 0
            rejected_issue_count = 0
            unchanged_batch_count = 0
            optimization_warnings: list[str] = []
            current_validation_count = len(
                validate_proposal(profile, current.analysis, optimized)
            )

            if report_issues:
                optimizer = ResumeAI(
                    model=models.analysis_tailoring_model,
                    reasoning_effort=models.analysis_tailoring_reasoning_effort,
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
                    optimization_warnings.append(str(exc))
                else:
                    candidate = repair_missing_bullet_proposals(profile, candidate)
                    candidate = ensure_career_translation_assessment(
                        profile,
                        current.analysis,
                        candidate,
                        _effective_career_background(current),
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
                            else:
                                rejected_batch_count = 1
                                rejected_issue_count = len(report_issues)

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

            remaining_recommendations = final_optimization_recommendations(
                report_after
            )
            if baseline_rolled_back:
                flash(
                    "The current Final Resume scored below the saved Job-Aligned Resume, so the weaker working copy was rolled back before optimization.",
                    "warning",
                )
            if optimization_warnings:
                flash(
                    "The optional quality pass could not be completed, so the score-safe Job-Aligned Resume was preserved: "
                    + optimization_warnings[0],
                    "warning",
                )
            score_change = (
                f"Overall score {report_before.overall_score():.1f}% → "
                f"{report_after.overall_score():.1f}%."
            )
            background_note = (
                " Exact page-count verification is finishing automatically while you review Step 4."
            )
            if changed_by_optimization:
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
                    "The job description or tailoring model changed. Return to Career and Job Setup and start tailoring again."
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
        """Download the Initial, Job-Aligned, or Final resume version."""
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
        payload = json.dumps(state().source_profile.model_dump(), ensure_ascii=False, indent=2)
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

    @application_builder_bp.get("/career-translation")
    def career_translation_workspace():
        """Open the newcomer career context inside the editable setup step."""
        return redirect(
            url_for(
                "application_builder.index",
                tab="tailoring",
                stage="setup",
                edit="setup",
                focus="career-translation",
            )
            + "#newcomer-onboarding"
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
            company_name = str(request.form.get("company_name") or "").strip()
            careers_url = str(request.form.get("careers_url") or "").strip()
            source_identifier = _source_identifier_value(
                source_type,
                request.form.get("source_identifier", ""),
                careers_url,
            )
            if source_type is JobSourceType.GENERIC_JSONLD:
                source_identifier = ""
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
            source = CompanySource(
                id=uuid4().hex,
                owner_id=owner_id,
                company_name=company_name,
                careers_url=careers_url,
                source_type=source_type,
                source_identifier=source_identifier,
                enabled=request.form.get("enabled", "1") not in {"0", "false"},
                filters={
                    "include_compensation": True,
                    "deactivate_after_missed_scans": 3,
                },
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
            careers_url = str(request.form.get("careers_url") or "").strip()
            source_identifier = _source_identifier_value(
                source_type,
                request.form.get("source_identifier", ""),
                careers_url,
            )
            if source_type is JobSourceType.GENERIC_JSONLD:
                source_identifier = ""
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
            revision = int(request.form.get("revision", existing.revision))
            updated = replace(
                existing,
                company_name=str(
                    request.form.get("company_name") or existing.company_name
                ).strip(),
                careers_url=careers_url,
                source_type=source_type,
                source_identifier=source_identifier,
                enabled=request.form.get("enabled") == "1",
                revision=revision,
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

    @application_builder_bp.post("/discovery/refresh")
    def refresh_discovered_jobs():
        _require_job_catalog_manager()
        owner_id = _application_owner_id()
        sources = discovery_store.list_company_sources(
            SHARED_CATALOG_SOURCE_OWNER_ID, enabled_only=True
        )
        if not sources:
            flash(
                "No enabled job sources are configured. Add a company source before refreshing jobs.",
                "warning",
            )
            return redirect(_discovery_results_url())

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
        issue_count = len(result.errors) + len(result.analysis_errors)
        message = (
            f"Job refresh completed across {len(sources)} enabled source"
            f"{'s' if len(sources) != 1 else ''}. "
            "Collected public postings are now available to every user; "
            "each user's posting-age filters and evidence-based fit assessment "
            "remain private."
        )
        if result.shared_catalog_hits:
            message += (
                f" {result.shared_catalog_hits} source"
                f"{'s reused' if result.shared_catalog_hits != 1 else ' reused'} recently collected public jobs without rescanning."
            )
        if result.shared_catalog_refreshes:
            message += (
                f" {result.shared_catalog_refreshes} shared public source"
                f"{'s were' if result.shared_catalog_refreshes != 1 else ' was'} refreshed for all users."
            )
        if result.shared_refreshes_in_progress:
            message += (
                f" {result.shared_refreshes_in_progress} source refresh"
                f"{'es were' if result.shared_refreshes_in_progress != 1 else ' was'} already in progress; cached public jobs were used."
            )
        if issue_count:
            message += f" {issue_count} source or analysis issue{'s' if issue_count != 1 else ''} need review."
        flash(message, "warning" if issue_count else "success")
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
        return redirect(_discovery_results_url())

    @application_builder_bp.post(
        "/discovery/jobs/<source_id>/<job_id>/save"
    )
    def save_discovered_job(source_id: str, job_id: str):
        try:
            _job_action_service().save(_application_owner_id(), source_id, job_id)
        except LookupError:
            abort(404)
        flash("Job saved for later review.", "success")
        return redirect(_discovery_results_url(anchor=f"discovered-job-{job_id}"))

    @application_builder_bp.post(
        "/discovery/jobs/<source_id>/<job_id>/ignore"
    )
    def ignore_discovered_job(source_id: str, job_id: str):
        try:
            _job_action_service().ignore(_application_owner_id(), source_id, job_id)
        except LookupError:
            abort(404)
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
        flash(
            "Application workspace created from the discovered posting."
            if result.created
            else "This discovered posting already has an application workspace.",
            "success" if result.created else "info",
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

        created = application_store.create(
            _application_owner_id(),
            company=company,
            role=role,
            job_url=request.form.get("job_url", ""),
            interview_audience=request.form.get("interview_audience", ""),
            application_date=normalize_iso_date(request.form.get("application_date")),
            status=normalize_application_status(request.form.get("status")),
            resume_version=request.form.get("resume_version", "Not started"),
            resume_style=normalize_resume_style(request.form.get("resume_style")),
            alignment_score=_optional_score("alignment_score"),
            interview_readiness=_optional_score("interview_readiness"),
            notes=request.form.get("notes", ""),
            next_action=request.form.get("next_action", ""),
            next_follow_up_date=normalize_iso_date(
                request.form.get("next_follow_up_date")
            ),
            upcoming_event_date=normalize_iso_date(
                request.form.get("upcoming_event_date")
            ),
            upcoming_event_type=request.form.get("upcoming_event_type", ""),
            job_description=request.form.get("job_description", ""),
            workflow_step="setup",
        )
        session["active_application_id"] = created.id
        flash("Job application created. Continue with Career and Job Setup.", "success")
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
            interview_readiness=_optional_score("interview_readiness"),
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
        workflow_key = f"{_application_owner_id()}:application:{application_id}"
        workflow_state = store.peek(workflow_key)
        if not application_store.delete(_application_owner_id(), application_id):
            abort(404)
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
