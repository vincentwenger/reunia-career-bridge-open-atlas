from __future__ import annotations

from flask import Response, redirect, url_for

from meeting_assistant.blueprints.live_qa import live_qa_bp
from meeting_assistant.utils.authentication import api_auth_required, login_required


_RETIRED_MESSAGE = (
    "Live Q&A is not available in Career Bridge. "
    "Use Mock Interview for practice and Interview Review for coaching."
)


@live_qa_bp.get("/live-qa.html")
@login_required
def view_live_qa():
    """Send legacy UI links to the supported mock-interview workflow."""
    return redirect(url_for("recorder.view_recorder"))


@live_qa_bp.get("/stream-ui")
@login_required
def stream_ui_updates():
    """Retire candidate-facing real-time answer streaming."""
    return Response(_RETIRED_MESSAGE, status=410, mimetype="text/plain")


@live_qa_bp.post("/submit-live-qa")
@api_auth_required
def handle_post():
    """Reject legacy real-interview answer requests with an explicit boundary."""
    return Response(_RETIRED_MESSAGE, status=410, mimetype="text/plain")
