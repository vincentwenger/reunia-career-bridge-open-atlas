from flask import Blueprint

# Canonical Career Bridge Adaptive Mock Interview routes.
recorder_bp = Blueprint("recorder", __name__)

from meeting_assistant.blueprints.recorder import routes  # noqa: E402, F401
