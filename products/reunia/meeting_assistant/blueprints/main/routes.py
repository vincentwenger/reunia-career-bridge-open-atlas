from pathlib import Path

from flask import (
    abort,
    current_app,
    redirect,
    render_template,
    send_from_directory,
    session,
    url_for,
)

from meeting_assistant.blueprints.main import main_bp
from meeting_assistant.services.admin_analytics_service import UsageMetricsService


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
    desktop_installer = Path(current_app.static_folder or "") / "ReuniaSetup.exe"
    return render_template(
        "index.html",
        desktop_recorder_available=desktop_installer.is_file(),
    )


@main_bp.get("/download/desktop-client")
def download_desktop_client():
    """Download the Windows recorder and count known-account downloads."""
    installer_name = "ReuniaSetup.exe"
    static_folder = Path(current_app.static_folder or "")
    installer_path = static_folder / installer_name
    if not installer_path.is_file():
        abort(404)

    user_id = str(session.get("user_id") or "").strip()
    if user_id:
        try:
            UsageMetricsService().record_desktop_client_download(user_id)
        except Exception:
            # Analytics must never prevent the user from downloading the client.
            current_app.logger.exception(
                "Could not record desktop client download for %s",
                user_id,
            )

    return send_from_directory(
        static_folder,
        installer_name,
        as_attachment=True,
        download_name=installer_name,
    )
