from io import BytesIO


def test_support_routes_are_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules() if not rule.build_only}
    assert "/help-support.html" in rules
    assert "/api/support" in rules
    assert "/api/support/recorder-error" in rules


def test_help_support_page_is_public(app):
    client = app.test_client()
    response = client.get("/help-support.html")
    assert response.status_code == 200
    assert b"Help &amp; Support" in response.data or b"Help & Support" in response.data
    assert b"Request type" in response.data
    assert b"Feature or area" in response.data
    assert b"Live Q&amp;A" in response.data
    assert b"Action Center" in response.data
    assert b"Analytics" in response.data


def test_valid_support_request_is_stored(app):
    client = app.test_client()
    response = client.post(
        "/api/support",
        data={
            "name": "Test User",
            "email": "test@example.com",
            "topic": "technical",
            "area": "meeting-review",
            "subject": "Transcript page problem",
            "message": "The transcript page did not load after I selected a meeting.",
            "page_url": "http://localhost/meeting-review.html",
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["request_id"].startswith("SUP-")

    stored = app.extensions["support_repository"].list_all()
    assert len(stored) == 1
    assert stored[0]["topic"] == "technical"
    assert stored[0]["topic_label"] == "Technical problem"
    assert stored[0]["area"] == "meeting-review"
    assert stored[0]["area_label"] == "Meeting Review"
    assert stored[0]["email"] == "test@example.com"


def test_support_request_rejects_invalid_email(app):
    client = app.test_client()
    response = client.post(
        "/api/support",
        data={
            "name": "Test User",
            "email": "not-an-email",
            "topic": "other",
            "subject": "Question",
            "message": "Please help me with this question.",
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    assert "valid email" in response.get_json()["error"].lower()


def test_support_attachment_requires_configured_bucket(app):
    client = app.test_client()
    response = client.post(
        "/api/support",
        data={
            "name": "Test User",
            "email": "test@example.com",
            "topic": "bug",
            "subject": "Screenshot",
            "message": "I attached a screenshot of the problem.",
            "attachment": (BytesIO(b"\x89PNG\r\n\x1a\nexample"), "issue.png"),
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    assert "attachments are not enabled" in response.get_json()["error"].lower()


def test_honeypot_submission_is_rejected_and_not_stored(app):
    client = app.test_client()
    response = client.post(
        "/api/support",
        data={
            "name": "Bot",
            "email": "bot@example.com",
            "topic": "other",
            "subject": "Spam",
            "message": "Spam message",
            "website": "https://spam.example.com",
        },
    )

    assert response.status_code == 400
    assert "could not verify" in response.get_json()["error"].lower()
    assert app.extensions["support_repository"].list_all() == []


def test_authenticated_recorder_error_is_stored_as_automated_support_request(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "recorder.user@example.com"
        session["email"] = "recorder.user@example.com"
        session["full_name"] = "Recorder User"

    response = client.post(
        "/api/support/recorder-error",
        json={
            "reference_id": "recorder-ref-123",
            "stage": "uploading_segment",
            "http_status": 413,
            "recording": "01:14:37 · mic 27.17 MB",
            "occurred_at": "2026-07-21T18:16:38.039Z",
            "page_url": "https://www.reunia.app/meeting-recorder",
            "diagnostic_details": (
                "Meeting Recorder Processing Error\n"
                "Reference ID: recorder-ref-123\n"
                "Message: A recording source is too large."
            ),
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["request_id"].startswith("SUP-")

    stored = app.extensions["support_repository"].list_all()
    assert len(stored) == 1
    request_item = stored[0]
    assert request_item["source"] == "browser_recorder_error"
    assert request_item["topic"] == "technical"
    assert request_item["area"] == "recorder"
    assert request_item["name"] == "Recorder User"
    assert request_item["email"] == "recorder.user@example.com"
    assert "recorder-ref-123" in request_item["subject"]
    assert "Diagnostic details:" in request_item["message"]
    assert "A recording source is too large" in request_item["message"]


def test_recorder_error_support_endpoint_requires_authentication(app):
    response = app.test_client().post(
        "/api/support/recorder-error",
        json={"diagnostic_details": "Recorder failed."},
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication required."}


def test_recorder_error_support_endpoint_requires_diagnostics(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "recorder.user@example.com"
        session["email"] = "recorder.user@example.com"

    response = client.post(
        "/api/support/recorder-error",
        json={"reference_id": "recorder-ref-456"},
    )
    assert response.status_code == 400
    assert "diagnostic details" in response.get_json()["error"].lower()
