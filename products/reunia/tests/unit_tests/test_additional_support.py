
def _valid_support_data():
    return {
        "name": "Test User",
        "email": "test@example.com",
        "topic": "question",
        "area": "meeting-review",
        "subject": "How do I use Meeting Review?",
        "message": "Please explain how I can find the scorecard for a saved meeting.",
    }


def test_support_request_requires_subject(app):
    data = _valid_support_data()
    data["subject"] = ""
    response = app.test_client().post(
        "/api/support",
        data=data,
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 400
    assert "subject" in response.get_json()["error"].lower()


def test_support_request_requires_message(app):
    data = _valid_support_data()
    data["message"] = ""
    response = app.test_client().post(
        "/api/support",
        data=data,
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 400
    assert "message" in response.get_json()["error"].lower()


def test_support_request_returns_json_when_requested(app):
    response = app.test_client().post(
        "/api/support",
        data=_valid_support_data(),
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 201
    assert response.is_json
    assert response.get_json()["success"] is True


def test_non_test_support_storage_defaults_to_dynamodb(monkeypatch):
    monkeypatch.delenv("SUPPORT_STORAGE_BACKEND", raising=False)
    from meeting_assistant.config import BaseConfig

    assert BaseConfig.SUPPORT_STORAGE_BACKEND == "dynamodb"


def test_support_request_rejects_invalid_feature_area(app):
    data = _valid_support_data()
    data["area"] = "not-a-real-area"
    response = app.test_client().post(
        "/api/support",
        data=data,
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 400
    assert "feature or area" in response.get_json()["error"].lower()


def test_legacy_support_request_without_area_remains_supported(app):
    data = _valid_support_data()
    data["topic"] = "using-app"
    data.pop("area")
    response = app.test_client().post(
        "/api/support",
        data=data,
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 201
    stored = app.extensions["support_repository"].list_all()
    assert stored[0]["area"] == "other"
