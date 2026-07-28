from __future__ import annotations

from pathlib import Path


EXPECTED_LABELS = (
    "Career Profile",
    "Application Builder",
    "Interview Preparation",
    "Mock Interview",
    "Interview Review",
    "Career Action Plan",
    "Progress",
    "Help &amp; Support",
)

EXPECTED_KEYS = (
    "career_profile",
    "application_builder",
    "interview_preparation",
    "mock_interview",
    "interview_review",
    "career_action_plan",
    "progress",
    "help_support",
)


def _signed_in_client(app, *, is_admin: bool = False):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(
            {
                "user_id": "admin@example.com" if is_admin else "member@example.com",
                "email": "admin@example.com" if is_admin else "member@example.com",
                "full_name": "Admin User" if is_admin else "Member User",
                "is_admin": is_admin,
            }
        )
    return client


def test_signed_in_navigation_uses_career_bridge_sections(app):
    response = _signed_in_client(app).get("/app")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    for label in EXPECTED_LABELS:
        assert label in page
    for key in EXPECTED_KEYS:
        assert f'data-career-section="{key}"' in page

    assert 'data-nav-category' not in page
    assert '<span>Meeting Preparation</span>' not in page
    assert '<span>Live Meeting</span>' not in page
    assert '<span>Review &amp; Follow-Up</span>' not in page
    assert "AI CAREER BRIDGE" in page


def test_administration_is_separate_and_admin_only(app):
    member_page = _signed_in_client(app).get("/app").get_data(as_text=True)
    assert 'class="admin-nav-link' not in member_page

    admin_page = _signed_in_client(app, is_admin=True).get("/app").get_data(as_text=True)
    assert 'class="admin-nav-link' in admin_page
    assert 'href="/admin/analytics"' in admin_page
    assert "Administration" in admin_page


def test_navigation_template_keeps_help_top_level_and_admin_outside_account_menu(app):
    template = (
        Path(app.template_folder) / "navbar.html"
    ).read_text(encoding="utf-8")

    help_link = template.index('data-career-section="help_support"')
    nav_auth = template.index('<div class="nav-auth">')
    admin_link = template.index('class="admin-nav-link')
    account_menu = template.index('id="accountDropdownMenu"')

    assert help_link < nav_auth
    assert admin_link < account_menu
    assert template.count("Administration") == 1
