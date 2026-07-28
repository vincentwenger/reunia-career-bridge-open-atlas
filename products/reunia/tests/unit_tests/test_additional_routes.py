
def test_main_application_pages_are_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules() if not rule.build_only}
    expected = {
        "/",
        "/meeting-review.html",
        "/action-center.html",
        "/knowledge.html",
        "/analytics.html",
        "/help-support.html",
        "/user-guide.html",
        "/meeting-recorder",
        "/api/meeting-recorder",
        "/api/actions",
        "/admin/analytics",
        "/api/analytics/track",
    }
    missing = expected - rules
    assert not missing, f"Missing expected routes: {sorted(missing)}"


def test_unknown_page_returns_not_found(app):
    response = app.test_client().get("/page-that-does-not-exist")
    assert response.status_code == 404
