from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import current_app, jsonify, redirect, session, url_for

from meeting_assistant.utils.error_handlers import render_error_page

F = TypeVar("F", bound=Callable[..., Any])


def configured_admin_user_ids() -> set[str]:
    configured = current_app.config.get("ADMIN_USER_IDS", ())
    if isinstance(configured, str):
        values = configured.split(",")
    else:
        values = configured or ()
    return {str(value).strip().lower() for value in values if str(value).strip()}


def is_admin_identity(user_id: str | None, user: dict[str, Any] | None = None) -> bool:
    normalized = str(user_id or "").strip().lower()
    if not normalized:
        return False
    if normalized in configured_admin_user_ids():
        return True
    return bool((user or {}).get("is_admin"))


def current_session_is_admin() -> bool:
    user_id = session.get("user_id")
    if not user_id:
        return False
    return bool(session.get("is_admin")) or is_admin_identity(str(user_id))


def admin_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not session.get("user_id"):
            return redirect(url_for("auth.login_page"))
        if not current_session_is_admin():
            return render_error_page(
                error_title="Administrator Access Required",
                error_message="This page is available only to Réunia administrators.",
                status_code=403,
            ), 403
        return view(*args, **kwargs)

    return cast(F, wrapped)


def admin_api_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not session.get("user_id"):
            return jsonify({"error": "Authentication required."}), 401
        if not current_session_is_admin():
            return jsonify({"error": "Administrator access required."}), 403
        return view(*args, **kwargs)

    return cast(F, wrapped)
