from flask import Blueprint

recorder_bp = Blueprint("recorder", __name__)

from meeting_assistant.blueprints.recorder import routes
