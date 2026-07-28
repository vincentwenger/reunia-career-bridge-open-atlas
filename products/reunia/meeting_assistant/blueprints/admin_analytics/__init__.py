from flask import Blueprint

admin_analytics_bp = Blueprint("admin_analytics", __name__)

from meeting_assistant.blueprints.admin_analytics import routes  # noqa: E402,F401
