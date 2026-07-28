from flask import url_for


def _sign_in(client, user_id="test-user@example.com"):
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
        flask_session["full_name"] = "Test User"


def test_signed_out_root_renders_marketing_page(app):
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Prepare. Connect. Move forward." in response.data
    assert "Réunia helps you prepare".encode("utf-8") in response.data
    assert b"Choose where you are in the meeting workflow" not in response.data


def test_signed_out_marketing_page_does_not_advertise_unavailable_plans(app):
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Plans designed to grow with your meetings" not in response.data
    assert b'id="pricing"' not in response.data
    assert b'href="#pricing"' not in response.data


def test_signed_in_root_redirects_to_app(app):
    client = app.test_client()
    _sign_in(client)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/app")


def test_signed_out_app_redirects_to_marketing_page(app):
    client = app.test_client()

    response = client.get("/app", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_signed_in_app_renders_guided_start_homepage(app):
    client = app.test_client()
    _sign_in(client)

    response = client.get("/app")

    assert response.status_code == 200
    assert b"Ready for your next meeting?" in response.data
    assert b"Get started" in response.data
    assert b"view=materials&amp;guided=1" in response.data
    assert b"Quick access" in response.data


def test_index_html_keeps_backward_compatible_dashboard_route(app):
    client = app.test_client()

    signed_out_response = client.get("/index.html", follow_redirects=False)
    assert signed_out_response.status_code == 302
    assert signed_out_response.headers["Location"].endswith("/")

    _sign_in(client)
    signed_in_response = client.get("/index.html")
    assert signed_in_response.status_code == 200
    assert b"Ready for your next meeting?" in signed_in_response.data
    assert b"Get started" in signed_in_response.data


def test_main_route_endpoints_build_canonical_urls(app):
    with app.test_request_context():
        assert url_for("main.marketing_page") == "/"
        assert url_for("main.view_index") == "/app"
