from flask import current_app, g, jsonify, redirect, render_template, request, url_for

from meeting_assistant.blueprints.transcripts import transcript_bp
from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.utils.authentication import api_auth_required, login_required


@transcript_bp.get("/meeting-review.html")
@login_required
def legacy_review_redirect():
    return redirect(url_for("transcript.view_transcripts"), code=302)


@transcript_bp.get("/interview-review")
@login_required
def view_transcripts():
    return render_template("meeting-review.html")


@transcript_bp.route(
    "/api/career/interview-reviews",
    methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
)
@transcript_bp.route("/api/transcripts", methods=["GET", "POST", "DELETE", "PATCH", "PUT"])
@api_auth_required
def transcripts_collection():
    service = TranscriptService()

    if request.method == "GET":
        return jsonify(service.list_for_user(g.current_user_id))

    data = request.get_json(silent=True) or {}

    if request.method == "POST":
        result = service.create(g.current_user_id, data)
        try:
            UsageMetricsService().record_product_event(
                "meeting_processing_succeeded", g.current_user_id,
                event_id=str(result.get("meeting_id") or data.get("meeting_id") or ""),
                metadata={"source": "transcript_api"},
            )
        except Exception:
            current_app.logger.exception("Could not record saved-meeting analytics")
        return jsonify(result), 201

    meeting_id = data.get("meeting_id") or data.get("transcript_id") or data.get("id")
    timestamp = data.get("timestamp") or data.get("date")

    if request.method == "DELETE":
        return jsonify(service.delete(g.current_user_id, meeting_id, timestamp))

    return jsonify(service.update(g.current_user_id, meeting_id, timestamp, data))


@transcript_bp.route(
    "/api/career/interview-reviews/<string:meeting_id>",
    methods=["DELETE", "PATCH", "PUT"],
)
@transcript_bp.route(
    "/api/transcripts/<string:meeting_id>",
    methods=["DELETE", "PATCH", "PUT"],
)
@api_auth_required
def modify_transcript(meeting_id: str):
    service = TranscriptService()
    data = request.get_json(silent=True) or {}
    timestamp = request.args.get("timestamp") or data.get("timestamp") or data.get("date")

    if request.method == "DELETE":
        return jsonify(service.delete(g.current_user_id, meeting_id, timestamp))

    return jsonify(service.update(g.current_user_id, meeting_id, timestamp, data))


@transcript_bp.patch("/api/career/interview-review-topics")
@transcript_bp.patch("/api/transcript-topics")
@api_auth_required
def manage_transcript_topics():
    data = request.get_json(silent=True) or {}
    return jsonify(TranscriptService().manage_topics(g.current_user_id, data))


@transcript_bp.post("/submit-transcript")
@api_auth_required
def submit_transcript():
    """Retired desktop-recorder compatibility endpoint."""
    return jsonify(
        {
            "error": "The Windows Desktop Recorder is not part of the Career Bridge MVP.",
            "replacement": "/mock-interview",
        }
    ), 410
