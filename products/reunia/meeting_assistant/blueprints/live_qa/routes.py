from __future__ import annotations

import json
import time

from flask import Response, current_app, g, render_template, session, stream_with_context

from meeting_assistant.blueprints.live_qa import live_qa_bp
from meeting_assistant.services.live_qa_service import LiveQAService
from meeting_assistant.services.user_service import (
    UserService,
    live_qa_answer_update_profile,
)
from meeting_assistant.utils.authentication import api_auth_required, login_required


@live_qa_bp.get("/live-qa.html")
@login_required
def view_live_qa():
    return render_template("live-qa.html")


@live_qa_bp.get("/stream-ui")
@login_required
def stream_ui_updates():
    current_user = session["user_id"]
    settings = UserService().get_settings(current_user)
    retention_hours = settings.get("retentionHours", 1)
    update_profile = live_qa_answer_update_profile(
        settings.get("liveQaAnswerUpdateFrequency", "efficient")
    )
    active_interval = max(
        0.25,
        float(update_profile["stream_interval_seconds"]),
    )
    idle_interval = max(
        active_interval,
        float(current_app.config.get("LIVE_QA_STREAM_IDLE_INTERVAL_SECONDS", 10.0)),
    )
    active_window = max(
        active_interval,
        float(current_app.config.get("LIVE_QA_STREAM_ACTIVE_WINDOW_SECONDS", 8.0)),
    )
    heartbeat_interval = max(
        idle_interval,
        float(current_app.config.get("LIVE_QA_STREAM_HEARTBEAT_SECONDS", 15.0)),
    )

    def event_stream():
        last_state = None
        last_change_at = time.monotonic()
        last_heartbeat_at = last_change_at
        service = LiveQAService()

        try:
            while True:
                entries = service.list_entries(
                    current_user,
                    retention_hours,
                    max_cache_age_seconds=update_profile["max_cache_age_seconds"],
                )
                serializable_data = [
                    {
                        "id": item["id"],
                        "origin": item.get("origin", ""),
                        "content": item.get("content", ""),
                        "chatgpt_answer": item.get("chatgpt_answer", ""),
                        "timestamp": item.get("timestamp", ""),
                        "answer_source": item.get("answer_source") or {},
                        "answer_origin": item.get("answer_origin", "ai_generated"),
                        "meeting_id": item.get("meeting_id", ""),
                        "meeting_title": item.get("meeting_title", ""),
                    }
                    for item in entries
                ]
                current_state = json.dumps(serializable_data, sort_keys=True)
                now = time.monotonic()

                if current_state != last_state:
                    yield f"data: {current_state}\n\n"
                    last_state = current_state
                    last_change_at = now
                    last_heartbeat_at = now
                elif now - last_heartbeat_at >= heartbeat_interval:
                    # Periodic SSE comments keep proxies from timing out and let the
                    # server notice when a hidden/closed browser tab disconnects.
                    yield ": keep-alive\n\n"
                    last_heartbeat_at = now

                recently_active = now - last_change_at < active_window
                time.sleep(active_interval if recently_active else idle_interval)
        except GeneratorExit:
            # The browser closes the EventSource while the tab is hidden or leaving.
            return

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@live_qa_bp.post("/submit-live-qa")
@api_auth_required
def handle_post():
    service = LiveQAService()
    response = service.submit(g.current_user_id)

    if isinstance(response, str):
        return Response(response, status=200, mimetype="text/plain")

    return Response(stream_with_context(response), mimetype="text/plain")
