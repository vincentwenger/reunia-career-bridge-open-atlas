"""User Guide Blueprint."""
from flask import Blueprint

user_guide_bp = Blueprint("user_guide", __name__)

from . import routes  # noqa: E402, F401
