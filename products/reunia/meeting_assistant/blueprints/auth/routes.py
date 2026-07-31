from flask import (
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from meeting_assistant.blueprints.auth import auth_bp
from meeting_assistant.i18n import normalize_language
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.services.authentication_service import AuthenticationService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.admin import is_admin_identity
from meeting_assistant.utils.api_tokens import generate_api_token
from meeting_assistant.utils.error_handlers import render_error_page
from meeting_assistant.utils.exceptions import AuthenticationError, ValidationError
from meeting_assistant.utils.feature_access import live_interview_assistance_access


def _rate_limit(scope: str, identity: str, *, count_key: str, window_key: str):
    limiter = current_app.extensions["rate_limiter"]
    client_ip = str(request.remote_addr or "unknown")
    normalized_identity = identity.strip().lower() or "missing"
    limit = int(current_app.config[count_key])
    window_seconds = int(current_app.config[window_key])
    results = (
        limiter.hit(
            f"{scope}:ip:{client_ip}",
            limit=limit,
            window_seconds=window_seconds,
        ),
        limiter.hit(
            f"{scope}:identity:{normalized_identity}",
            limit=limit,
            window_seconds=window_seconds,
        ),
    )
    allowed = all(result[0] for result in results)
    retry_after = max(result[1] for result in results if not result[0]) if not allowed else 0
    return allowed, retry_after


def _too_many_attempts(
    message: str,
    retry_after: int,
    *,
    json_response: bool = False,
    auth_mode: str | None = None,
    email: str = "",
    full_name: str = "",
):
    if json_response:
        response = make_response(jsonify({"success": False, "message": message}), 429)
    elif auth_mode in {"login", "signup"}:
        response = make_response(
            render_template(
                "login.html",
                auth_mode=auth_mode,
                auth_error=message,
                login_email=email if auth_mode == "login" else "",
                signup_email=email if auth_mode == "signup" else "",
                signup_full_name=full_name if auth_mode == "signup" else "",
            ),
            429,
        )
    else:
        response = make_response(
            render_error_page(
                error_title="Too Many Attempts",
                error_message=message,
                status_code=429,
            ),
            429,
        )
    response.headers["Retry-After"] = str(retry_after)
    return response


@auth_bp.get("/login.html")
def login_page():
    return render_template("login.html")


@auth_bp.get("/forgot-password")
def forgot_password_page():
    return render_template("forgot-password.html")


@auth_bp.post("/forgot-password")
def handle_forgot_password():
    email = str(request.form.get("email") or "").strip()
    allowed, retry_after = _rate_limit(
        "forgot-password",
        email or "missing",
        count_key="PASSWORD_RESET_RATE_LIMIT_COUNT",
        window_key="PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS",
    )
    if not allowed:
        return _too_many_attempts(
            "Too many password reset requests. Please wait before trying again.",
            retry_after,
        )

    service = AuthenticationService()
    reset_request = service.create_password_reset_token(email)
    if reset_request:
        user, token = reset_request
        service.send_password_reset_email(
            user,
            url_for("auth.reset_password_page", token=token, _external=True),
        )

    # Always use the same response so this endpoint cannot enumerate accounts.
    flash(
        "If an account exists for that email, a password reset link has been sent.",
        "success",
    )
    return redirect(url_for("auth.login_page"), code=303)


@auth_bp.get("/reset-password/<token>")
def reset_password_page(token: str):
    try:
        AuthenticationService().validate_password_reset_token(token)
    except ValidationError as exc:
        return render_template("reset-password.html", invalid_message=str(exc)), 400
    return render_template("reset-password.html", token=token)


@auth_bp.post("/reset-password")
def handle_reset_password():
    token = str(request.form.get("token") or "")
    password = str(request.form.get("password") or "")
    confirmation = str(request.form.get("confirm_password") or "")
    if password != confirmation:
        flash("The passwords do not match.", "error")
        return redirect(url_for("auth.reset_password_page", token=token), code=303)

    try:
        AuthenticationService().reset_password(token, password)
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("auth.reset_password_page", token=token), code=303)

    flash("Your password was updated. You can now sign in.", "success")
    return redirect(url_for("auth.login_page"), code=303)


@auth_bp.post("/api/login")
def handle_login():
    data = request.get_json(silent=True) if request.is_json else request.form
    email = (data.get("email") or data.get("user_id") or "").strip()
    password = data.get("password") or ""
    allowed, retry_after = _rate_limit(
        "login",
        email or "missing",
        count_key="AUTH_LOGIN_RATE_LIMIT_COUNT",
        window_key="AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    )
    if not allowed:
        return _too_many_attempts(
            "Too many sign-in attempts. Please wait before trying again.",
            retry_after,
            json_response=request.is_json,
            auth_mode="login",
            email=email,
        )

    try:
        user = AuthenticationService().authenticate(email, password)
    except (AuthenticationError, ValidationError):
        message = "Invalid email or password. Please double check your credentials."
        if request.is_json:
            return jsonify({"error": message}), 401
        return render_template(
            "login.html",
            auth_mode="login",
            auth_error=message,
            login_email=email,
        ), 401

    session.clear()
    session.permanent = True
    session["user_id"] = user["user_id"]
    session["email"] = user.get("email", user["user_id"])
    session["full_name"] = user.get("full_name", "")
    session["is_admin"] = is_admin_identity(user["user_id"], user)
    session["groups"] = list(user.get("groups") or user.get("access_groups") or ())
    session["live_interview_assistance_enabled"] = bool(
        live_interview_assistance_access(user["user_id"], user)["enabled"]
    )
    session["language"] = normalize_language(
        (user.get("settings") or {}).get("language"),
        default="en",
    )
    return redirect(url_for("main.view_index"))


@auth_bp.post("/api/signup")
def handle_signup():
    data = request.get_json(silent=True) if request.is_json else request.form
    full_name = (data.get("full_name") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    signup_language = normalize_language(session.get("language"), default="en")
    allowed, retry_after = _rate_limit(
        "signup",
        email or "missing",
        count_key="AUTH_SIGNUP_RATE_LIMIT_COUNT",
        window_key="AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS",
    )
    if not allowed:
        return _too_many_attempts(
            "Too many account-creation attempts. Please wait before trying again.",
            retry_after,
            json_response=request.is_json,
            auth_mode="signup",
            email=email,
            full_name=full_name,
        )

    try:
        user = AuthenticationService().register(
            full_name,
            email,
            password,
            language=signup_language,
        )
    except ValidationError as exc:
        if request.is_json:
            return jsonify({"error": str(exc)}), 400
        return render_template(
            "login.html",
            auth_mode="signup",
            auth_error=str(exc),
            signup_email=email,
            signup_full_name=full_name,
        ), 400

    session.clear()
    session.permanent = True
    session["user_id"] = user["user_id"]
    session["email"] = user.get("email", user["user_id"])
    session["full_name"] = user.get("full_name", "")
    session["is_admin"] = is_admin_identity(user["user_id"], user)
    session["groups"] = list(user.get("groups") or user.get("access_groups") or ())
    session["live_interview_assistance_enabled"] = bool(
        live_interview_assistance_access(user["user_id"], user)["enabled"]
    )
    session["language"] = signup_language
    try:
        UsageMetricsService().record_product_event(
            "registration_completed", user["user_id"], event_id=f"signup-{user['user_id']}"
        )
    except Exception:
        current_app.logger.exception("Could not record registration analytics")
    return redirect(url_for("main.view_index"))


@auth_bp.post("/logout")
def handle_logout():
    session.clear()
    return redirect(url_for("auth.login_page"))


@auth_bp.post("/api/user")
def api_get_user():
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    password = data.get("password") or ""
    allowed, retry_after = _rate_limit(
        "desktop-login",
        user_id or "missing",
        count_key="AUTH_LOGIN_RATE_LIMIT_COUNT",
        window_key="AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    )
    if not allowed:
        return _too_many_attempts(
            "Too many sign-in attempts. Please wait before trying again.",
            retry_after,
            json_response=True,
        )

    try:
        user = AuthenticationService().authenticate(user_id, password)
    except ValidationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except AuthenticationError:
        return jsonify({"success": False, "message": "Invalid user_id or password."}), 401

    user_settings = UserService().get_settings(user["user_id"])
    live_assistance_enabled = bool(
        live_interview_assistance_access(user["user_id"], user)["enabled"]
    )
    if not live_assistance_enabled:
        user_settings.update({"aiClipboard": False, "aiSpeaker": False, "aiMicrophone": False})
    user_settings["liveInterviewAssistanceEnabled"] = live_assistance_enabled
    # Backward-compatible aliases let connected desktop clients use the same
    # preference as the browser recorder without requiring a separate setting.
    user_settings["whisperLanguage"] = user_settings.get("language", "en")
    user_settings["whisper_language"] = user_settings.get("language", "en")
    safe_user = {
        "user_id": user["user_id"],
        "email": user.get("email", user["user_id"]),
        "full_name": user.get("full_name", ""),
        "settings": user_settings,
    }

    api_token = generate_api_token(user["user_id"])
    try:
        UsageMetricsService().record_desktop_client_use(
            user["user_id"],
            event_id=api_token,
        )
    except Exception:
        # A temporary analytics failure must not prevent desktop authentication.
        current_app.logger.exception(
            "Could not record desktop client use for %s",
            user["user_id"],
        )

    return jsonify(
        {
            "success": True,
            "user": safe_user,
            "api_token": api_token,
        }
    )
