"""Production entry point for the merged Réunia Career Bridge application.

The container serves both existing products as one website:

* Réunia is the authenticated Career Bridge shell at ``/`` and ``/app``.
* Resume Taylor is the Application Builder module at ``/applications/``.

Both Flask applications share the same secret key and session cookie, so a user
who signs in through Réunia can open the Application Builder without signing in
again. No reverse proxy or secondary public mount prefix is required.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, redirect
from werkzeug.middleware.dispatcher import DispatcherMiddleware

ROOT = Path(__file__).resolve().parent
REUNIA_ROOT = ROOT / "products" / "reunia"
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"

# The imported products use ``meeting_assistant`` and ``resume_tailor`` as
# top-level packages. Add their product roots before importing the factories.
for product_root in (REUNIA_ROOT, RESUME_TAYLOR_ROOT):
    product_path = str(product_root)
    if product_path not in sys.path:
        sys.path.insert(0, product_path)

from meeting_assistant import create_app as create_reunia_app  # noqa: E402
from products.resume_taylor.app import create_app as create_builder_app  # noqa: E402


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _application_database_path() -> str:
    configured = (
        os.environ.get("CAREER_BRIDGE_APPLICATIONS_DB")
        or os.environ.get("APPLICATIONS_DB_PATH")
        or ""
    ).strip()
    if configured:
        database_path = Path(configured)
    else:
        database_path = ROOT / "instance" / "career_bridge_applications.sqlite3"

    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)
    return str(database_path)


def create_application(config_name: str | None = None) -> Flask:
    """Create the merged Career Bridge WSGI application."""

    reunia_app = create_reunia_app(config_name)

    # Use the exact same session-signing configuration as the Réunia shell.
    builder_app = create_builder_app(
        {
            "SECRET_KEY": reunia_app.config["SECRET_KEY"],
            "SESSION_COOKIE_NAME": reunia_app.config.get(
                "SESSION_COOKIE_NAME", "session"
            ),
            "SESSION_COOKIE_PATH": "/",
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": reunia_app.config.get(
                "SESSION_COOKIE_SAMESITE", "Lax"
            ),
            "SESSION_COOKIE_SECURE": reunia_app.config.get(
                "SESSION_COOKIE_SECURE",
                _env_bool("FLASK_COOKIE_SECURE", default=False),
            ),
            "CAREER_BRIDGE_REQUIRE_AUTH": True,
            "CAREER_BRIDGE_LOGIN_URL": "/login.html",
            "CAREER_BRIDGE_HOME_URL": "/app",
            "APPLICATIONS_DB_PATH": _application_database_path(),
        }
    )

    @reunia_app.get("/health")
    def health_check():
        return jsonify(status="ok", services=["reunia", "application-builder"])

    # Canonicalize the mounted module root before DispatcherMiddleware handles
    # requests below /applications/.
    @reunia_app.get("/applications")
    def applications_trailing_slash_redirect():
        return redirect("/applications/", code=308)

    # DispatcherMiddleware adjusts SCRIPT_NAME for the mounted Flask app. Its
    # url_for() calls therefore generate /applications/... links automatically.
    reunia_wsgi = reunia_app.wsgi_app
    reunia_app.wsgi_app = DispatcherMiddleware(
        reunia_wsgi,
        {"/applications": builder_app},
    )

    # Keep references available for tests and operational inspection.
    reunia_app.extensions["career_bridge_builder_app"] = builder_app
    return reunia_app


app = create_application()
application = app


if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", "8000")))
    app.run(host=host, port=port, debug=_env_bool("FLASK_DEBUG", default=False))
