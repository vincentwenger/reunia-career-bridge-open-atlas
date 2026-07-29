from __future__ import annotations

from typing import Any

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
from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.authentication import login_required


def _meaningful_career_profile(context: dict[str, Any]) -> bool:
    return any(
        str(context.get(key) or "").strip()
        for key in (
            "role",
            "domain",
            "objective",
            "free_text",
            "company",
            "audience",
        )
    )


def _workflow_rank(state: Any) -> tuple[int, int, int, int]:
    if state is None:
        return (0, 0, 0, 0)
    stage_rank = {"initial": 1, "draft": 2, "final": 3}.get(
        str(getattr(state, "workflow_stage", "") or ""),
        0,
    )
    return (
        stage_rank,
        int(bool(getattr(state, "confirmation_complete", False))),
        int(bool(getattr(state, "analysis", None))),
        int(bool(getattr(state, "profile_upload_name", ""))),
    )


def _builder_progress(user_id: str) -> tuple[list[Any], Any | None, Any | None]:
    application_store = current_app.extensions.get("career_bridge_application_store")
    workflow_store = current_app.extensions.get("career_bridge_workflow_store")
    if application_store is None:
        return [], None, None

    try:
        applications = application_store.list_for_owner(user_id)
    except Exception:
        current_app.logger.exception("Could not load applications for MVP progress")
        return [], None, None

    selected_application = None
    active_application_id = str(session.get("active_application_id") or "").strip()
    if active_application_id:
        selected_application = next(
            (item for item in applications if str(getattr(item, "id", "")) == active_application_id),
            None,
        )
    if selected_application is None and applications:
        selected_application = applications[0]

    if workflow_store is None:
        return applications, selected_application, None

    keys: list[str] = []
    active_workflow_key = str(session.get("active_workflow_key") or "").strip()
    if active_workflow_key:
        keys.append(active_workflow_key)
    keys.append(f"{user_id}:application:scratch")
    for application in applications:
        application_id = str(getattr(application, "id", "") or "").strip()
        if application_id:
            keys.append(f"{user_id}:application:{application_id}")

    states: list[Any] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        try:
            state = workflow_store.peek(key)
        except Exception:
            current_app.logger.exception("Could not inspect workflow state for MVP progress")
            continue
        if state is not None:
            states.append(state)

    selected_state = max(states, key=_workflow_rank, default=None)
    return applications, selected_application, selected_state




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


@main_bp.get("/")
def marketing_page():
    """Render the public marketing site or send signed-in users to the app."""
    if session.get("user_id"):
        return redirect(url_for("main.view_index"))
    return render_template("marketing.html")


@main_bp.get("/index.html")
@main_bp.get("/app")
def view_index():
    """Render the authenticated hackathon MVP journey dashboard."""
    if not session.get("user_id"):
        return redirect(url_for("main.marketing_page"))
    return render_template("index.html", desktop_recorder_available=False)


@main_bp.get("/api/career/mvp-progress")
@login_required
def mvp_progress():
    """Return resilient progress for the single Career Bridge hackathon journey."""
    user_id = str(session["user_id"])
    applications, selected_application, workflow = _builder_progress(user_id)

    profile_complete = False
    try:
        profile_complete = _meaningful_career_profile(
            UserService().get_assistant_context(user_id)
        )
    except Exception:
        current_app.logger.exception("Could not load Career Profile for MVP progress")

    resume_uploaded = bool(
        getattr(workflow, "profile_upload_name", "")
        or any(str(getattr(item, "resume_filename", "") or "").strip() for item in applications)
    )
    target_job_added = bool(
        applications
        or str(getattr(workflow, "job_description", "") or "").strip()
        or str(getattr(workflow, "target_title", "") or "").strip()
    )
    translation_ready = bool(getattr(workflow, "analysis", None))
    evidence_answered = bool(
        getattr(workflow, "confirmation_complete", False)
        or getattr(workflow, "candidate_answers", None)
    )
    tailored_resume_ready = bool(
        getattr(workflow, "draft_proposal", None)
        or getattr(workflow, "final_proposal", None)
        or getattr(workflow, "final_resume_bytes", None)
    )

    preparation_ready = False
    if selected_application is not None:
        application_store = current_app.extensions.get("career_bridge_application_store")
        try:
            preparation_ready = bool(
                application_store
                and application_store.get_interview_preparation(
                    user_id,
                    str(getattr(selected_application, "id", "")),
                )
            )
        except Exception:
            current_app.logger.exception("Could not load Interview Preparation for MVP progress")

    reviews: list[dict[str, Any]] = []
    try:
        reviews = [
            review
            for review in TranscriptService().list_for_user(user_id)
            if _is_mock_interview_review(review)
        ]
    except Exception:
        current_app.logger.exception("Could not load mock interviews for MVP progress")
    mock_interview_complete = bool(reviews)
    scorecard_ready = any(_has_mock_interview_scorecard(review) for review in reviews)

    actions_ready = False
    try:
        actions_ready = bool(ActionService().list_for_user(user_id))
    except Exception:
        current_app.logger.exception("Could not load Career Action Plan for MVP progress")

    completed = [
        profile_complete,
        resume_uploaded,
        target_job_added,
        translation_ready,
        evidence_answered,
        tailored_resume_ready,
        preparation_ready,
        mock_interview_complete,
        scorecard_ready,
        actions_ready,
    ]
    completed_count = sum(1 for value in completed if value)
    current_index = next((index for index, value in enumerate(completed) if not value), len(completed) - 1)

    selected_application_id = (
        str(getattr(selected_application, "id", "") or "")
        if selected_application is not None
        else ""
    )
    preparation_url = "/applications/interview-preparation"
    mock_url = "/mock-interview"
    if selected_application_id:
        preparation_url += f"?application_id={selected_application_id}"
        mock_url += f"?application_id={selected_application_id}"

    steps = [
        ("career-profile", "Create a Career Profile", "/career-profile?guided=1", completed[0]),
        ("resume-upload", "Upload an international resume", "/applications/?tab=tailoring&stage=setup#resume-import", completed[1]),
        ("target-job", "Add a target job", "/applications/?tab=applications#new-application", completed[2]),
        ("translation", "Review the Career Translation Assessment", "/applications/?tab=tailoring&stage=confirmation#career-translation-assessment", completed[3]),
        ("evidence", "Answer evidence questions", "/applications/?tab=tailoring&stage=confirmation#confirmation", completed[4]),
        ("tailored-resume", "Generate a tailored resume", "/applications/?tab=tailoring&stage=review#tailored-resume", completed[5]),
        ("interview-preparation", "Open Interview Preparation", preparation_url, completed[6]),
        ("mock-interview", "Complete a short adaptive mock interview", mock_url, completed[7]),
        ("scorecard", "Review the Interview Scorecard", "/interview-review", completed[8]),
        ("action-plan", "See generated actions in the Career Action Plan", "/career-action-plan", completed[9]),
    ]

    return jsonify(
        {
            "completed_count": completed_count,
            "total_count": len(steps),
            "progress_percent": round((completed_count / len(steps)) * 100),
            "current_step": min(current_index + 1, len(steps)),
            "application": {
                "id": selected_application_id,
                "company": str(getattr(selected_application, "company", "") or ""),
                "role": str(getattr(selected_application, "role", "") or ""),
            },
            "steps": [
                {
                    "key": key,
                    "number": index,
                    "title": title,
                    "url": url,
                    "complete": is_complete,
                    "status": "complete" if is_complete else ("current" if index - 1 == current_index else "upcoming"),
                }
                for index, (key, title, url, is_complete) in enumerate(steps, start=1)
            ],
        }
    )


@main_bp.get("/download/desktop-client")
def download_desktop_client():
    """The Windows recorder is intentionally excluded from the Career Bridge MVP."""
    return (
        "The Windows Desktop Recorder is not part of the Career Bridge MVP.",
        410,
        {"Content-Type": "text/plain; charset=utf-8"},
    )
