from flask import Blueprint


actions_bp = Blueprint("actions", __name__)

from meeting_assistant.blueprints.actions import routes  # noqa: E402, F401
