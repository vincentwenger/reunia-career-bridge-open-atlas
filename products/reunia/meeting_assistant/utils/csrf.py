from __future__ import annotations

import hmac
import secrets
from typing import Any

from flask import Flask, jsonify, request, session

from meeting_assistant.utils.error_handlers import render_error_page


_SESSION_KEY = "_csrf_token"
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def init_csrf(app: Flask) -> None:
    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    @app.before_request
    def protect_unsafe_requests():
        if not app.config.get("CSRF_ENABLED", True):
            return None
        if request.method.upper() not in _UNSAFE_METHODS:
            return None
        if request.endpoint == "auth.api_get_user" or _uses_api_token():
            return None

        expected = str(session.get(_SESSION_KEY) or "")
        supplied = _submitted_token()
        if expected and supplied and hmac.compare_digest(expected, supplied):
            return None

        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "The security token is missing or expired. Refresh the page and try again."}), 400
        return (
            render_error_page(
                error_title="Request Expired",
                error_message="Refresh the page and submit the form again.",
                status_code=400,
            ),
            400,
        )


def generate_csrf_token() -> str:
    token = str(session.get(_SESSION_KEY) or "")
    if not token:
        token = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def _submitted_token() -> str:
    header = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
    if header:
        return str(header).strip()
    if request.form:
        return str(request.form.get("csrf_token") or "").strip()
    payload: Any = request.get_json(silent=True) if request.is_json else None
    if isinstance(payload, dict):
        return str(payload.get("csrf_token") or "").strip()
    return ""


def _uses_api_token() -> bool:
    authorization = str(request.headers.get("Authorization") or "")
    if authorization.lower().startswith("bearer "):
        return True
    if str(request.headers.get("X-API-Token") or "").strip():
        return True
    payload: Any = request.get_json(silent=True) if request.is_json else None
    return isinstance(payload, dict) and bool(str(payload.get("api_token") or "").strip())
