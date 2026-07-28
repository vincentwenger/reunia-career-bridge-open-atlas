from flask import Blueprint

main_bp = Blueprint("main", __name__)

from meeting_assistant.blueprints.main import routes  # noqa: E402, F401
