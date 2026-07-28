from __future__ import annotations

from functools import wraps

from flask import g, jsonify, redirect, request, session, url_for, current_app

from meeting_assistant.services.authentication_service import AuthenticationService
from meeting_assistant.utils.api_tokens import verify_api_token
from meeting_assistant.utils.exceptions import ApplicationError


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id"):
            return view(*args, **kwargs)
        return redirect(url_for("auth.login_page"))

    return wrapped


def api_auth_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = _resolve_api_user()
        if not user_id:
            return jsonify({"error": "Authentication required."}), 401
        g.current_user_id = user_id
        return view(*args, **kwargs)

    return wrapped


def _resolve_api_user() -> str | None:
    if session.get("user_id"):
        return str(session["user_id"])

    authorization = request.headers.get("Authorization", "")
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization.split(None, 1)[1].strip()
    token = token or request.headers.get("X-API-Token", "").strip()

    data = request.get_json(silent=True) if request.is_json else {}
    data = data or {}
    token = token or str(data.get("api_token") or "").strip()
    if token:
        return verify_api_token(token)

    if current_app.config.get("ALLOW_PASSWORD_AUTH_IN_API_BODY"):
        user_id = str(data.get("user_id") or "").strip()
        password = data.get("password") or ""
        if user_id and password:
            try:
                user = AuthenticationService().authenticate(user_id, password)
                return str(user["user_id"])
            except ApplicationError:
                return None

    return None
