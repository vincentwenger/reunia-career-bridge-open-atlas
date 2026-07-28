from __future__ import annotations

from pathlib import Path

from meeting_assistant.utils.exceptions import AuthenticationError, ValidationError


def test_not_found_page_prioritizes_home_and_uses_specific_title(app):
    response = app.test_client().get("/__missing-error-review-page")

    assert response.status_code == 404
    page = response.get_data(as_text=True)
    assert "<title>Réunia - Page Not Found</title>" in page
    assert 'class="status-pill"' in page
    assert "Page Not Found" in page
    assert "Go Home" in page
    assert "Return to Login" not in page
    assert page.index("Go Home") < page.index("Go Back")
    assert response.headers["X-Request-ID"].startswith("REQ-")


def test_admin_denial_uses_403_recovery_page(app):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(
            {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "full_name": "Member User",
                "is_admin": False,
            }
        )

    response = client.get("/admin/analytics")

    assert response.status_code == 403
    page = response.get_data(as_text=True)
    assert "Administrator Access Required" in page
    assert "This account does not have access." in page
    assert "Go Home" in page


def test_expired_form_uses_400_back_recovery(app):
    app.config["CSRF_ENABLED"] = True
    client = app.test_client()
    assert client.get("/login.html").status_code == 200

    response = client.post("/forgot-password", data={"email": "member@example.com"})

    assert response.status_code == 400
    page = response.get_data(as_text=True)
    assert "Request Expired" in page
    assert "The request needs to be submitted again." in page
    assert 'class="action-btn"' in page
    assert "data-safe-back" in page


def test_large_request_uses_413_specific_recovery_and_reference(app):
    app.config["MAX_CONTENT_LENGTH"] = 100
    response = app.test_client().post(
        "/forgot-password",
        data={"email": "a" * 200},
        headers={"X-Recorder-Reference": "recording-review-123"},
    )

    assert response.status_code == 413
    page = response.get_data(as_text=True)
    assert "Recording Too Large" in page
    assert "The recording is larger than allowed." in page
    assert "Return to Login" in page
    assert "recording-review-123" in page


def test_rate_limited_password_reset_uses_429_recovery_page(app, monkeypatch):
    from meeting_assistant.blueprints.auth import routes as auth_routes

    app.config["PASSWORD_RESET_RATE_LIMIT_COUNT"] = 1
    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "create_password_reset_token",
        lambda self, email: None,
    )
    client = app.test_client()

    first = client.post("/forgot-password", data={"email": "member@example.com"})
    second = client.post("/forgot-password", data={"email": "member@example.com"})

    assert first.status_code == 303
    assert second.status_code == 429
    page = second.get_data(as_text=True)
    assert "Too Many Attempts" in page
    assert "Please wait before trying again." in page


def test_unexpected_error_shows_correlated_reference(app):
    def fail_for_test():
        raise RuntimeError("private implementation detail")

    app.add_url_rule("/__test-system-error", "test_system_error", fail_for_test)
    response = app.test_client().get(
        "/__test-system-error",
        headers={"X-Request-ID": "request-review-500"},
    )

    assert response.status_code == 500
    page = response.get_data(as_text=True)
    assert "System Error" in page
    assert "private implementation detail" not in page
    assert "request-review-500" in page
    assert response.headers["X-Request-ID"] == "request-review-500"
    assert "Open Help &amp; Support" in page


def test_login_and_signup_errors_remain_inline(app, monkeypatch):
    from meeting_assistant.blueprints.auth import routes as auth_routes

    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "authenticate",
        lambda self, email, password: (_ for _ in ()).throw(AuthenticationError("invalid")),
    )
    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "register",
        lambda self, full_name, email, password, language=None: (
            _ for _ in ()
        ).throw(ValidationError("That email is already registered.")),
    )
    client = app.test_client()

    login_response = client.post(
        "/api/login",
        data={"email": "member@example.com", "password": "wrong"},
    )
    signup_response = client.post(
        "/api/signup",
        data={
            "full_name": "Member User",
            "email": "member@example.com",
            "password": "not-returned",
        },
    )

    assert login_response.status_code == 401
    login_page = login_response.get_data(as_text=True)
    assert 'id="auth-error"' in login_page
    assert 'value="member@example.com"' in login_page
    assert "error-shell" not in login_page

    assert signup_response.status_code == 400
    signup_page = signup_response.get_data(as_text=True)
    assert 'data-auth-mode="signup"' in signup_page
    assert 'value="Member User"' in signup_page
    assert 'value="member@example.com"' in signup_page
    assert "not-returned" not in signup_page
    assert "That email is already registered." in signup_page


def test_json_login_error_remains_json(app, monkeypatch):
    from meeting_assistant.blueprints.auth import routes as auth_routes

    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "authenticate",
        lambda self, email, password: (_ for _ in ()).throw(AuthenticationError("invalid")),
    )
    response = app.test_client().post(
        "/api/login",
        json={"email": "member@example.com", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.is_json
    assert "Invalid email or password" in response.get_json()["error"]


def test_error_script_restricts_back_navigation_to_same_origin(app):
    project_root = Path(app.root_path).parent
    script = (project_root / "static/js/pages/error.js").read_text(encoding="utf-8")

    assert "previousUrl.origin !== currentUrl.origin" in script
    assert "history.back" not in script
