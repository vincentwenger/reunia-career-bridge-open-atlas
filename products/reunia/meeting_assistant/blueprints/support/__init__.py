from flask import Blueprint

support_bp = Blueprint("support", __name__)

from . import routes  # noqa: E402, F401
