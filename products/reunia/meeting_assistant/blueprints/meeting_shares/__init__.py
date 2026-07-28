from flask import Blueprint

meeting_shares_bp = Blueprint("meeting_shares", __name__)

from meeting_assistant.blueprints.meeting_shares import routes  # noqa: E402, F401
