from flask import Blueprint

auth_bp = Blueprint("auth", __name__)

from meeting_assistant.blueprints.auth import routes  # noqa: E402, F401
