from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import current_app, g, jsonify, redirect, session, url_for

from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.admin import is_admin_identity
from meeting_assistant.utils.error_handlers import render_error_page

F = TypeVar("F", bound=Callable[..., Any])
LIVE_INTERVIEW_ASSISTANCE_FEATURE = "live_interview_assistance"


def _normalized_values(value: Any) -> set[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = ()
    return {str(item).strip().lower() for item in items if str(item).strip()}


def configured_live_assistance_groups() -> set[str]:
    return _normalized_values(current_app.config.get("LIVE_INTERVIEW_ASSISTANCE_GROUPS", ()))


def configured_live_assistance_user_ids() -> set[str]:
    return _normalized_values(current_app.config.get("LIVE_INTERVIEW_ASSISTANCE_USER_IDS", ()))


def live_interview_assistance_access(
    user_id: str | None,
    user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_user_id = str(user_id or "").strip().lower()
    user = user or {}
    groups = _normalized_values(user.get("groups") or user.get("access_groups") or ())
    features = user.get("features") if isinstance(user.get("features"), dict) else {}
    override = features.get(LIVE_INTERVIEW_ASSISTANCE_FEATURE)

    if is_admin_identity(normalized_user_id, user):
        return {"enabled": True, "reason": "administrator", "groups": sorted(groups), "override": override}
    if isinstance(override, bool):
        return {
            "enabled": override,
            "reason": "individual_override" if override else "individual_denial",
            "groups": sorted(groups),
            "override": override,
        }
    if normalized_user_id and normalized_user_id in configured_live_assistance_user_ids():
        return {"enabled": True, "reason": "configured_user", "groups": sorted(groups), "override": None}
    matched_groups = groups & configured_live_assistance_groups()
    if matched_groups:
        return {
            "enabled": True,
            "reason": "approved_group",
            "groups": sorted(groups),
            "matched_groups": sorted(matched_groups),
            "override": None,
        }
    return {"enabled": False, "reason": "not_approved", "groups": sorted(groups), "override": None}


def user_has_live_interview_assistance(user_id: str | None) -> bool:
    if not user_id:
        return False
    user = UserService().get_user(str(user_id)) or {}
    return bool(live_interview_assistance_access(str(user_id), user)["enabled"])


def current_user_has_live_interview_assistance() -> bool:
    return user_has_live_interview_assistance(session.get("user_id"))


def live_interview_assistance_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not session.get("user_id"):
            return redirect(url_for("auth.login_page"))
        if not current_user_has_live_interview_assistance():
            return render_error_page(
                error_title="Feature Access Required",
                error_message=(
                    "Live Interview Assistance is currently limited to administrators "
                    "and approved Career Bridge groups."
                ),
                status_code=403,
            ), 403
        return view(*args, **kwargs)

    return cast(F, wrapped)


def live_interview_assistance_api_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        user_id = str(getattr(g, "current_user_id", "") or session.get("user_id") or "").strip()
        if not user_id:
            return jsonify({"error": "Authentication required."}), 401
        if not user_has_live_interview_assistance(user_id):
            return jsonify({
                "error": "Live Interview Assistance is not enabled for this account.",
                "stage": "feature_access",
            }), 403
        return view(*args, **kwargs)

    return cast(F, wrapped)
