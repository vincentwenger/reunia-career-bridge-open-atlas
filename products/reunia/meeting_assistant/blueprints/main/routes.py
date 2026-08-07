from __future__ import annotations

from typing import Any

from career_bridge.application.evidence_readiness import (
    build_evidence_library_readiness,
)
from career_bridge.application.interview_readiness import (
    build_interview_readiness_assessments,
)
from flask import (
    current_app,
    jsonify,
    redirect,
    render_template,
    session,
    url_for,
)

from meeting_assistant.blueprints.main import main_bp
from meeting_assistant.services.action_service import ActionService
from meeting_assistant.services.knowledge_service import KnowledgeService
from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.authentication import login_required


def _meaningful_career_profile(context: dict[str, Any]) -> bool:
    return any(
        str(context.get(key) or "").strip()
        for key in (
            "professional_headline",
            "current_location",
            "preferred_roles",
            "industries",
            "countries_worked",
            "target_country",
            "target_country_experience",
            "titles_needing_translation",
            "career_transition",
            "work_preferences",
            "relocation_preferences",
            "work_authorization",
            "career_goals",
            "constraints",
        )
    )


def _is_mock_interview_review(review: dict[str, Any]) -> bool:
    topics = review.get("topics") or []
    if not isinstance(topics, list):
        topics = [topics]
    topic_text = " ".join(str(item or "") for item in topics).casefold()
    return bool(
        str(review.get("career_application_id") or "").strip()
        or str(review.get("interview_type") or "").strip()
        or str(review.get("interview_type_label") or "").strip()
        or "mock interview" in topic_text
        or "adaptive_mock_interview" in str(review.get("entity_type") or "").casefold()
    )


def _has_mock_interview_scorecard(review: dict[str, Any]) -> bool:
    return bool(
        review.get("interview_scorecard")
        or review.get("scorecard")
        or review.get("performance_scorecard")
        or review.get("overall_score") is not None
        or review.get("performance_score") is not None
    )


def _display_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _workflow_has_source_profile(workflow: Any) -> bool:
    profile = getattr(workflow, "source_profile", None)
    if profile is None:
        return False
    source_text = getattr(profile, "all_source_text", None)
    if callable(source_text):
        try:
            return bool(str(source_text() or "").strip())
        except Exception:
            return False
    return False


def _career_evidence_readiness(user_id: str) -> dict[str, Any]:
    """Load and summarize reusable evidence for the dashboard."""

    try:
        library = KnowledgeService().list_library(user_id)
    except Exception:
        current_app.logger.exception(
            "Could not load Career Evidence Library readiness for dashboard"
        )
        library = {}
    return build_evidence_library_readiness(library)


def _dashboard_records(user_id: str) -> tuple[list[Any], Any | None, Any | None, Any | None]:
    """Load only the records needed by the authenticated dashboard."""

    application_store = current_app.extensions.get("career_bridge_application_store")
    workflow_store = current_app.extensions.get("career_bridge_workflow_store")
    if application_store is None:
        return [], None, None, None

    try:
        applications = application_store.list_for_owner(user_id)
    except Exception:
        current_app.logger.exception("Could not load applications for dashboard")
        return [], None, None, None

    selected_application = None
    active_application_id = str(session.get("active_application_id") or "").strip()
    if active_application_id:
        selected_application = next(
            (
                item
                for item in applications
                if str(getattr(item, "id", "") or "") == active_application_id
            ),
            None,
        )
    if selected_application is None and applications:
        selected_application = applications[0]

    if workflow_store is None:
        return applications, selected_application, None, None

    selected_workflow = None
    selected_application_id = str(
        getattr(selected_application, "id", "") or ""
    ).strip()
    if selected_application_id:
        try:
            selected_workflow = workflow_store.peek(
                f"{user_id}:application:{selected_application_id}"
            )
        except Exception:
            current_app.logger.exception("Could not load active application workflow for dashboard")

    foundation_workflow = None
    try:
        foundation_workflow = workflow_store.peek(
            f"{user_id}:career-foundation:translation"
        )
    except Exception:
        current_app.logger.exception("Could not load Baseline Resume status for dashboard")

    return applications, selected_application, selected_workflow, foundation_workflow


@main_bp.get("/")
def marketing_page():
    """Render the public marketing site or send signed-in users to the app."""
    if session.get("user_id"):
        return redirect(url_for("main.view_index"))
    return render_template("marketing.html")


@main_bp.get("/index.html")
@main_bp.get("/app")
def view_index():
    """Render the authenticated Career Bridge dashboard."""
    if not session.get("user_id"):
        return redirect(url_for("main.marketing_page"))
    return render_template("index.html", desktop_recorder_available=False)


@main_bp.get("/api/career/dashboard-summary")
@login_required
def dashboard_summary():
    """Return a compact, resilient summary for the production dashboard."""

    user_id = str(session["user_id"])
    applications, selected_application, workflow, foundation = _dashboard_records(
        user_id
    )

    profile_complete = False
    try:
        profile_complete = _meaningful_career_profile(
            UserService().get_assistant_context(user_id)
        )
    except Exception:
        current_app.logger.exception("Could not load Career Profile for dashboard")

    foundation_has_resume = _workflow_has_source_profile(foundation)
    translation_ready = bool(
        foundation_has_resume
        and str(
            getattr(foundation, "source_profile_translation_fingerprint", "") or ""
        ).strip()
    )
    translation_state = (
        "ready"
        if translation_ready
        else ("needs_review" if foundation_has_resume else "not_started")
    )
    evidence_library = _career_evidence_readiness(user_id)

    selected_application_id = str(
        getattr(selected_application, "id", "") or ""
    ).strip()
    application_company = str(
        getattr(selected_application, "company", "") or ""
    ).strip()
    application_role = str(getattr(selected_application, "role", "") or "").strip()
    application_status = _display_value(getattr(selected_application, "status", ""))

    resume_stage = str(getattr(workflow, "workflow_stage", "") or "").strip()
    resume_ready = bool(
        resume_stage in {"draft", "final"}
        or getattr(workflow, "draft_proposal", None)
        or getattr(workflow, "final_proposal", None)
        or getattr(workflow, "final_resume_key", "")
    )

    preparation_ready = False
    if selected_application_id:
        application_store = current_app.extensions.get("career_bridge_application_store")
        try:
            preparation_ready = bool(
                application_store
                and application_store.get_interview_preparation(
                    user_id, selected_application_id
                )
            )
        except Exception:
            current_app.logger.exception(
                "Could not load Interview Preparation status for dashboard"
            )

    reviews: list[dict[str, Any]] = []
    try:
        reviews = [
            review
            for review in TranscriptService().list_for_user(user_id)
            if _is_mock_interview_review(review)
        ]
    except Exception:
        current_app.logger.exception("Could not load mock interviews for dashboard")
    scorecard_count = sum(1 for review in reviews if _has_mock_interview_scorecard(review))
    readiness = None
    if selected_application_id:
        readiness = build_interview_readiness_assessments(
            [selected_application_id],
            prepared_application_ids=(
                [selected_application_id] if preparation_ready else []
            ),
            reviews=reviews,
        ).get(selected_application_id)

    action_count = 0
    try:
        action_count = len(ActionService().list_for_user(user_id))
    except Exception:
        current_app.logger.exception("Could not load Career Action Plan for dashboard")

    application_query = (
        f"&application_id={selected_application_id}" if selected_application_id else ""
    )
    interview_query = (
        f"?application_id={selected_application_id}" if selected_application_id else ""
    )

    if not profile_complete:
        recommended_action = {
            "label": "Complete Career Profile",
            "description": "Add reusable background, goals, preferences, and constraints.",
            "url": "/career-profile",
        }
    elif not translation_ready:
        recommended_action = {
            "label": "Create your Baseline Resume",
            "description": "Create a reusable target-market resume baseline.",
            "url": "/applications/career-translation",
        }
    elif not applications:
        recommended_action = {
            "label": "Discover jobs",
            "description": "Find opportunities that match your verified profile.",
            "url": "/applications/job-discovery",
        }
    elif not resume_ready:
        recommended_action = {
            "label": "Continue Resume Workflow",
            "description": "Build the application-specific resume for your active role.",
            "url": f"/applications/?tab=tailoring{application_query}",
        }
    elif not preparation_ready:
        recommended_action = {
            "label": "Prepare for the interview",
            "description": "Create role-specific questions, strengths, gaps, and talking points.",
            "url": f"/applications/interview-preparation{interview_query}",
        }
    elif not reviews:
        recommended_action = {
            "label": "Practice a mock interview",
            "description": "Test your answers with adaptive, evidence-aware questions.",
            "url": f"/mock-interview{interview_query}",
        }
    elif action_count:
        recommended_action = {
            "label": "Review your action plan",
            "description": "Work through the follow-ups generated from your applications and practice.",
            "url": "/career-action-plan",
        }
    else:
        recommended_action = {
            "label": "Open Job Applications",
            "description": "Review your active applications and choose what to work on next.",
            "url": "/applications/?tab=applications",
        }

    foundation_complete_count = sum(
        (profile_complete, translation_ready, evidence_library["ready"])
    )

    response = jsonify(
        {
            "applications": {
                "count": len(applications),
                "active": (
                    {
                        "id": selected_application_id,
                        "company": application_company,
                        "role": application_role,
                        "status": application_status,
                        "resume_ready": resume_ready,
                        "resume_stage": resume_stage,
                        "preparation_ready": preparation_ready,
                        "interview_readiness": (
                            readiness.score if readiness is not None else None
                        ),
                        "interview_readiness_status": (
                            readiness.status_label if readiness is not None else "Not started"
                        ),
                        "workspace_url": f"/applications/?tab=applications{application_query}",
                        "resume_url": f"/applications/?tab=tailoring{application_query}",
                        "preparation_url": f"/applications/interview-preparation{interview_query}",
                    }
                    if selected_application is not None
                    else None
                ),
            },
            "foundation": {
                "complete_count": foundation_complete_count,
                "total_count": 3,
                "profile": {
                    "complete": profile_complete,
                    "url": "/career-profile",
                },
                "translation": {
                    "state": translation_state,
                    "ready": translation_ready,
                    "url": "/applications/career-translation",
                },
                "evidence_library": {
                    **evidence_library,
                    "url": "/career-evidence-library",
                },
            },
            "interviews": {
                "practice_count": len(reviews),
                "scorecard_count": scorecard_count,
            },
            "actions": {"count": action_count},
            "recommended_action": recommended_action,
        }
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@main_bp.get("/download/desktop-client")
def download_desktop_client():
    """The Windows recorder is not part of Career Bridge."""
    return (
        "The Windows Desktop Recorder is not part of Career Bridge.",
        410,
        {"Content-Type": "text/plain; charset=utf-8"},
    )
