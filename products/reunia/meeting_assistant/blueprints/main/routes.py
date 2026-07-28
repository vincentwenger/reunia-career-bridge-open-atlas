from flask import (
    redirect,
    render_template,
    session,
    url_for,
)

from meeting_assistant.blueprints.main import main_bp

@main_bp.get("/")
def marketing_page():
    """Render the public marketing site or send signed-in users to the app."""
    if session.get("user_id"):
        return redirect(url_for("main.view_index"))
    return render_template("marketing.html")


@main_bp.get("/index.html")
@main_bp.get("/app")
def view_index():
    """Render the authenticated workflow dashboard."""
    if not session.get("user_id"):
        return redirect(url_for("main.marketing_page"))
    return render_template("index.html", desktop_recorder_available=False)


@main_bp.get("/download/desktop-client")
def download_desktop_client():
    """The Windows recorder is intentionally excluded from the Career Bridge MVP."""
    return (
        "The Windows Desktop Recorder is not part of the Career Bridge MVP.",
        410,
        {"Content-Type": "text/plain; charset=utf-8"},
    )
