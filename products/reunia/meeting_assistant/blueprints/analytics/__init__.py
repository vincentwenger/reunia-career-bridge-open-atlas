from flask import Blueprint

analytics_bp = Blueprint("analytics", __name__)

from meeting_assistant.blueprints.analytics import routes
