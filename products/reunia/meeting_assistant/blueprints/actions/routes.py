from __future__ import annotations

from flask import current_app, g, jsonify, redirect, render_template, request, url_for

from meeting_assistant.blueprints.actions import actions_bp
from meeting_assistant.services.action_service import ActionService
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.utils.authentication import api_auth_required, login_required
from meeting_assistant.utils.exceptions import ValidationError


@actions_bp.get("/action-center.html")
@login_required
def legacy_action_center_redirect():
    return redirect(url_for("actions.action_center_page"), code=302)


@actions_bp.get("/career-action-plan")
@login_required
def action_center_page():
    selected_application = None
    application_id = str(request.args.get("application_id") or "").strip()
    application_store = current_app.extensions.get("career_bridge_application_store")
    if application_id and application_store is not None:
        try:
            selected_application = application_store.get(
                g.current_user_id,
                application_id,
                include_resume_bytes=False,
            )
        except Exception:
            current_app.logger.exception("Could not load selected application for action plan")
    return render_template(
        "action-center.html",
        selected_application=selected_application,
    )


@actions_bp.get("/api/career/action-plan/context")
@api_auth_required
def action_plan_context():
    service = ActionService()
    return jsonify(
        {
            "applications": service.list_applications(g.current_user_id),
            "sources": [
                {"value": "resume_gap", "label": "Resume gaps"},
                {"value": "evidence_review", "label": "Evidence-review findings"},
                {"value": "interview_scorecard", "label": "Interview scorecard findings"},
                {"value": "upcoming_interview", "label": "Upcoming interviews"},
                {"value": "application_follow_up", "label": "Application follow-ups"},
                {"value": "application_next_action", "label": "Application next steps"},
                {"value": "manual", "label": "Manual actions"},
            ],
        }
    )


@actions_bp.route(
    "/api/career/actions", methods=["GET", "POST", "PATCH", "PUT", "DELETE"]
)
@actions_bp.route("/api/actions", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
@api_auth_required
def actions_collection():
    service = ActionService()

    if request.method == "GET":
        return jsonify(service.list_for_user(g.current_user_id))

    data = request.get_json(silent=True) or {}
    if request.method == "POST":
        result = service.create(g.current_user_id, data)
        try:
            UsageMetricsService().record_product_event(
                "action_created", g.current_user_id,
                event_id=str(result.get("action_id") or result.get("id") or ""),
            )
        except Exception:
            current_app.logger.exception("Could not record action-created analytics")
        return jsonify(result), 201

    action_id = data.get("action_id") or data.get("id")
    if not action_id:
        raise ValidationError("action_id is required.")

    if request.method == "DELETE":
        return jsonify(service.delete(g.current_user_id, action_id))

    result = service.update(g.current_user_id, action_id, data)
    if str(result.get("status") or "") == "done":
        try:
            UsageMetricsService().record_product_event(
                "action_completed", g.current_user_id, event_id=str(action_id)
            )
        except Exception:
            current_app.logger.exception("Could not record action-completed analytics")
    return jsonify(result)


@actions_bp.route(
    "/api/career/actions/<string:action_id>",
    methods=["GET", "PATCH", "PUT", "DELETE"],
)
@actions_bp.route(
    "/api/actions/<string:action_id>",
    methods=["GET", "PATCH", "PUT", "DELETE"],
)
@api_auth_required
def action_item(action_id: str):
    service = ActionService()

    if request.method == "GET":
        action = next(
            (
                item
                for item in service.list_for_user(g.current_user_id)
                if item["action_id"] == action_id
            ),
            None,
        )
        if action is None:
            from meeting_assistant.utils.exceptions import ResourceNotFoundError

            raise ResourceNotFoundError("Action not found.")
        return jsonify(action)

    if request.method == "DELETE":
        return jsonify(service.delete(g.current_user_id, action_id))

    result = service.update(
        g.current_user_id, action_id, request.get_json(silent=True) or {}
    )
    if str(result.get("status") or "") == "done":
        try:
            UsageMetricsService().record_product_event(
                "action_completed", g.current_user_id, event_id=str(action_id)
            )
        except Exception:
            current_app.logger.exception("Could not record action-completed analytics")
    return jsonify(result)
