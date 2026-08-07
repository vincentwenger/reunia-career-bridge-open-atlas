from flask import jsonify, render_template, request, session

from meeting_assistant.blueprints.users import users_bp
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.authentication import login_required


@users_bp.get("/profile.html")
@login_required
def profile_page():
    user = UserService().get_user(session["user_id"]) or {}
    return render_template("profile.html", user=user)


@users_bp.post("/update-profile")
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    updated = UserService().update_profile(session["user_id"], data)
    if "full_name" in updated:
        session["full_name"] = updated["full_name"]
    return jsonify(
        {
            "message": "Profile updated successfully!",
            "updated_attributes": updated,
        }
    )


@users_bp.get("/settings.html")
@login_required
def settings_page():
    settings = UserService().get_settings(session["user_id"])
    return render_template("settings.html", settings=settings)


@users_bp.get("/api/settings")
@login_required
def get_settings():
    settings = UserService().get_settings(session["user_id"])
    return jsonify({"settings": settings})


@users_bp.post("/update-settings")
@login_required
def update_settings():
    data = request.get_json(silent=True) or {}
    updated = UserService().update_settings(session["user_id"], data)
    session["language"] = updated.get("language", "en")
    session.pop("cached_settings", None)
    return jsonify(
        {
            "success": True,
            "language": session["language"],
            "settings": updated,
        }
    )
