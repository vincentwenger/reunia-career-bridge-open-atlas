from flask import Blueprint

# Canonical Career Bridge profile, evidence, and application-material routes.
knowledge_bp = Blueprint("knowledge", __name__)

from meeting_assistant.blueprints.knowledge import routes  # noqa: E402, F401
