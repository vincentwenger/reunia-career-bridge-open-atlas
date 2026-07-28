"""Routes for the public User Guide page."""
from flask import render_template

from . import user_guide_bp


@user_guide_bp.get("/user-guide.html")
def user_guide_page():
    """Display the public User Guide without requiring authentication."""
    return render_template("user-guide.html")
