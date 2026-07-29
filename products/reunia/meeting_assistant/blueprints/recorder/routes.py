from __future__ import annotations

import json

from flask import current_app, g, jsonify, render_template, request, session, url_for

from meeting_assistant.blueprints.recorder import recorder_bp
from meeting_assistant.services.browser_recorder_job_service import BrowserRecorderJobService
from meeting_assistant.services.browser_recorder_live_service import BrowserRecorderLiveService
from meeting_assistant.services.mock_interview_service import MockInterviewService
from meeting_assistant.utils.authentication import api_auth_required, login_required
from meeting_assistant.utils.exceptions import ApplicationError
from meeting_assistant.utils.feature_access import (
    current_user_has_live_interview_assistance,
    live_interview_assistance_api_required,
)


@recorder_bp.get("/meeting-recorder.html")
@recorder_bp.get("/meeting-recorder")
@recorder_bp.get("/mock-interview")
@login_required
def view_recorder():
    allowed = current_user_has_live_interview_assistance()
    return render_template(
        "meeting-recorder.html",
        desktop_recorder_available=False,
        live_interview_assistance_enabled=allowed,
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

def _prepared_meeting_from_form() -> dict[str, object]:
    participants_raw = str(request.form.get("prepared_meeting_participants") or "").strip()
    try:
        participants = json.loads(participants_raw) if participants_raw else []
    except json.JSONDecodeError:
        participants = [value.strip() for value in participants_raw.split(",") if value.strip()]
    if not isinstance(participants, list):
        participants = []
    return {
        "id": str(request.form.get("prepared_meeting_id") or "").strip(),
        "title": str(request.form.get("prepared_meeting_title") or "").strip(),
        "scheduled_at": str(request.form.get("prepared_meeting_scheduled_at") or "").strip(),
        "participants": [str(value).strip() for value in participants if str(value).strip()],
        "purpose": str(request.form.get("prepared_meeting_purpose") or "").strip(),
    }


def _recorder_error_response(exc: ApplicationError, reference_id: str, stage: str):
    current_app.logger.warning(
        "Recorder request rejected reference=%s stage=%s status_code=%s error=%s",
        reference_id or "unavailable",
        stage,
        exc.status_code,
        exc,
    )
    return jsonify(
        {
            "error": str(exc),
            "reference_id": reference_id or "unavailable",
            "stage": stage,
        }
    ), exc.status_code


@recorder_bp.post("/api/meeting-recorder/sessions")
@recorder_bp.post("/api/career/mock-interviews/sessions")
@api_auth_required
def create_recording_session():
    requested_reference_id = str(
        request.form.get("client_reference_id")
        or request.headers.get("X-Recorder-Reference")
        or ""
    ).strip()
    try:
        result = BrowserRecorderJobService().create_upload_session(
            user_id=g.current_user_id,
            started_at=request.form.get("started_at", ""),
            requested_reference_id=requested_reference_id,
            prepared_meeting=_prepared_meeting_from_form(),
            language=request.form.get("language", ""),
        )
    except ApplicationError as exc:
        return _recorder_error_response(exc, requested_reference_id, "creating_upload_session")
    job_id = result["job_id"]
    result.update(
        {
            "segment_url": url_for("recorder.upload_recording_segment", job_id=job_id),
            "finalize_url": url_for("recorder.finalize_recording_session", job_id=job_id),
            "discard_url": url_for("recorder.discard_recording_session", job_id=job_id),
            "status_url": url_for("recorder.get_recording_job", job_id=job_id),
            "review_url": url_for("transcript.view_transcripts"),
        }
    )
    return jsonify(result), 201


@recorder_bp.post("/api/meeting-recorder/sessions/<job_id>/segments")
@recorder_bp.post("/api/career/mock-interviews/sessions/<job_id>/segments")
@api_auth_required
def upload_recording_segment(job_id: str):
    try:
        result = BrowserRecorderJobService().append_segment(
            job_id=job_id,
            user_id=g.current_user_id,
            source=request.form.get("source", ""),
            sequence=request.form.get("sequence", ""),
            offset_seconds=request.form.get("offset_seconds", ""),
            duration_seconds=request.form.get("duration_seconds", ""),
            audio_segment=request.files.get("audio_segment"),
        )
    except ApplicationError as exc:
        return _recorder_error_response(exc, job_id, "uploading_segment")
    return jsonify(result), 201 if result.get("status") == "uploaded" else 200


@recorder_bp.post("/api/meeting-recorder/sessions/<job_id>/finalize")
@recorder_bp.post("/api/career/mock-interviews/sessions/<job_id>/finalize")
@api_auth_required
def finalize_recording_session(job_id: str):
    try:
        result = BrowserRecorderJobService().finalize_upload_session(
            job_id=job_id,
            user_id=g.current_user_id,
            duration_seconds=request.form.get("duration_seconds", ""),
        )
    except ApplicationError as exc:
        return _recorder_error_response(exc, job_id, "finalizing_upload")
    result.update(
        {
            "status_url": url_for("recorder.get_recording_job", job_id=job_id),
            "review_url": url_for("transcript.view_transcripts"),
        }
    )
    return jsonify(result), 202


@recorder_bp.delete("/api/meeting-recorder/sessions/<job_id>")
@recorder_bp.delete("/api/career/mock-interviews/sessions/<job_id>")
@api_auth_required
def discard_recording_session(job_id: str):
    try:
        BrowserRecorderJobService().discard_upload_session(
            job_id=job_id,
            user_id=g.current_user_id,
        )
    except ApplicationError as exc:
        return _recorder_error_response(exc, job_id, "discarding")
    return jsonify({"status": "discarded", "job_id": job_id})


@recorder_bp.post("/api/meeting-recorder/jobs/<job_id>/retry")
@recorder_bp.post("/api/career/mock-interviews/jobs/<job_id>/retry")
@api_auth_required
def retry_recording_job(job_id: str):
    try:
        result = BrowserRecorderJobService().retry_job(
            job_id=job_id,
            user_id=g.current_user_id,
        )
    except ApplicationError as exc:
        return _recorder_error_response(exc, job_id, "retrying")
    result.update(
        {
            "status_url": url_for("recorder.get_recording_job", job_id=job_id),
            "review_url": url_for("transcript.view_transcripts"),
        }
    )
    return jsonify(result), 202


@recorder_bp.post("/api/meeting-recorder")
@recorder_bp.post("/api/career/mock-interviews")
@api_auth_required
def submit_recording():
    requested_reference_id = str(
        request.form.get("client_reference_id")
        or request.headers.get("X-Recorder-Reference")
        or ""
    ).strip()

    try:
        result = BrowserRecorderJobService().queue_meeting(
            user_id=g.current_user_id,
            started_at=request.form.get("started_at", ""),
            microphone_audio=request.files.get("microphone_audio"),
            speaker_audio=request.files.get("speaker_audio"),
            requested_reference_id=requested_reference_id,
            prepared_meeting=_prepared_meeting_from_form(),
            language=request.form.get("language", ""),
        )
    except ApplicationError as exc:
        reference_id = requested_reference_id or "unavailable"
        current_app.logger.warning(
            "Recorder upload rejected reference=%s status_code=%s error=%s",
            reference_id,
            exc.status_code,
            exc,
        )
        return jsonify(
            {
                "error": str(exc),
                "reference_id": reference_id,
                "stage": "uploading",
            }
        ), exc.status_code
    except Exception as exc:  # pragma: no cover - defensive request boundary
        reference_id = requested_reference_id or "unavailable"
        current_app.logger.exception(
            "Recorder upload failed reference=%s", reference_id, exc_info=exc
        )
        return jsonify(
            {
                "error": "An unexpected server error occurred while uploading the recording.",
                "reference_id": reference_id,
                "stage": "uploading",
            }
        ), 500

    job_id = result["job_id"]
    result.update(
        {
            "message": "Recording uploaded. Processing has started.",
            "status_url": url_for("recorder.get_recording_job", job_id=job_id),
            "review_url": url_for("transcript.view_transcripts"),
        }
    )
    return jsonify(result), 202


@recorder_bp.post("/api/meeting-recorder/live-chunk")
@api_auth_required
@live_interview_assistance_api_required
def submit_live_chunk():
    recording_id = str(request.form.get("recording_id") or "").strip()
    chunk_id = str(request.form.get("chunk_id") or "").strip()

    try:
        result = BrowserRecorderLiveService().process_chunk(
            user_id=g.current_user_id,
            recording_id=recording_id,
            chunk_id=chunk_id,
            source=request.form.get("source", ""),
            sequence=request.form.get("sequence", ""),
            audio_chunk=request.files.get("audio_chunk"),
            prepared_meeting_id=request.form.get("prepared_meeting_id", ""),
            previous_transcript=request.form.get("previous_transcript", ""),
            question_context=request.form.get("question_context", ""),
            language=request.form.get("language", ""),
            elapsed_seconds=request.form.get("elapsed_seconds", "0"),
        )
    except ApplicationError as exc:
        current_app.logger.warning(
            "Live recorder chunk rejected recording=%s chunk=%s status_code=%s error=%s",
            recording_id or "unavailable",
            chunk_id or "unavailable",
            exc.status_code,
            exc,
        )
        return jsonify({
            "error": str(exc),
            "recording_id": recording_id,
            "chunk_id": chunk_id,
            "stage": "live_qa",
        }), exc.status_code
    except Exception as exc:  # pragma: no cover - defensive request boundary
        current_app.logger.exception(
            "Live recorder chunk failed recording=%s chunk=%s",
            recording_id or "unavailable",
            chunk_id or "unavailable",
            exc_info=exc,
        )
        return jsonify({
            "error": "The live audio chunk could not be processed.",
            "recording_id": recording_id,
            "chunk_id": chunk_id,
            "stage": "live_qa",
        }), 500

    return jsonify(result)


@recorder_bp.delete("/api/meeting-recorder/live-session/<recording_id>")
@api_auth_required
@live_interview_assistance_api_required
def cancel_live_session(recording_id: str):
    try:
        BrowserRecorderLiveService.cancel_session(
            user_id=g.current_user_id,
            recording_id=recording_id,
        )
    except ApplicationError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    return jsonify({"status": "cancelled", "recording_id": recording_id})


@recorder_bp.get("/api/meeting-recorder/jobs/<job_id>")
@recorder_bp.get("/api/career/mock-interviews/jobs/<job_id>")
@api_auth_required
def get_recording_job(job_id: str):
    result = BrowserRecorderJobService().get_job(
        job_id=job_id,
        user_id=g.current_user_id,
    )
    result["review_url"] = url_for("transcript.view_transcripts")
    return jsonify(result)
