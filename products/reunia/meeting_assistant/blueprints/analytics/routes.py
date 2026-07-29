from flask import g, jsonify, redirect, render_template, url_for

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
    return render_template("analytics.html")

@analytics_bp.get("/api/career/impact")
@api_auth_required
def career_impact():
    return jsonify(CareerImpactService().build(g.current_user_id))
