from flask import Blueprint

# Canonical Career Bridge Interview Review routes.
transcript_bp = Blueprint("transcript", __name__)

from meeting_assistant.blueprints.transcripts import routes  # noqa: E402, F401
