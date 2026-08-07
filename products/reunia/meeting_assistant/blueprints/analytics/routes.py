from flask import current_app, g, jsonify, redirect, render_template, request, url_for

from meeting_assistant.blueprints.analytics import analytics_bp
from meeting_assistant.services.career_impact_service import CareerImpactService
from meeting_assistant.utils.authentication import api_auth_required, login_required


@analytics_bp.get("/analytics.html")
@login_required
def legacy_analytics_redirect():
    return redirect(url_for("analytics.view_analytics"), code=302)


@analytics_bp.get("/progress")
@login_required
def view_analytics():
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
            current_app.logger.exception("Could not load selected application for progress dashboard")
    return render_template(
        "analytics.html",
        selected_application=selected_application,
    )

@analytics_bp.get("/api/career/impact")
@api_auth_required
def career_impact():
    return jsonify(CareerImpactService().build(g.current_user_id))
