from flask import Blueprint

knowledge_bp = Blueprint("knowledge", __name__)

from meeting_assistant.blueprints.knowledge import routes
