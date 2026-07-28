from flask import render_template

from meeting_assistant.blueprints.analytics import analytics_bp
from meeting_assistant.utils.authentication import login_required


@analytics_bp.get("/progress")
@analytics_bp.get("/analytics.html")
@login_required
def view_analytics():
    return render_template("analytics.html")