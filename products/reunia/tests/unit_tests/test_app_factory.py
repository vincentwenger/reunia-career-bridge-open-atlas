def test_expected_routes_are_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules() if not rule.build_only}
    assert "/" in rules
    assert "/app" in rules
    assert "/index.html" in rules
    assert "/login.html" in rules
    assert "/api/user" in rules
    assert "/api/transcripts" in rules
    assert "/submit-transcript" in rules
    assert "/submit-live-qa" in rules
    assert "/help-support.html" in rules
    assert "/user-guide.html" in rules
    assert "/api/support" in rules
    assert "/meeting-recorder" in rules
    assert "/meeting-recorder.html" in rules
    assert "/api/meeting-recorder" in rules
    assert "/api/meeting-recorder/jobs/<job_id>" in rules
    assert "/api/system-settings" not in rules


def test_legacy_endpoint_aliases_build(app):
    with app.test_request_context():
        from flask import url_for

        assert url_for("view_index") == "/app"
        assert url_for("marketing_page") == "/"
        assert url_for("login_page") == "/login.html"


def test_transcript_api_rejects_unauthenticated_user(app):
    client = app.test_client()
    response = client.get('/api/transcripts')
    assert response.status_code == 401
    assert response.get_json() == {'error': 'Authentication required.'}


def test_app_environment_uses_app_env(monkeypatch):
    from meeting_assistant import _environment_name

    monkeypatch.delenv("APP_ENV", raising=False)
    assert _environment_name() == "development"

    monkeypatch.setenv("APP_ENV", "production")
    assert _environment_name() == "production"

    monkeypatch.setenv("APP_ENV", "test")
    assert _environment_name() == "testing"
