from __future__ import annotations

import os
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

import meeting_assistant
from meeting_assistant.blueprints.auth import routes as auth_routes
from meeting_assistant.services.authentication_service import AuthenticationService
from meeting_assistant.utils.exceptions import AuthenticationError, ValidationError


def _support_data() -> dict[str, str]:
    return {
        "name": "Review User",
        "email": "review@example.com",
        "topic": "technical",
        "area": "meeting-review",
        "subject": "Review request",
        "message": "Please help investigate this issue.",
    }


def test_support_form_has_safe_native_post_fallback(app):
    response = app.test_client().get("/help-support.html")

    assert response.status_code == 200
    assert b'action="/api/support"' in response.data
    assert b'method="post"' in response.data
    assert b'enctype="multipart/form-data"' in response.data
    assert b'name="csrf_token"' in response.data


def test_native_support_submission_redirects_and_stores(app):
    response = app.test_client().post(
        "/api/support",
        data=_support_data(),
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 303
    assert response.headers["Location"].endswith("/help-support.html#contact-support")
    assert len(app.extensions["support_repository"].list_all()) == 1


def test_support_uses_proxy_resolved_remote_address_not_raw_forwarded_header(app):
    response = app.test_client().post(
        "/api/support",
        data=_support_data(),
        headers={
            "Accept": "application/json",
            "X-Forwarded-For": "198.51.100.99",
        },
        environ_overrides={"REMOTE_ADDR": "203.0.113.8"},
    )

    assert response.status_code == 201
    stored = app.extensions["support_repository"].list_all()[0]
    assert stored["remote_address"] == "203.0.113.8"


def test_support_rate_limit_cannot_be_bypassed_with_forwarded_header(app):
    app.config["SUPPORT_RATE_LIMIT_COUNT"] = 1
    client = app.test_client()

    first = client.post(
        "/api/support",
        data=_support_data(),
        headers={"Accept": "application/json", "X-Forwarded-For": "198.51.100.1"},
        environ_overrides={"REMOTE_ADDR": "203.0.113.9"},
    )
    second = client.post(
        "/api/support",
        data=_support_data(),
        headers={"Accept": "application/json", "X-Forwarded-For": "198.51.100.2"},
        environ_overrides={"REMOTE_ADDR": "203.0.113.9"},
    )

    assert first.status_code == 201
    assert second.status_code == 429


def test_login_rate_limit_has_ip_wide_bucket(app, monkeypatch):
    app.config["AUTH_LOGIN_RATE_LIMIT_COUNT"] = 2
    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "authenticate",
        lambda self, email, password: (_ for _ in ()).throw(
            AuthenticationError("invalid")
        ),
    )
    client = app.test_client()

    statuses = [
        client.post(
            "/api/login",
            data={"email": f"person{index}@example.com", "password": "wrong"},
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        ).status_code
        for index in range(3)
    ]

    assert statuses == [401, 401, 429]


def test_login_rate_limit_has_identity_bucket_across_addresses(app, monkeypatch):
    app.config["AUTH_LOGIN_RATE_LIMIT_COUNT"] = 2
    monkeypatch.setattr(
        auth_routes.AuthenticationService,
        "authenticate",
        lambda self, email, password: (_ for _ in ()).throw(
            AuthenticationError("invalid")
        ),
    )
    client = app.test_client()

    statuses = [
        client.post(
            "/api/login",
            data={"email": "target@example.com", "password": "wrong"},
            environ_overrides={"REMOTE_ADDR": f"203.0.113.{20 + index}"},
        ).status_code
        for index in range(3)
    ]

    assert statuses == [401, 401, 429]


class _ResetRepository:
    def __init__(self):
        self.users = {
            "member@example.com": {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "password_hash": generate_password_hash("old-password"),
            }
        }

    def get_by_id(self, user_id):
        return self.users.get(user_id)

    def update_fields(self, user_id, fields):
        self.users[user_id].update(fields)
        return fields


def test_password_reset_token_is_invalidated_after_password_change(app):
    repository = _ResetRepository()
    service = AuthenticationService(repository)

    with app.app_context():
        user, token = service.create_password_reset_token("MEMBER@example.com")
        assert user["user_id"] == "member@example.com"
        assert service.validate_password_reset_token(token)["user_id"] == user["user_id"]

        service.reset_password(token, "new-secure-password")
        assert check_password_hash(
            repository.users[user["user_id"]]["password_hash"],
            "new-secure-password",
        )
        with pytest.raises(ValidationError, match="no longer valid"):
            service.validate_password_reset_token(token)


def test_password_reset_request_does_not_reveal_account_existence(app, monkeypatch):
    class _AuthenticationService:
        def create_password_reset_token(self, email):
            return None

        def send_password_reset_email(self, user, reset_url):
            raise AssertionError("No email should be sent for an unknown account")

    monkeypatch.setattr(auth_routes, "AuthenticationService", _AuthenticationService)
    response = app.test_client().post(
        "/forgot-password",
        data={"email": "unknown@example.com"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"If an account exists" in response.data
    assert b"unknown" not in response.data.lower()


def test_static_fingerprint_does_not_read_large_asset_contents(tmp_path, monkeypatch):
    (tmp_path / "installer.exe").write_bytes(b"large-enough-for-this-test")
    monkeypatch.setattr(meeting_assistant, "_STATIC_FINGERPRINT_CONTENT_LIMIT", 0)

    def _unexpected_open(self, *args, **kwargs):
        raise AssertionError("Large static assets must not be opened during startup")

    monkeypatch.setattr(Path, "open", _unexpected_open)
    assert len(meeting_assistant._static_asset_fingerprint(tmp_path)) == 16


def test_static_fingerprint_content_hashes_normal_web_assets(tmp_path):
    asset = tmp_path / "app.js"
    fixed_time = 1_700_000_000_000_000_000
    asset.write_bytes(b"one")
    os.utime(asset, ns=(fixed_time, fixed_time))
    first = meeting_assistant._static_asset_fingerprint(tmp_path)

    asset.write_bytes(b"two")
    os.utime(asset, ns=(fixed_time, fixed_time))
    second = meeting_assistant._static_asset_fingerprint(tmp_path)

    assert first != second
