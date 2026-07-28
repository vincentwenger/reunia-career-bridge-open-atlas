from flask import Blueprint

live_qa_bp = Blueprint("live_qa", __name__)

from meeting_assistant.blueprints.live_qa import routes  # noqa: E402, F401
