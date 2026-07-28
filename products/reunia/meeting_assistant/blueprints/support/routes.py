from __future__ import annotations

from flask import current_app, flash, g, jsonify, redirect, render_template, request, session, url_for

from meeting_assistant.blueprints.support import support_bp
from meeting_assistant.services.support_service import SupportService
from meeting_assistant.utils.authentication import api_auth_required
from meeting_assistant.utils.exceptions import ApplicationError


@support_bp.get("/help-support.html")
def help_support_page():
    user_email = str(
        session.get("email")
        or session.get("user_id")
        or ""
    )

    return render_template(
        "help-support.html",
        support_email=current_app.config.get("SUPPORT_EMAIL", ""),
        support_response_message=current_app.config["SUPPORT_RESPONSE_MESSAGE"],
        support_user_email=user_email,
    )


@support_bp.post("/api/support")
def submit_support_request():
    maximum_request_size = current_app.config["SUPPORT_MAX_ATTACHMENT_BYTES"] + 128 * 1024
    if request.content_length and request.content_length > maximum_request_size:
        return _support_error(
            "The support request is too large. Attachments must be 5 MB or smaller.",
            413,
        )

    service = SupportService()
    try:
        result = service.submit(
            form=request.form,
            attachment=request.files.get("attachment"),
            user_id=str(session["user_id"]) if session.get("user_id") else None,
            # ProxyFix has already resolved the configured trusted proxy hops.
            remote_address=str(request.remote_addr or "").strip(),
            user_agent=request.headers.get("User-Agent", ""),
            page_url=request.form.get("page_url", ""),
        )
    except ApplicationError as exc:
        if _support_wants_json():
            raise
        return _support_error(str(exc), exc.status_code)

    if not result.get("stored"):
        current_app.logger.error("Support service returned without confirming persistence.")
        return _support_error("The support request was not saved. Please try again.", 500)

    if _support_wants_json():
        return jsonify({"success": True, **result}), 201

    flash(result["message"], "success")
    return redirect(url_for("support.help_support_page") + "#contact-support", code=303)


@support_bp.post("/api/support/recorder-error")
@api_auth_required
def submit_recorder_error():
    if request.content_length and request.content_length > 64 * 1024:
        return jsonify({"error": "The recorder error details are too large."}), 413

    payload = request.get_json(silent=True)
    page_url = str(payload.get("page_url") or "") if isinstance(payload, dict) else ""
    service = SupportService()
    try:
        result = service.submit_recorder_error(
            payload=payload,
            user_id=g.current_user_id,
            user_name=str(session.get("full_name") or ""),
            user_email=str(session.get("email") or g.current_user_id),
            remote_address=str(request.remote_addr or "").strip(),
            user_agent=request.headers.get("User-Agent", ""),
            page_url=page_url,
        )
    except ApplicationError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    if not result.get("stored"):
        current_app.logger.error(
            "Automated recorder support request returned without confirming persistence."
        )
        return jsonify({"error": "The error details were not sent. Please try again."}), 500

    return jsonify({"success": True, **result}), 201


def _support_wants_json() -> bool:
    best_match = request.accept_mimetypes.best
    # Preserve the API's historical JSON response when a client sends no
    # Accept header, while allowing the form's native browser fallback to use
    # redirects and flash messages.
    return request.is_json or best_match in {None, "application/json"}


def _support_error(message: str, status_code: int):
    if _support_wants_json():
        return jsonify({"error": message}), status_code
    flash(message, "error")
    return redirect(url_for("support.help_support_page") + "#contact-support", code=303)
