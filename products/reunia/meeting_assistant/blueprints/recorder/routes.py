from __future__ import annotations

from flask import g, jsonify, render_template, request, session, url_for

from meeting_assistant.blueprints.recorder import recorder_bp
from meeting_assistant.services.mock_interview_service import MockInterviewService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.authentication import api_auth_required, login_required


@recorder_bp.get("/mock-interview")
@login_required
def view_recorder():
    return render_template(
        "meeting-recorder.html",
        desktop_recorder_available=False,
    )



@recorder_bp.get("/api/career/mock-interviews/types")
@api_auth_required
def list_mock_interview_types():
    return jsonify({"interview_types": MockInterviewService.interview_types()})


@recorder_bp.get("/api/career/mock-interviews/applications")
@api_auth_required
def list_mock_interview_applications():
    applications = MockInterviewService().list_application_options(g.current_user_id)
    active_application_id = str(session.get("active_application_id") or "").strip()
    preferred_id = f"builder:{active_application_id}" if active_application_id else ""
    if preferred_id and not any(item.get("id") == preferred_id for item in applications):
        preferred_id = ""
    return jsonify(
        {
            "applications": applications,
            "active_application_context_id": preferred_id,
        }
    )


@recorder_bp.get("/api/career/mock-interviews/question-sets")
@api_auth_required
def list_mock_interview_question_sets():
    return jsonify(
        {
            "question_sets": UserService().list_mock_interview_question_sets(
                g.current_user_id
            )
        }
    )


@recorder_bp.post("/api/career/mock-interviews/question-sets")
@api_auth_required
def save_mock_interview_question_set():
    payload = request.get_json(silent=True) or {}
    saved = UserService().save_mock_interview_question_set(
        g.current_user_id,
        payload,
    )
    return jsonify({"question_set": saved}), 201


@recorder_bp.delete("/api/career/mock-interviews/question-sets/<question_set_id>")
@api_auth_required
def delete_mock_interview_question_set(question_set_id: str):
    return jsonify(
        UserService().delete_mock_interview_question_set(
            g.current_user_id,
            question_set_id,
        )
    )


@recorder_bp.post("/api/career/mock-interviews/adaptive/sessions")
@api_auth_required
def create_adaptive_mock_interview_session():
    payload = request.get_json(silent=True) or {}
    result = MockInterviewService().create_session(g.current_user_id, payload)
    result.update(
        {
            "answer_url": url_for(
                "recorder.submit_adaptive_mock_interview_answer",
                session_id=result["session_id"],
            ),
            "skip_url": url_for(
                "recorder.skip_adaptive_mock_interview_question",
                session_id=result["session_id"],
            ),
            "complete_url": url_for(
                "recorder.complete_adaptive_mock_interview",
                session_id=result["session_id"],
            ),
            "status_url": url_for(
                "recorder.get_adaptive_mock_interview",
                session_id=result["session_id"],
            ),
            "discard_url": url_for(
                "recorder.discard_adaptive_mock_interview",
                session_id=result["session_id"],
            ),
            "review_url": url_for("transcript.view_transcripts"),
        }
    )
    return jsonify(result), 201


@recorder_bp.post("/api/career/mock-interviews/adaptive/sessions/<session_id>/answers")
@api_auth_required
def submit_adaptive_mock_interview_answer(session_id: str):
    result = MockInterviewService().submit_answer(
        g.current_user_id,
        session_id,
        request.files.get("answer_audio"),
        language=request.form.get("language", ""),
        duration_seconds=request.form.get("duration_seconds"),
    )
    result.update(
        {
            "answer_url": url_for(
                "recorder.submit_adaptive_mock_interview_answer",
                session_id=session_id,
            ),
            "skip_url": url_for(
                "recorder.skip_adaptive_mock_interview_question",
                session_id=session_id,
            ),
            "complete_url": url_for(
                "recorder.complete_adaptive_mock_interview",
                session_id=session_id,
            ),
            "status_url": url_for(
                "recorder.get_adaptive_mock_interview",
                session_id=session_id,
            ),
            "discard_url": url_for(
                "recorder.discard_adaptive_mock_interview",
                session_id=session_id,
            ),
            "review_url": url_for("transcript.view_transcripts"),
        }
    )
    return jsonify(result)


@recorder_bp.post("/api/career/mock-interviews/adaptive/sessions/<session_id>/skip")
@api_auth_required
def skip_adaptive_mock_interview_question(session_id: str):
    result = MockInterviewService().skip_question(g.current_user_id, session_id)
    result.update(
        {
            "answer_url": url_for(
                "recorder.submit_adaptive_mock_interview_answer",
                session_id=session_id,
            ),
            "skip_url": url_for(
                "recorder.skip_adaptive_mock_interview_question",
                session_id=session_id,
            ),
            "complete_url": url_for(
                "recorder.complete_adaptive_mock_interview",
                session_id=session_id,
            ),
            "status_url": url_for(
                "recorder.get_adaptive_mock_interview",
                session_id=session_id,
            ),
            "discard_url": url_for(
                "recorder.discard_adaptive_mock_interview",
                session_id=session_id,
            ),
            "review_url": url_for("transcript.view_transcripts"),
        }
    )
    return jsonify(result)


@recorder_bp.post("/api/career/mock-interviews/adaptive/sessions/<session_id>/complete")
@api_auth_required
def complete_adaptive_mock_interview(session_id: str):
    result = MockInterviewService().complete_session(g.current_user_id, session_id)
    result["review_url"] = url_for("transcript.view_transcripts")
    return jsonify(result), 201


@recorder_bp.get("/api/career/mock-interviews/adaptive/sessions/<session_id>")
@api_auth_required
def get_adaptive_mock_interview(session_id: str):
    result = MockInterviewService().get_session(g.current_user_id, session_id)
    result.update(
        {
            "answer_url": url_for(
                "recorder.submit_adaptive_mock_interview_answer",
                session_id=session_id,
            ),
            "skip_url": url_for(
                "recorder.skip_adaptive_mock_interview_question",
                session_id=session_id,
            ),
            "complete_url": url_for(
                "recorder.complete_adaptive_mock_interview",
                session_id=session_id,
            ),
            "discard_url": url_for(
                "recorder.discard_adaptive_mock_interview",
                session_id=session_id,
            ),
            "review_url": url_for("transcript.view_transcripts"),
        }
    )
    return jsonify(result)


@recorder_bp.delete("/api/career/mock-interviews/adaptive/sessions/<session_id>")
@api_auth_required
def discard_adaptive_mock_interview(session_id: str):
    return jsonify(
        MockInterviewService().discard_session(g.current_user_id, session_id)
    )
