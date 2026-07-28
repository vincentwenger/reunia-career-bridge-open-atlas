def test_user_guide_route_is_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules() if not rule.build_only}
    assert "/user-guide.html" in rules


def test_user_guide_page_is_public_and_covers_current_workflow(app):
    client = app.test_client()
    response = client.get("/user-guide.html")

    assert response.status_code == 200
    assert b"User Guide" in response.data
    assert b"Use the app in three simple steps" in response.data
    assert b"Document Library" in response.data
    assert b"Meeting Materials" in response.data
    assert b"Knowledge Search" in response.data
    assert b"Live Q&amp;A" in response.data
    assert b"Ask about this meeting" in response.data
    assert b"Action Center" in response.data
    assert b"Analytics" in response.data
    assert b"All Audio Sources" in response.data


def test_user_guide_legacy_endpoint_alias_builds(app):
    with app.test_request_context():
        from flask import url_for

        assert url_for("user_guide_page") == "/user-guide.html"
