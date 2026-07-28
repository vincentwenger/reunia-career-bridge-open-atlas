from pathlib import Path


def _signed_in_profile_client(app, monkeypatch):
    from meeting_assistant.blueprints.users import routes as user_routes

    class _UserService:
        def get_user(self, user_id):
            assert user_id == "member@example.com"
            return {
                "email": "member@example.com",
                "full_name": "Member User",
                "job_title": "Project Manager",
                "dob": "1980-01-01",
                "phone_number": "+1 555 0100",
                "address": "123 Example Street",
            }

    monkeypatch.setattr(user_routes, "UserService", _UserService)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(
            {
                "user_id": "member@example.com",
                "email": "member@example.com",
                "full_name": "Member User",
                "language": "en",
            }
        )
    return client


def test_profile_page_collects_only_workspace_identity(app, monkeypatch):
    client = _signed_in_profile_client(app, monkeypatch)

    response = client.get("/profile.html")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="fullName"' in page
    assert 'autocomplete="name"' in page
    assert 'id="jobTitle"' in page
    assert 'autocomplete="organization-title"' in page
    assert page.count('maxlength="120"') == 2
    assert 'id="emailAddressHelp"' in page
    assert 'aria-describedby="emailAddressHelp"' in page
    assert 'id="dob"' not in page
    assert 'id="phone_number"' not in page
    assert 'id="address"' not in page
    assert 'id="profile-toast"' in page
    assert page.index('id="profile-toast"') > page.index('class="form-actions"')


def test_profile_assets_cover_dirty_state_and_accessible_feedback(app):
    project_root = Path(app.root_path).parent
    script = (project_root / "static/js/pages/profile.js").read_text(encoding="utf-8")
    stylesheet = (project_root / "static/css/pages/profile.css").read_text(encoding="utf-8")

    assert "Unsaved changes" in script
    assert "navbarProfileName" in script
    assert "role', isError ? 'alert' : 'status'" in script
    assert "#0f766e" in stylesheet
    assert ".profile-card {" in stylesheet
    assert "order: 1" in stylesheet
