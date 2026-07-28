from __future__ import annotations

from typing import Any

from flask import current_app, g, jsonify, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from meeting_assistant.utils.exceptions import ApplicationError


def render_error_page(
    *,
    error_title: str,
    error_message: str,
    status_code: int,
    reference_id: str | None = None,
    primary_action: dict[str, str] | None = None,
) -> str:
    """Render a recovery-focused HTML error page for the current request."""
    context = _error_page_context(status_code, reference_id=reference_id)
    if primary_action:
        context["primary_action"] = primary_action
    return render_template(
        "error.html",
        error_title=error_title,
        error_message=error_message,
        error_status_code=status_code,
        **context,
    )


def current_request_reference() -> str:
    recorder_reference = str(request.headers.get("X-Recorder-Reference") or "").strip()
    return recorder_reference or str(getattr(g, "request_id", "unavailable"))


def register_error_handlers(app) -> None:
    @app.errorhandler(ApplicationError)
    def handle_application_error(error: ApplicationError):
        if _wants_json():
            return jsonify({"error": str(error)}), error.status_code
        return render_error_page(
            error_title="Request Error",
            error_message=str(error),
            status_code=error.status_code,
            reference_id=(current_request_reference() if error.status_code >= 500 else None),
        ), error.status_code

    @app.errorhandler(413)
    def handle_request_too_large(error):
        message = (
            "The uploaded recording is too large. "
            "Record a shorter meeting and try again."
        )
        reference_id = current_request_reference()
        if _wants_json():
            return jsonify({
                "error": message,
                "reference_id": reference_id,
                "stage": "uploading",
            }), 413
        return render_error_page(
            error_title="Recording Too Large",
            error_message=message,
            status_code=413,
            reference_id=reference_id,
        ), 413

    @app.errorhandler(404)
    def handle_not_found(error):
        if _wants_json():
            return jsonify({"error": "Not found."}), 404
        return render_error_page(
            error_title="Page Not Found",
            error_message="The requested page could not be found.",
            status_code=404,
        ), 404

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        status_code = int(error.code or 500)
        if _wants_json():
            return jsonify({"error": error.description}), status_code
        return render_error_page(
            error_title=error.name,
            error_message=error.description,
            status_code=status_code,
            reference_id=(current_request_reference() if status_code >= 500 else None),
        ), status_code

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        reference_id = current_request_reference()
        current_app.logger.exception(
            "Unhandled application error reference=%s",
            reference_id,
            exc_info=error,
        )
        if _wants_json():
            return jsonify({
                "error": "An unexpected server error occurred.",
                "reference_id": reference_id,
            }), 500
        return render_error_page(
            error_title="System Error",
            error_message="An unexpected server error occurred. Please try again later.",
            status_code=500,
            reference_id=reference_id,
        ), 500


def _link_action(label: str, href: str) -> dict[str, str]:
    return {"kind": "link", "label": label, "href": href}


def _back_action(label: str, fallback_url: str) -> dict[str, str]:
    return {"kind": "back", "label": label, "fallback_url": fallback_url}


def _error_page_context(status_code: int, *, reference_id: str | None) -> dict[str, Any]:
    signed_in = bool(session.get("user_id"))
    home_url = url_for("main.view_index") if signed_in else url_for("main.marketing_page")
    login_url = url_for("auth.login_page")
    support_url = url_for("support.help_support_page") + "#contact-support"
    recorder_url = url_for("recorder.view_recorder") if signed_in else login_url

    primary_action = _link_action("Go Home", home_url)
    secondary_actions = [
        _back_action("Go Back", home_url),
        _link_action("Open Help & Support", support_url),
    ]
    recovery_title = "Use a safe route back into Réunia."
    recovery_description = "Choose one of the recovery actions for this error."
    recovery_items = [
        {
            "icon": "←",
            "title": "Return safely",
            "text": "Use Go Back only when the previous page belongs to Réunia.",
        },
        {
            "icon": "?",
            "title": "Ask for help",
            "text": "Open Help & Support if the same error continues.",
        },
    ]

    if status_code == 400:
        primary_action = _back_action("Go Back", home_url)
        recovery_title = "The request needs to be submitted again."
        recovery_description = "Return to the previous Réunia page, review the information, and retry."
        recovery_items[0] = {
            "icon": "↻",
            "title": "Refresh the form",
            "text": "Open the form again before submitting new information.",
        }
    elif status_code == 401:
        primary_action = _link_action("Return to Login", login_url)
        recovery_title = "Sign in to continue."
        recovery_description = "Your session may have expired or this page requires authentication."
        recovery_items[0] = {
            "icon": "🔐",
            "title": "Protect your account",
            "text": "Sign in again, then return to the feature you were using.",
        }
    elif status_code == 403:
        recovery_title = "This account does not have access."
        recovery_description = "Return home or ask an administrator if you believe access should be available."
        recovery_items[0] = {
            "icon": "🔒",
            "title": "Check your access",
            "text": "Make sure you are signed in with the intended Réunia account.",
        }
    elif status_code == 404:
        recovery_title = "The page may have moved."
        recovery_description = "Check the address or return to a known Réunia page."
        recovery_items[0] = {
            "icon": "⌨",
            "title": "Check the address",
            "text": "Make sure the web address is complete and spelled correctly.",
        }
    elif status_code == 413:
        primary_action = _link_action(
            "Start a new recording" if signed_in else "Return to Login",
            recorder_url,
        )
        recovery_title = "The recording is larger than allowed."
        recovery_description = "Return to the recorder and create a shorter recording before trying again."
        recovery_items[0] = {
            "icon": "◷",
            "title": "Record a shorter meeting",
            "text": "Split a long recording into smaller sessions before uploading.",
        }
    elif status_code == 429:
        primary_action = _back_action("Go Back", home_url)
        recovery_title = "Please wait before trying again."
        recovery_description = "Too many requests were received in a short period."
        recovery_items[0] = {
            "icon": "◷",
            "title": "Wait and retry",
            "text": "Keep this page open and retry after the waiting period.",
        }
    elif status_code >= 500:
        secondary_actions = [
            _link_action("Open Help & Support", support_url),
            _back_action("Go Back", home_url),
        ]
        recovery_title = "Réunia is temporarily unavailable."
        recovery_description = "Your information was not intentionally discarded. Return home or contact support."
        recovery_items[0] = {
            "icon": "↻",
            "title": "Try again later",
            "text": "Return home and retry the action after a short wait.",
        }

    return {
        "primary_action": primary_action,
        "secondary_actions": secondary_actions,
        "recovery_title": recovery_title,
        "recovery_description": recovery_description,
        "recovery_items": recovery_items,
        "error_reference_id": reference_id,
    }


def _wants_json() -> bool:
    return (
        request.path.startswith("/api/")
        or request.path.startswith("/submit-")
        or request.is_json
    )
